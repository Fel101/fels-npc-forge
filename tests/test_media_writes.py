"""Check that new PNG and GLB writes pass through the media module."""
import ast
import collections
from pathlib import Path

# Buffered Pillow encoders for intermediate ComfyUI/Blender inputs and textures. Their delivered files use media.label.
INTERMEDIATE_PNG = {
    "anim.py": ("front_reference", "_puppet"), "check3d.py": ("board",), "engines.py": ("_resized",),
    "eyes.py": ("locate",), "eyesoft.py": ("_png",), "glasses.py": ("_gradient_png",), "glassesoff.py": ("_png",),
    "head3d.py": ("png_bytes", "_mask"), "headdetail.py": ("crop", "_widen"),
    "headgraft.py": ("blend_colour", "fill_backing_colour"), "headpaint.py": ("_png", "paint"),
    "keying.py": ("remove_background", "grey_png"), "paint3d.py": ("_png", "paint"), "posekeys.py": ("detect",),
    "sheet.py": ("_png",), "three_d.py": ("prepare_reference", "prepare_head_reference", "prepare_portrait_head_reference", "cut_out"),
    "underwear.py": ("dress",), "unwrap.py": ("unwrap",), "voice/character_rig.py": ("_decode_png",),
}

# Persistent writes whose data is labelled earlier, immutable inputs, and local subprocess scratch files.
EXISTING_WRITES = {
    ("server.py", "upload_photo", "write_asset"): 1,  # service.clean_photo
    ("media.py", "png", "save"): 1,
    ("creaturemaker.py", "run_concept", "write_file"): 1,
    ("creaturemaker.py", "run_3d", "write_file"): 1,
    ("creatures.py", "choose_concept", "write_file"): 1,
    ("itemmaker.py", "_shape", "write_asset"): 1,
    ("itemmaker.py", "_finish", "write_asset"): 1,
    ("itemstore.py", "build_recipe", "write_asset"): 1,  # procmesh's GLB writer
    ("jobs.py", "use_uploaded_model", "write_asset"): 1,  # user's exact input
    ("jobs.py", "_import_model", "write_asset"): 1,  # exact input or labelled clean-up
    ("jobs.py", "_run_step", "write_asset"): 3,
    ("bodymaker.py", "_dress", "write_bytes"): 2,
    ("bodymaker.py", "_copy", "write_bytes"): 1,
    ("check3d.py", "render", "write_bytes"): 1,
    ("dressing.py", "fit_process", "write_bytes"): 1,
    ("hands3d.py", "find", "write_bytes"): 1,
    ("rig3d.py", "run", "write_bytes"): 1,
}


class MediaWrites(ast.NodeVisitor):
    def __init__(self, file):
        self.file = file
        self.function = "<module>"
        self.calls = collections.Counter()

    def visit_FunctionDef(self, node):
        outer = self.function
        self.function = node.name
        self.generic_visit(node)
        self.function = outer

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"save", "write_asset", "write_file", "write_media", "write_bytes", "writestr"}:
            literals = (n.value.lower() for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str))
            is_media = any(value in ("png", "glb") or value.endswith((".png", ".glb")) for value in literals)
            if is_media and not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "media"
                                    and n.func.attr in ("label", "png") for n in ast.walk(node)):
                self.calls[(self.file, self.function, node.func.attr)] += 1
        self.generic_visit(node)


def test_new_png_and_glb_savers_use_the_shared_label_writer():
    root = Path(__file__).resolve().parents[1]
    allowed = collections.Counter(EXISTING_WRITES)
    allowed.update((file, function, "save") for file, functions in INTERMEDIATE_PNG.items() for function in functions)
    seen = collections.Counter()
    for path in [root / "server.py", *(root / "npcgen").rglob("*.py")]:
        name = path.relative_to(root).as_posix().removeprefix("npcgen/")
        scan = MediaWrites(name)
        scan.visit(ast.parse(path.read_text(encoding="utf-8")))
        seen.update(scan.calls)
    assert not seen - allowed, f"Use media.png or media.label for new PNG and GLB writers: {seen - allowed}"
