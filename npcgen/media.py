"""Fel's Studio labels for PNG and binary glTF delivery files."""
import io
import json
import struct
import xml.etree.ElementTree as ET
import zlib

from . import __version__

MADE_BY = f"Fel's NPC Forge {__version__}"
PNG = b"\x89PNG\r\n\x1a\n"
XMP = b"XML:com.adobe.xmp"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
IPTC = "http://iptc.org/std/Iptc4xmpExt/2008-02-29/"
DIGITAL_SOURCE_TYPE = f"{{{IPTC}}}DigitalSourceType"
AI_SOURCE = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
TEXT_KEYS = {b"fels:ai-generated", b"fels:made-by", b"fels:models", XMP}
GLB_HEADER = struct.Struct("<4sII")
GLB_CHUNK = struct.Struct("<I4s")
MAX_TEXT = 1 << 20

ET.register_namespace("x", "adobe:ns:meta/")
ET.register_namespace("rdf", RDF)
ET.register_namespace("Iptc4xmpExt", IPTC)


def _png_chunks(data):
    offset = len(PNG)
    while offset + 12 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, offset)
        end = offset + 12 + size
        if end > len(data):
            break
        yield kind, memoryview(data)[offset + 8:end - 4], memoryview(data)[offset:end]
        if kind == b"IEND":
            return
        offset = end
    raise ValueError("Incomplete PNG")


def _png_text(chunks):
    for kind, payload, _ in chunks:
        if kind not in (b"tEXt", b"iTXt", b"zTXt"):
            continue
        key = bytes(payload[:80]).split(b"\0", 1)[0]
        if key not in TEXT_KEYS:
            continue
        value = bytes(payload[len(key) + 1:])
        try:
            if kind == b"tEXt":
                yield key, value.decode("latin-1")
                continue
            if kind == b"iTXt":
                compressed = value[:1] == b"\1"
                value = value[2:].split(b"\0", 2)[2]
            else:
                compressed, value = True, value[1:]
            if compressed:
                reader = zlib.decompressobj()
                value = reader.decompress(value, MAX_TEXT)
                if not reader.eof:
                    continue
            yield key, value.decode("utf-8" if kind == b"iTXt" else "latin-1")
        except (IndexError, UnicodeError, zlib.error):
            continue


def _xmp(packet):
    """Keep the existing RDF properties and set the IPTC URI on its description."""
    try:
        root = ET.fromstring(packet) if packet else None
    except ET.ParseError:
        root = None
    if root is None:
        root = ET.Element("{adobe:ns:meta/}xmpmeta")
    rdf = root if root.tag == f"{{{RDF}}}RDF" else root.find(f".//{{{RDF}}}RDF")
    if rdf is None:
        rdf = ET.SubElement(root, f"{{{RDF}}}RDF")
    descriptions = rdf.findall(f"{{{RDF}}}Description")
    if not descriptions:
        descriptions = [ET.SubElement(rdf, f"{{{RDF}}}Description", {f"{{{RDF}}}about": ""})]
    for desc in descriptions:
        desc.attrib.pop(DIGITAL_SOURCE_TYPE, None)
        for child in desc.findall(DIGITAL_SOURCE_TYPE):
            desc.remove(child)
    ET.SubElement(descriptions[0], DIGITAL_SOURCE_TYPE, {f"{{{RDF}}}resource": AI_SOURCE})
    return ET.tostring(root, encoding="unicode")


def _glb_document(data):
    if len(data) < 20 or GLB_HEADER.unpack_from(data) != (b"glTF", 2, len(data)):
        raise ValueError("Invalid binary glTF header")
    size, kind = GLB_CHUNK.unpack_from(data, 12)
    if kind != b"JSON" or size % 4 or size + 20 > len(data):
        raise ValueError("Invalid binary glTF JSON chunk")
    document = json.loads(data[20:20 + size])
    if not isinstance(document, dict) or not isinstance(document.get("asset"), dict):
        raise ValueError("Invalid glTF asset")
    return document, 20 + size


def _object(value):
    return value if isinstance(value, dict) else {}


def _names(values):
    return sorted({name for name in values if isinstance(name, str) and name})


def _png_fields(texts):
    if b"fels:ai-generated" not in texts:
        return {}
    try:
        names = json.loads(texts.get(b"fels:models", "[]"))
    except ValueError:
        names = []
    return {"ai-generated": {"true": True, "false": False}.get(texts.get(b"fels:ai-generated")),
            "made-by": texts.get(b"fels:made-by"), "models": names}


def _glb_fields(document):
    return _object(_object(document["asset"].get("extras")).get("fels"))


def read_label(data):
    """Read Forge label fields without decoding image pixels or mesh buffers."""
    try:
        if data.startswith(PNG):
            return _png_fields(dict(_png_text(_png_chunks(data))))
        if data.startswith(b"glTF"):
            document, _ = _glb_document(data)
            return _glb_fields(document)
    except (ValueError, RecursionError):
        pass
    return {}


def source_models(*sources):
    """Names already recorded on one or more source files."""
    models = []
    for source in sources:
        names = read_label(source).get("models")
        if isinstance(names, list):
            models.extend(names)
    return _names(models)


def _fields(previous, models, ai_generated):
    if models is None:
        names = previous.get("models")
        models = names if isinstance(names, list) else ()
    if ai_generated is None:
        ai_generated = previous.get("ai-generated") is not False
    return {"ai-generated": ai_generated, "made-by": MADE_BY, "models": _names(models)}


def _text_chunk(key, value):
    chunk = b"iTXt" + key + b"\0\0\0\0\0" + value.encode("utf-8")
    return struct.pack(">I", len(chunk) - 4) + chunk + struct.pack(">I", zlib.crc32(chunk))


def _label_png(data, models, ai_generated):
    chunks = tuple(_png_chunks(data))
    texts = dict(_png_text(chunks))
    fields = _fields(_png_fields(texts), models, ai_generated)
    written = {b"fels:ai-generated": json.dumps(fields["ai-generated"]), b"fels:made-by": fields["made-by"],
               b"fels:models": json.dumps(fields["models"], ensure_ascii=False)}
    if fields["ai-generated"]:
        written[XMP] = _xmp(texts.get(XMP))
    if all(texts.get(key) == value for key, value in written.items()):
        return data
    # Keep IDAT/APNG data, colour profiles and unrelated metadata byte for byte.
    parts = [PNG]
    for kind, payload, raw in chunks:
        if kind in (b"tEXt", b"iTXt", b"zTXt") and bytes(payload[:80]).split(b"\0", 1)[0] in written:
            continue
        parts.append(raw)
        if kind == b"IHDR":
            parts.extend(_text_chunk(key, value) for key, value in written.items())
    return b"".join(parts)


def _label_glb(data, models, ai_generated):
    document, end = _glb_document(data)
    asset = document["asset"]
    previous = _glb_fields(document)
    fields = {**previous, **_fields(previous, models, ai_generated)}
    if fields == previous:
        return data
    old_extras = asset.get("extras")
    extras = _object(old_extras)
    if old_extras is not None and not isinstance(old_extras, dict):
        extras = {"sourceExtras": old_extras}
    asset["extras"] = {**extras, "fels": fields}
    body = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    body += b" " * (-len(body) % 4)
    return b"".join((GLB_HEADER.pack(b"glTF", 2, 20 + len(body) + len(data) - end),
                      GLB_CHUNK.pack(len(body), b"JSON"), body, memoryview(data)[end:]))


def label(data, models=None, *, ai_generated=None):
    """Label PNG or GLB bytes. Other formats pass through for mixed-file export writers."""
    if data.startswith(PNG):
        return _label_png(data, models, ai_generated)
    if len(data) >= 20 and data.startswith(b"glTF"):
        return _label_glb(data, models, ai_generated)
    return data


def png(image, target=None, *, models=None, ai_generated=None, **options):
    """Encode and label a new PNG, returning bytes or writing to a path or stream."""
    buffer = io.BytesIO()
    image.save(buffer, "PNG", **options)
    data = label(buffer.getvalue(), models, ai_generated=ai_generated)
    if target is None:
        return data
    if hasattr(target, "write"):
        target.write(data)
    else:
        with open(target, "wb") as stream:
            stream.write(data)
