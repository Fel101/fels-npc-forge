"""Take parts out of a GLB: nodes by name, and everything only they used.

A designed body that was built with its clothes on (npcgen/bodymaker.py) carries each garment as a node, a mesh, a material, a texture, an image and some buffer views of its
own. The Items tab's garment designer (npcgen/dressing.py) puts a new shirt on such a body in place of the one it wears, so the old one has to come out cleanly: not hidden,
not left in the file as dead weight, and nothing else touched. `remove_nodes` drops the nodes, then whatever no remaining node reaches (meshes, materials, textures, images,
samplers, accessors, buffer views), numbers what is left again and lays the binary chunk out without the removed bytes. Everything that stays keeps its order and its values, so a
rigged character keeps its skeleton and its clips (animations and skins are re-pointed, never rewritten).
"""
import copy

from .paint3d import Glb


def _texture_refs(value):
    """Every texture reference {"index": n} inside a material, wherever it sits (the core slots and the extensions' own, which are all called ...Texture)."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if key.endswith("Texture") and isinstance(inner, dict) and isinstance(inner.get("index"), int):
                yield inner
            else:
                yield from _texture_refs(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _texture_refs(inner)


def _kept(count, used):
    """{old index: new index} for the indexes in `used`, in their old order."""
    return {old: new for new, old in enumerate(i for i in range(count) if i in used)}


def _accessors_of(primitive):
    ids = set(primitive.get("attributes", {}).values())
    if "indices" in primitive:
        ids.add(primitive["indices"])
    for target in primitive.get("targets", []):
        ids |= set(target.values())
    return ids


def remove_nodes(data, names):
    """(GLB bytes without the nodes called any of `names`, [the names removed]). A node is taken with its children; one that is a joint of a skin or that an animation moves is
    refused (ValueError), because the skeleton would break. Meshes, materials, textures, images, samplers, accessors and buffer views that nothing else reaches go with them.
    No node with those names: the same bytes back."""
    glb = Glb(data)
    doc = glb.json
    nodes = doc.get("nodes", [])
    wanted = set(names)
    gone = {i for i, node in enumerate(nodes) if node.get("name") in wanted}
    if not gone:
        return data, []
    stack = list(gone)
    while stack:                                                           # (a node takes its children with it)
        for child in nodes[stack.pop()].get("children", []):
            if child not in gone:
                gone.add(child)
                stack.append(child)
    held = {j for skin in doc.get("skins", []) for j in skin.get("joints", [])} | {skin["skeleton"] for skin in doc.get("skins", []) if "skeleton" in skin}
    held |= {c["target"]["node"] for animation in doc.get("animations", []) for c in animation.get("channels", []) if "node" in c.get("target", {})}
    if gone & held:
        raise ValueError(f"{nodes[min(gone & held)].get('name')!r} is part of the skeleton and cannot be taken out")
    if len(doc.get("buffers", [])) != 1:
        raise ValueError("Only a GLB with one binary chunk can have parts taken out")
    removed = [nodes[i].get("name") for i in sorted(gone)]
    doc = copy.deepcopy(doc)
    nodes = doc["nodes"]
    # ---- nodes: the ones that stay, numbered again --------------------------------------------------------------------------------------------------
    node_map = _kept(len(nodes), set(range(len(nodes))) - gone)
    for scene in doc.get("scenes", []):
        scene["nodes"] = [node_map[i] for i in scene.get("nodes", []) if i in node_map]
    for node in nodes:
        if "children" in node:
            node["children"] = [node_map[i] for i in node["children"] if i in node_map]
            if not node["children"]:
                del node["children"]
    for skin in doc.get("skins", []):
        skin["joints"] = [node_map[j] for j in skin["joints"]]
        if "skeleton" in skin:
            skin["skeleton"] = node_map[skin["skeleton"]]
    for animation in doc.get("animations", []):
        for channel in animation.get("channels", []):
            if "node" in channel.get("target", {}):
                channel["target"]["node"] = node_map[channel["target"]["node"]]
    doc["nodes"] = nodes = [nodes[i] for i in sorted(node_map)]
    # ---- what the remaining nodes reach, down to the buffer views ---------------------------------------------------------------------------------------
    meshes = {node["mesh"] for node in nodes if "mesh" in node}
    primitives = [p for i in meshes for p in doc["meshes"][i].get("primitives", [])]
    materials = {p["material"] for p in primitives if "material" in p}
    textures = {ref["index"] for i in materials for ref in _texture_refs(doc["materials"][i])}
    images = {doc["textures"][i]["source"] for i in textures if "source" in doc["textures"][i]}
    for i in textures:
        for extension in (doc["textures"][i].get("extensions") or {}).values():
            if isinstance(extension, dict) and "source" in extension:
                images.add(extension["source"])
    samplers = {doc["textures"][i]["sampler"] for i in textures if "sampler" in doc["textures"][i]}
    accessors = set().union(*(_accessors_of(p) for p in primitives)) if primitives else set()
    accessors |= {skin["inverseBindMatrices"] for skin in doc.get("skins", []) if "inverseBindMatrices" in skin}
    for animation in doc.get("animations", []):
        for sampler in animation.get("samplers", []):
            accessors |= {sampler["input"], sampler["output"]}
    views = {doc["accessors"][i]["bufferView"] for i in accessors if "bufferView" in doc["accessors"][i]}
    for i in accessors:
        sparse = doc["accessors"][i].get("sparse")
        if sparse:
            views |= {sparse["indices"]["bufferView"], sparse["values"]["bufferView"]}
    views |= {doc["images"][i]["bufferView"] for i in images if "bufferView" in doc["images"][i]}
    maps = {"meshes": _kept(len(doc.get("meshes", [])), meshes), "materials": _kept(len(doc.get("materials", [])), materials),
            "textures": _kept(len(doc.get("textures", [])), textures), "images": _kept(len(doc.get("images", [])), images),
            "samplers": _kept(len(doc.get("samplers", [])), samplers), "accessors": _kept(len(doc["accessors"]), accessors),
            "bufferViews": _kept(len(doc["bufferViews"]), views)}
    # ---- the binary chunk without what nothing reaches --------------------------------------------------------------------------------------------------
    blob = bytearray()
    new_views = []
    for old in sorted(maps["bufferViews"]):
        view = dict(doc["bufferViews"][old])
        part = glb.view_bytes(old)
        blob += b"\0" * (-len(blob) % 4)
        view["buffer"], view["byteOffset"] = 0, len(blob)
        new_views.append(view)
        blob += part
    blob += b"\0" * (-len(blob) % 4)
    # ---- every reference, pointed at its new number ---------------------------------------------------------------------------------------------------
    for node in nodes:
        if "mesh" in node:
            node["mesh"] = maps["meshes"][node["mesh"]]
    new_meshes = [doc["meshes"][i] for i in sorted(maps["meshes"])]
    for mesh in new_meshes:
        for primitive in mesh.get("primitives", []):
            primitive["attributes"] = {k: maps["accessors"][v] for k, v in primitive["attributes"].items()}
            if "indices" in primitive:
                primitive["indices"] = maps["accessors"][primitive["indices"]]
            if "material" in primitive:
                primitive["material"] = maps["materials"][primitive["material"]]
            if "targets" in primitive:
                primitive["targets"] = [{k: maps["accessors"][v] for k, v in target.items()} for target in primitive["targets"]]
    new_materials = [doc["materials"][i] for i in sorted(maps["materials"])]
    for material in new_materials:
        for ref in _texture_refs(material):
            ref["index"] = maps["textures"][ref["index"]]
    new_textures = [doc["textures"][i] for i in sorted(maps["textures"])]
    for texture in new_textures:
        if "source" in texture:
            texture["source"] = maps["images"][texture["source"]]
        if "sampler" in texture:
            texture["sampler"] = maps["samplers"][texture["sampler"]]
        for extension in (texture.get("extensions") or {}).values():
            if isinstance(extension, dict) and "source" in extension:
                extension["source"] = maps["images"][extension["source"]]
    new_images = [doc["images"][i] for i in sorted(maps["images"])]
    for image in new_images:
        if "bufferView" in image:
            image["bufferView"] = maps["bufferViews"][image["bufferView"]]
    new_accessors = [doc["accessors"][i] for i in sorted(maps["accessors"])]
    for accessor in new_accessors:
        if "bufferView" in accessor:
            accessor["bufferView"] = maps["bufferViews"][accessor["bufferView"]]
        sparse = accessor.get("sparse")
        if sparse:
            sparse["indices"]["bufferView"] = maps["bufferViews"][sparse["indices"]["bufferView"]]
            sparse["values"]["bufferView"] = maps["bufferViews"][sparse["values"]["bufferView"]]
    for skin in doc.get("skins", []):
        if "inverseBindMatrices" in skin:
            skin["inverseBindMatrices"] = maps["accessors"][skin["inverseBindMatrices"]]
    for animation in doc.get("animations", []):
        for sampler in animation.get("samplers", []):
            sampler["input"], sampler["output"] = maps["accessors"][sampler["input"]], maps["accessors"][sampler["output"]]
    for key, items in (("meshes", new_meshes), ("materials", new_materials), ("textures", new_textures), ("images", new_images), ("samplers", [doc["samplers"][i] for i in sorted(maps["samplers"])] if "samplers" in doc else []),
                       ("accessors", new_accessors), ("bufferViews", new_views)):
        if items:
            doc[key] = items
        else:
            doc.pop(key, None)
    doc["buffers"] = [{"byteLength": len(blob)}]
    glb.json, glb.bin = doc, bytes(blob)
    return glb.dumps(), removed
