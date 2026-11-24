"""
Altera — Reading character cards (Character Card V2, and the older V1 and newer V3 shapes), without any image library.

A PNG card is an ordinary PNG with the card's JSON, base64-encoded, in a text chunk. The usual keyword is "chara"; V3 cards
add "ccv3" and some tools write "tavernCardV2". tEXt, zTXt (zlib) and iTXt chunks are all read. A .json card is the same
JSON on its own.

parse_png(data) -> (card, clean_png)   the card, and the image with every text chunk removed (that is what is shown)
parse_json(data) -> card
normalize(raw) -> card                 V1/V2/V3 -> one dict of V2 data fields, every value a string or list of strings,
                                       plus "book": the character book (lorebook) or None, see read_book

Everything is bounded: file size, chunk count, decompressed size and JSON size. A malformed file raises CardError with a
message that is safe to show.
"""
import base64
import binascii
import json
import re
import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_CHUNKS = 4096
MAX_CARD_BYTES = 2 * 1024 * 1024           # the decoded JSON of one card
MAX_IMAGE_SIDE = 8192
CARD_KEYWORDS = ("ccv3", "chara", "taverncardv2")   # preferred first; compared case-insensitively
TEXT_CHUNKS = {b"tEXt", b"zTXt", b"iTXt"}

# The V2 data fields Altera reads. extensions are kept only as an opaque original (the saved file).
STRING_FIELDS = ("name", "description", "personality", "scenario", "first_mes", "mes_example", "creator_notes",
                 "system_prompt", "post_history_instructions", "creator", "character_version")
LIST_FIELDS = ("alternate_greetings", "tags")
FIELD_LIMIT = 60_000                          # characters per text field
BOOK_ENTRIES = 512                            # entries read from a character book
BOOK_KEYS = 32                                # keys per entry
KEY_LIMIT = 100                               # characters per key
ENTRY_LIMIT = 8000                            # characters per entry
POSITIONS = ("before_char", "after_char")
_REGEX_KEY = re.compile(r"^/.+/[a-z]*$")       # a key written as a regular expression (/pattern/flags)


class CardError(ValueError):
    """The file is not a card Altera can read. The message is safe to show."""


def _chunks(data: bytes):
    if len(data) > MAX_FILE_BYTES:
        raise CardError("The card is larger than 20 MB.")
    if not data.startswith(PNG_SIGNATURE):
        raise CardError("That file is not a PNG image.")
    pos, count = len(PNG_SIGNATURE), 0
    while pos < len(data):
        if pos + 8 > len(data):
            raise CardError("The PNG file is cut short.")
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        end = pos + 12 + length
        if end > len(data):
            raise CardError("The PNG file is cut short.")
        body = data[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length:end])[0]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise CardError("The PNG file is damaged (a checksum does not match).")
        yield kind, body, data[pos:end]
        count += 1
        if count > MAX_CHUNKS:
            raise CardError("The PNG file has too many parts to be a card.")
        pos = end
        if kind == b"IEND":
            return
    raise CardError("The PNG file has no end marker.")


def _inflate(body: bytes) -> bytes:
    stream = zlib.decompressobj()
    out = stream.decompress(body, MAX_CARD_BYTES * 2 + 1)
    if len(out) > MAX_CARD_BYTES * 2 or stream.unconsumed_tail:
        raise CardError("The card text is too large.")
    return out


def _text_chunk(kind: bytes, body: bytes):
    """(keyword, text bytes) of a tEXt / zTXt / iTXt chunk, or None if it is malformed."""
    keyword, sep, rest = body.partition(b"\0")
    if not sep or not 1 <= len(keyword) <= 79:
        return None
    try:
        if kind == b"tEXt":
            return keyword.decode("latin-1"), rest
        if kind == b"zTXt":
            return (keyword.decode("latin-1"), _inflate(rest[1:])) if rest[:1] == b"\0" else None
        compressed, method = rest[:1], rest[1:2]
        _language, sep1, rest = rest[2:].partition(b"\0")
        _translated, sep2, text = rest.partition(b"\0")
        if not (sep1 and sep2):
            return None
        if compressed == b"\1":
            if method != b"\0":
                return None
            text = _inflate(text)
        return keyword.decode("latin-1"), text
    except (zlib.error, UnicodeDecodeError):
        return None


def _decode_card_text(text: bytes):
    raw = text.strip()
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        decoded = raw                                         # a few tools store the JSON itself
    if len(decoded) > MAX_CARD_BYTES:
        raise CardError("The card text is too large.")
    try:
        return json.loads(decoded.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None


def png_size(data: bytes):
    """(width, height) of a PNG Altera can show, or CardError. Every chunk's checksum and the end marker are checked, so a
    truncated or damaged upload is refused before it is written."""
    width = height = 0
    picture = False
    for kind, body, _whole in _chunks(data):
        if kind == b"IHDR":
            if len(body) != 13:
                raise CardError("The PNG header is damaged.")
            width, height = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            picture = True
    if not (0 < width <= MAX_IMAGE_SIDE and 0 < height <= MAX_IMAGE_SIDE):
        raise CardError(f"The image must be at most {MAX_IMAGE_SIDE} pixels on each side.")
    if not picture:
        raise CardError("The PNG image has no picture data.")
    return width, height


def parse_png(data: bytes):
    """(card, clean_png): the normalized card and the same image without its text chunks."""
    found, kept, width, height = {}, [PNG_SIGNATURE], 0, 0
    for kind, body, whole in _chunks(data):
        if kind == b"IHDR":
            if len(body) != 13:
                raise CardError("The PNG header is damaged.")
            width, height = struct.unpack(">II", body[:8])
        if kind in TEXT_CHUNKS:
            parsed = _text_chunk(kind, body)
            if parsed and parsed[0].lower() in CARD_KEYWORDS and parsed[0].lower() not in found:
                found[parsed[0].lower()] = parsed[1]
            continue                                          # text chunks are never kept in the shown image
        kept.append(whole)
    if not (0 < width <= MAX_IMAGE_SIDE and 0 < height <= MAX_IMAGE_SIDE):
        raise CardError(f"The card image must be at most {MAX_IMAGE_SIDE} pixels on each side.")
    for keyword in CARD_KEYWORDS:
        if keyword in found:
            raw = _decode_card_text(found[keyword])
            if raw is not None:
                return normalize(raw), b"".join(kept)
    raise CardError("This PNG has no character card in it (no chara or ccv3 text).")


def parse_json(data: bytes):
    if len(data) > MAX_CARD_BYTES:
        raise CardError("The card is larger than 2 MB.")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise CardError("That file is not valid JSON.") from None
    return normalize(raw)


def _text(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)):
        return ""
    return str(value).replace("\r\n", "\n").strip()[:FIELD_LIMIT]


def _flag(value, default=False) -> bool:
    return value if isinstance(value, bool) else default


def _number(value, default, low, high) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, int(value)))


def _keys(value) -> list:
    values = value if isinstance(value, list) else []
    keys = []
    for item in values[:BOOK_KEYS]:
        text = " ".join(_text(item).split())[:KEY_LIMIT]
        if text and text not in keys:
            keys.append(text)
    return keys


def read_book(raw):
    """A character_book (V2/V3) as {name, scan_depth, token_budget, entries: [...]}, or None when there is none. Each entry is
    {name, keys, secondary_keys, content, enabled, constant, selective, case_sensitive, regex, order, position}: unknown or
    broken values fall back to the spec's meaning, and an entry with no content is dropped."""
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        return None
    entries = []
    for item in raw["entries"][:BOOK_ENTRIES]:
        if not isinstance(item, dict):
            continue
        content = _text(item.get("content"))[:ENTRY_LIMIT]
        if not content:
            continue
        extensions = item.get("extensions") if isinstance(item.get("extensions"), dict) else {}
        position = item.get("position") if item.get("position") in POSITIONS else "before_char"
        keys, secondary = _keys(item.get("keys")), _keys(item.get("secondary_keys"))
        entries.append({
            "name": " ".join(_text(item.get("name") or item.get("comment")).split())[:120],
            "keys": keys, "secondary_keys": secondary, "content": content,
            "enabled": _flag(item.get("enabled"), True), "constant": _flag(item.get("constant")),
            "selective": _flag(item.get("selective")), "case_sensitive": _flag(item.get("case_sensitive")),
            # V3 use_regex, or SillyTavern's /pattern/ keys: Altera does not run a card's regular expressions
            "regex": (_flag(item.get("use_regex")) or _flag(extensions.get("use_regex"))
                      or any(_REGEX_KEY.match(key) for key in keys + secondary)),
            "order": _number(item.get("insertion_order"), 100, -100000, 100000), "position": position,
        })
    if not entries:
        return None
    return {"name": " ".join(_text(raw.get("name")).split())[:120],
            "scan_depth": _number(raw.get("scan_depth"), 0, 0, 100),
            "token_budget": _number(raw.get("token_budget"), 0, 0, 100000), "entries": entries}


def normalize(raw) -> dict:
    """One flat dict of V2 data fields from a V1, V2 or V3 card. Raises CardError when there is no usable card."""
    if not isinstance(raw, dict):
        raise CardError("The card is not a JSON object.")
    spec = raw.get("spec")
    if spec in ("chara_card_v2", "chara_card_v3") or isinstance(raw.get("data"), dict):
        data = raw.get("data")
        if not isinstance(data, dict):
            raise CardError("The card has no data section.")
        version = "v3" if spec == "chara_card_v3" else "v2"
    else:
        data = {**raw, "first_mes": raw.get("first_mes", raw.get("first_message"))}   # V1: the fields sit at the top
        version = "v1"
    card = {field: _text(data.get(field)) for field in STRING_FIELDS}
    for field in LIST_FIELDS:
        values = data.get(field) if isinstance(data.get(field), list) else []
        card[field] = [text for text in (_text(v) for v in values[:64]) if text]
    card["spec"] = version
    card["book"] = read_book(data.get("character_book"))
    card["name"] = " ".join(card["name"].split())[:80]
    if not card["name"]:
        raise CardError("The card has no name.")
    if not (card["description"] or card["personality"] or card["first_mes"]):
        raise CardError("The card has no description, personality or first message.")
    return card
