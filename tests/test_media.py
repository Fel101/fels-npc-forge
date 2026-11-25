"""Provenance survives normal file readers."""
import io
import json
import struct
import xml.etree.ElementTree as ET

from PIL import Image, PngImagePlugin

from npcgen import media


def test_png_label_preserves_pixels_palette_transparency_and_other_metadata():
    image = Image.new("P", (3, 2), 1)
    image.putpalette([0, 0, 0, 128, 20, 64])
    extra = PngImagePlugin.PngInfo()
    extra.add_text("chara", "card data")
    extra.add_itxt("XML:com.adobe.xmp", f'<rdf:RDF xmlns:rdf="{media.RDF}" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<rdf:Description dc:creator="The artist"/></rdf:RDF>', zip=True)
    raw = io.BytesIO()
    image.save(raw, "PNG", pnginfo=extra, transparency=0)
    data = media.label(raw.getvalue(), ["Qwen-Image-Edit 2511", "Modèle", "Modèle"])
    with Image.open(io.BytesIO(data)) as result:
        assert result.mode == "P" and result.info["transparency"] == 0
        assert result.convert("RGBA").tobytes() == Image.open(io.BytesIO(raw.getvalue())).convert("RGBA").tobytes()
        assert result.text["chara"] == "card data" and result.text["fels:ai-generated"] == "true"
        assert result.text["fels:made-by"] == media.MADE_BY
        assert json.loads(result.text["fels:models"]) == ["Modèle", "Qwen-Image-Edit 2511"]
        packet = ET.fromstring(result.text["XML:com.adobe.xmp"])
        assert packet.find(f".//{media.DIGITAL_SOURCE_TYPE}").attrib[f"{{{media.RDF}}}resource"] == media.AI_SOURCE
        assert packet.find(f".//{{{media.RDF}}}Description").attrib["{http://purl.org/dc/elements/1.1/}creator"] == "The artist"
    assert media.label(data) == data
    assert media.read_label(media.label(data, ["New renderer"]))["models"] == ["New renderer"]


def test_external_photo_label_leaves_no_generated_source_type():
    data = media.png(Image.new("RGB", (2, 2)), ai_generated=False)
    assert media.read_label(data) == {"ai-generated": False, "made-by": media.MADE_BY, "models": []}
    assert "XML:com.adobe.xmp" not in Image.open(io.BytesIO(data)).text
    assert media.label(data) == data


def test_glb_label_preserves_asset_extras_and_every_non_json_chunk():
    doc = {"asset": {"version": "2.0", "extras": {"landmarks": [1, 2, 3], "fels": {"custom": 7}}}, "nodes": [{"name": "Pelvis"}]}
    raw = json.dumps(doc).encode()
    raw += b" " * (-len(raw) % 4)
    chunks = struct.pack("<I4s", 4, b"BIN\0") + b"body" + struct.pack("<I4s", 4, b"TEST") + b"more"
    data = struct.pack("<4sIII4s", b"glTF", 2, 20 + len(raw) + len(chunks), len(raw), b"JSON") + raw + chunks
    marked = media.label(data, ["Pixal3D"])
    version, total, size, kind = struct.unpack_from("<III4s", marked, 4)
    rebuilt = json.loads(marked[20:20 + size])
    assert (version, total, kind) == (2, len(marked), b"JSON") and size % 4 == 0
    assert marked[20 + size:] == chunks
    assert rebuilt["nodes"] == doc["nodes"] and rebuilt["asset"]["extras"]["landmarks"] == [1, 2, 3]
    assert rebuilt["asset"]["extras"]["fels"] == {"custom": 7, "ai-generated": True, "made-by": media.MADE_BY, "models": ["Pixal3D"]}
    assert media.label(marked) == marked
