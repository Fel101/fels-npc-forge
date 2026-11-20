"""Bring a 3D model made elsewhere into a character: look inside the GLB, close its holes and bring it down to a sensible size
in ComfyUI, and store it as the character's model (the clean-up and paint step then works on it like on any other).

The model has to be a file ComfyUI can already see: one of its own folders (a GLB another workflow saved in `output`, or one
dropped into `input` or `input/3d`, where ComfyUI's Load 3D node looks). Nothing is copied to NPC Forge to be sent on again,
because ComfyUI refuses uploads over 100 MB by default and a raw shape from Trellis2 is 100-400 MB.

Measured on Airi (2026-10-01), raw Trellis2 shapes made in ComfyUI: 7.5 million faces in 232 s; its mesh clean-up: 25 s to a
closed 695k-face model; painted from the four turnaround pictures it is the best-looking model of everything tried (see
docs/model-import.md).
"""
import json
import pathlib
import re
import struct

from .comfy import ComfyError

GLB_MAGIC = 0x46546C67                 # "glTF"
JSON_CHUNK = 0x4E4F534A                # "JSON"
HEAD_BYTES = 1 << 20                   # how much of the start of a file is read to look inside it (its description comes first)
MAX_DESCRIPTION = 64 << 20             # a description bigger than this is not a model of one character
CLEAN_ABOVE_FACES = 1_000_000          # an untextured model denser than this is a raw generator shape: closed and simplified by default
UPLOAD_LIMIT = 99 << 20                # ComfyUI's default upload limit is 100 MB (--max-upload-size)
MAX_SOURCES = 200
FOLDERS = ("output", "input", "temp")
COMPRESSED = {"KHR_draco_mesh_compression": "Draco", "EXT_meshopt_compression": "meshopt"}
# the glTF extensions a file may REQUIRE for ComfyUI's loader (comfy_extras/mesh3d/fileio/gltf_read.py) to read it; any other makes it refuse
SUPPORTED_REQUIRED = {"EXT_mesh_gpu_instancing", "EXT_texture_webp", "KHR_materials_emissive_strength", "KHR_materials_unlit"}


class NotFound(ValueError):
    """ComfyUI has no such file in that folder (as opposed to a file that is there but cannot be used)."""


class Incomplete(ValueError):
    """The first bytes of the file do not hold its whole description; `needed` bytes do."""

    def __init__(self, needed):
        super().__init__("The start of the file is too short to read its description")
        self.needed = needed


def inspect(head):
    """What a GLB holds, read from the first bytes of the file (its header and its JSON description, no geometry):
    {faces, vertices, meshes, extent, textured, skinned, animated, bytes, generator, warnings}.

    Raises ValueError, with a sentence the user can act on, for a file that cannot be used, and Incomplete when `head` is shorter
    than the description."""
    if len(head) < 20:
        raise ValueError("That is not a GLB file: it is too short")
    magic, version, total = struct.unpack_from("<III", head, 0)
    if magic != GLB_MAGIC:
        raise ValueError("That is not a GLB (binary glTF) file: export the model as .glb")
    if version != 2:
        raise ValueError(f"glTF version {version} is not supported: export the model as glTF 2.0")
    size, kind = struct.unpack_from("<II", head, 12)
    if kind != JSON_CHUNK:
        raise ValueError("That GLB does not start with its description: it is damaged")
    if size > MAX_DESCRIPTION:
        raise ValueError("That GLB's description is unreasonably large: it is not one character's model")
    if len(head) < 20 + size:
        raise Incomplete(20 + size)
    try:
        doc = json.loads(head[20:20 + size].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("That GLB's description cannot be read: it is damaged") from None
    if not isinstance(doc, dict):
        raise ValueError("That GLB's description cannot be read: it is damaged")

    unsupported = sorted(set(doc.get("extensionsRequired") or []) - SUPPORTED_REQUIRED)
    if unsupported:
        packed = [COMPRESSED[name] for name in unsupported if name in COMPRESSED]
        if packed:
            raise ValueError(f"That GLB is {packed[0]}-compressed and ComfyUI cannot read it: export it without compression")
        raise ValueError(f"That GLB requires glTF extensions ComfyUI cannot read ({', '.join(unsupported)}): export it again without them")
    accessors = doc.get("accessors") or []
    faces = vertices = meshes = 0
    low, high, skipped = [None] * 3, [None] * 3, 0
    try:
        for mesh in doc.get("meshes") or []:
            meshes += 1
            for prim in mesh.get("primitives") or []:
                if prim.get("mode", 4) != 4:                     # points, lines, strips and fans are not a model's surface
                    skipped += 1
                    continue
                position = accessors[prim["attributes"]["POSITION"]]
                vertices += int(position["count"])
                faces += (int(accessors[prim["indices"]]["count"]) if "indices" in prim else int(position["count"])) // 3
                if position.get("min") and position.get("max"):
                    low = [a if b is None else min(a, b) for a, b in zip(position["min"], low)]
                    high = [a if b is None else max(a, b) for a, b in zip(position["max"], high)]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("That GLB's mesh description is damaged") from None
    if not faces:
        raise ValueError("That GLB has no triangle mesh in it")

    extent = [round(float(h) - float(l), 4) for l, h in zip(low, high)] if None not in low else None
    warnings = []
    if skipped:
        warnings.append(f"{skipped} part(s) that are not triangle surfaces (lines or points) are left out")
    if extent and extent[1] < 0.8 * max(extent):
        warnings.append("the model does not stand along the Y axis (glTF's up): it will not line up with the turnaround "
                        "pictures when painted. Export it standing up, facing +Z")
    if doc.get("skins") or doc.get("animations"):
        warnings.append("its skeleton and animations are not kept: NPC Forge makes its own")
    textured = any((m.get("pbrMetallicRoughness") or {}).get("baseColorTexture") for m in doc.get("materials") or [])
    return {"faces": faces, "vertices": vertices, "meshes": meshes, "extent": extent, "textured": textured,
            "skinned": bool(doc.get("skins")), "animated": bool(doc.get("animations")), "bytes": int(total),
            "generator": str((doc.get("asset") or {}).get("generator") or ""), "warnings": warnings}


def default_clean(info):
    """Whether the mesh is closed and simplified in ComfyUI unless the user says otherwise: yes for a dense shape with no colour
    (a raw generator output: holes, voxel steps, millions of faces), no for a model that has its own texture (cleaning it would
    drop the texture) or is already a sensible size."""
    return not info["textured"] and info["faces"] > CLEAN_ABOVE_FACES


def locate(model_file):
    """Split a Load 3D value into (ComfyUI folder, subfolder, file name). Only a .glb inside ComfyUI's own folders is accepted:
    nothing absolute, nothing that climbs out. `name.glb [output]`, `name.glb [temp]`, `name.glb [input]` or `3d/name.glb`."""
    if not isinstance(model_file, str) or not model_file.strip():
        raise ValueError("Choose a GLB file to import")
    text, folder = model_file.strip(), "input"
    for name in FOLDERS:
        if text.endswith(f" [{name}]"):
            text, folder = text[:-len(name) - 3], name
            break
    path = text.replace("\\", "/")
    parts = path.split("/")
    if path.startswith("/") or ":" in path or len(parts) > 4 or any(part in ("", ".", "..") for part in parts):
        raise ValueError("That is not a file inside one of ComfyUI's folders")
    if not path.lower().endswith(".glb"):
        raise ValueError("Only .glb files can be imported")
    return folder, "/".join(parts[:-1]), parts[-1]


def canonical(model_file):
    """The Load 3D value of a file, always with its folder in brackets."""
    folder, sub, name = locate(model_file)
    return f"{sub + '/' if sub else ''}{name} [{folder}]"


def describe(comfy, model_file):
    """inspect() of a file in one of ComfyUI's folders, from its first bytes only. ValueError for a file that cannot be used."""
    folder, sub, name = locate(model_file)
    need = HEAD_BYTES
    for _ in range(2):                                            # a description longer than the first read: once more, for all of it
        try:
            head = comfy.fetch_file(name, sub, folder, byte_range=(0, need - 1))
        except ComfyError as e:
            raise NotFound(f"ComfyUI could not give {name} from its {folder} folder ({e})") from None
        try:
            return inspect(head)
        except Incomplete as e:
            need = e.needed
    raise ValueError("That GLB is shorter than its own header says: it is damaged")


def has_folder(text):
    """Whether a typed name says which of ComfyUI's folders it is in (`name.glb [output]`)."""
    return isinstance(text, str) and any(text.strip().endswith(f" [{name}]") for name in FOLDERS)


def resolve(comfy, text):
    """(Load 3D value, inspect() result) of a file the user named. A name with its folder in brackets is looked for there; one
    without is looked for in ComfyUI's output folder first (where workflows save), then its input folder. ValueError says what
    is wrong: no such file, or a file that cannot be used."""
    if has_folder(text):
        model_file = canonical(text)
        return model_file, describe(comfy, model_file)
    for folder in ("output", "input"):
        model_file = canonical(f"{text.strip()} [{folder}]")        # also checks that it is a name inside ComfyUI's folders
        try:
            return model_file, describe(comfy, model_file)
        except NotFound:
            continue
    name = locate(f"{text.strip()} [output]")[2]
    raise NotFound(f"ComfyUI has no {name} in its output or input folder. If it is in a sub-folder, name that too: 3D/{name} [output]")


def list_sources(comfy):
    """The GLB files ComfyUI can see, newest first: [{model_file, name, folder}] from its output and input folders (the top of
    each, as ComfyUI lists them) and `input/3d`, where its Load 3D node looks."""
    out, seen = [], set()

    def add(model_file):
        if model_file not in seen and len(out) < MAX_SOURCES:
            try:
                folder, sub, name = locate(model_file)
            except ValueError:
                return
            seen.add(model_file)
            out.append({"model_file": model_file, "name": f"{sub}/{name}" if sub else name, "folder": folder})

    for folder in ("output", "input"):
        for name in comfy.list_files(folder):
            add(name)
    try:
        node = comfy.get_json("/object_info/Load3DAdvanced", timeout=10)["Load3DAdvanced"]
        options = node["input"]["required"]["model_file"]
        options = options[1].get("options") if len(options) > 1 and isinstance(options[1], dict) and "options" in options[1] else options[0]
        for name in options if isinstance(options, list) else []:
            if isinstance(name, str) and name.lower().endswith(".glb"):
                add(f"{name} [input]")
    except (ComfyError, KeyError, IndexError, TypeError, ValueError):
        pass
    return out


def upload(comfy, path):
    """A GLB on this computer, put into ComfyUI's temp folder so it can be imported; returns its Load 3D value. ComfyUI refuses
    uploads over 100 MB by default, and a raw generator shape is usually bigger: then it has to be copied into ComfyUI's
    input\\3d folder (or output) by hand."""
    path = pathlib.Path(path)
    if not path.is_file() or path.suffix.lower() != ".glb":
        raise ValueError(f"{path} is not a .glb file")
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(HEAD_BYTES)
    try:
        inspect(head)
    except Incomplete:
        pass                                                      # a long description: the import step reads all of it
    if size > UPLOAD_LIMIT:
        raise ValueError(f"{path.name} is {size / (1 << 20):.0f} MB and ComfyUI accepts uploads of up to 100 MB by default. Copy it "
                         "into ComfyUI's input\\3d folder (or output) and import it from there")
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", path.stem)[:60] or "model"
    return canonical(comfy.upload_image(path.read_bytes(), f"{stem}.glb", image_type="temp"))


def prepare(comfy, asset):
    """Look inside the file an import request names and settle what depends on it: `clean` becomes true or false (an "auto" is
    decided by default_clean), and the request gets `faces` and `textured`, the colour the imported model will have (cleaning
    it drops its texture) so the plan knows whether it must be painted. Raises ValueError for a file that cannot be imported."""
    info = describe(comfy, asset["model_file"])
    clean = default_clean(info) if asset.get("clean", "auto") == "auto" else bool(asset["clean"])
    return {**asset, "clean": clean, "faces": info["faces"], "textured": bool(info["textured"] and not clean),
            "warnings": info["warnings"]}
