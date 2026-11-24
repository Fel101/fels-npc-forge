"""Portable Character Card V3 exports for saved Forge characters."""

import base64
import datetime
import hashlib
import io
import json
import re
import stat
import time
import zipfile

from PIL import Image, PngImagePlugin

from . import __version__, dialogue, fels_suite, licences, roll
from .voice import engines, markup, record
from .voice.lab_client import LabClient, LabError

APP = "npc-forge"
CREATOR = "Fel's NPC Forge"
EMBED = "embeded://"
SHEET = "assets/other/fels/npc.json"
LIVING = "assets/other/fels/living"
PERSONA = "assets/other/fels/voice.zip"
MAX_MEMBER = 20 * 1024 * 1024
MAX_CHARX = 100 * 1024 * 1024
MAX_MEMBERS = 500
MAX_JSON = 2 * 1024 * 1024
MAX_SIDE = 8192
EMOTIONS = {
    "neutral": "neutral", "happy": "joy", "sad": "sadness", "angry": "anger", "surprised": "surprise",
    "scared": "fear", "disgusted": "disgust", "laughing": "amusement", "tired": "tired", "smug": "pride",
    "embarrassed": "embarrassment", "thinking": "curiosity", "flirty": "flirty",
}
PROFILE_FIELDS = (*roll.PROFILE_TEXT, "age", "age_group", "gender", "personality", "hobbies")
RATINGS = {"yes": "commercial_ok", "conditions": "commercial_conditions", "unclear": "not_cleared", "no": "non_commercial"}


def allowed(npc):
    """The old Models setting and the registered adult settings cannot export a core card."""
    return npc.get("setting") != "models" and npc.get("setting") not in roll.extension_adult_settings()


def inbox_ready():
    folder = fels_suite.inbox_dir("altera")
    for path in (folder.parent, folder):
        try:
            info = path.lstat()
        except OSError:
            return False
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            return False
    return True


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sheet(npc):
    # A portable sheet carries character fields. App state, local paths, add-on data and original uploads stay in Forge.
    return {
        **{key: npc[key] for key in ("id", "setting", "style", "morphology", "freeform", "adult") if key in npc},
        "profile": {key: npc["profile"][key] for key in PROFILE_FIELDS if key in npc["profile"]},
        "appearance": {key: value for key, value in (npc.get("appearance") or {}).items() if key in roll.APPEARANCE_TEXT},
    }


def _lines(npc):
    generated = dialogue.generate(npc)["lines"]
    saved = (npc.get("dialogue") or {}).get("lines") or {}
    return {category: [line for row in saved.get(category, []) if (line := markup.plain(row.get("text")))]
            or [row["text"] for row in generated[category]] for category in dialogue.CATEGORIES}


def _card_text(npc, lines, licence):
    p = npc["profile"]
    name = " ".join(filter(None, (p.get("first_name"), p.get("last_name")))).strip() or "Unnamed character"
    identity = ", ".join(filter(None, (p.get("occupation"), p.get("race") or p.get("heritage"))))
    look = npc.get("appearance") or {}
    appearance = look.get("extra") if npc.get("freeform") else ", ".join(
        look[key] for key in ("build", "hair", "eyes", "distinguishing", "outfit") if look.get(key))
    description = "\n\n".join(filter(None, (p.get("bio"), f"{name} ({identity})." if identity else "", appearance)))
    traits = ", ".join(p.get("personality") or [])
    manner = [traits + "." if traits else ""]
    for key, label in (("speech", "Speech"), ("demeanor", "Demeanor"), ("quirk", "Quirk"), ("peeve", "Pet peeve")):
        if p.get(key):
            manner.append(f"{label}: {p[key]}")
    examples = (("How do you spend your time?", "idle", ""),
                ("Can you help me?", "trade", f"*Manner: {p['demeanor']}* " if p.get("demeanor") else ""),
                ("What are you working toward?", "quest", f"*Tone of voice: {p['speech']}* " if p.get("speech") else ""))
    return {
        "name": name, "description": description or name, "personality": "\n".join(filter(None, manner)),
        "scenario": f"{{{{char}}}} is working toward this goal: {p['goal']}" if p.get("goal") else "",
        "first_mes": lines["greeting"][0], "alternate_greetings": lines["greeting"][1:],
        "mes_example": "\n".join(f"<START>\n{{{{user}}}}: {prompt}\n{{{{char}}}}: {direction}{lines[category][0]}"
                                 for prompt, category, direction in examples),
        "tags": list(dict.fromkeys(filter(None, (npc.get("setting"), p.get("occupation"), *(p.get("personality") or []))))),
        "creator": CREATOR, "character_version": "1", "system_prompt": "", "post_history_instructions": "",
        "creator_notes": f"Made with {CREATOR}.\n{licence['line']}",
    }


def _asset(lib, npc_id, rel, label):
    try:
        with lib.resolve(npc_id, rel).open("rb") as stream:
            data = stream.read(MAX_MEMBER + 1)
    except (OSError, KeyError):
        raise ValueError(f"The saved {label} could not be read. Regenerate it in Forge and try again.") from None
    if len(data) > MAX_MEMBER:
        raise ValueError(f"The {label} is over 20 MB. Make a smaller one and try again.")
    return data


def _image(data, label):
    try:
        image = Image.open(io.BytesIO(data), formats=("PNG",))
        if max(image.size) > MAX_SIDE:
            raise ValueError
        image.verify()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ValueError(f"The saved {label} is unreadable or over {MAX_SIDE} pixels on one side. Regenerate it and try again.") from None
    return data


def _png_card(portrait, card):
    metadata = PngImagePlugin.PngInfo()
    v2 = {key: value for key, value in card["data"].items()
          if key not in {"assets", "group_only_greetings", "creation_date", "modification_date"}}
    v2["creator_notes"] += "\nThis is the V2 copy of a V3 card. A V3 reader can open the full card."
    for keyword, payload in (("ccv3", card), ("chara", {"spec": "chara_card_v2", "spec_version": "2.0", "data": v2})):
        encoded = _json(payload)
        if len(encoded) > MAX_JSON:
            raise ValueError("The character card text is over 2 MB. Shorten the character's sheet and try again.")
        metadata.add_text(keyword, base64.b64encode(encoded).decode("ascii"))
    with Image.open(io.BytesIO(portrait)) as image:
        output = io.BytesIO()
        # Keep the portrait's pixels and colour profile, with fresh card fields as its text metadata.
        image.save(output, format="PNG", pnginfo=metadata)
    if output.tell() > MAX_MEMBER:
        raise ValueError("The PNG card is over 20 MB. Make a smaller portrait or try the CHARX card.")
    return output.getvalue()


class _Archive:
    def __init__(self):
        self.files = {}
        self.assets = []
        self.size = 0

    def add(self, path, data, kind, name):
        if len(data) > MAX_MEMBER:
            raise ValueError(f"The {name} is over 20 MB. Make a smaller one or export without it.")
        self.size += len(data)
        if self.size > MAX_CHARX or len(self.files) + 2 > MAX_MEMBERS:
            raise ValueError("The CHARX card exceeds Altera's size limits. Try the PNG card or export without the voice.")
        uri = EMBED + path
        self.files[path] = data
        self.assets.append({"type": kind, "name": name, "uri": uri, "ext": path.rsplit(".", 1)[-1]})
        return uri

    def finish(self, card):
        encoded = _json(card)
        if len(encoded) > MAX_JSON:
            raise ValueError("The character card text is over 2 MB. Shorten the character's sheet and try again.")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zip_:
            zip_.writestr("card.json", encoded)
            for path, data in self.files.items():
                zip_.writestr(path, data)
        if output.tell() > MAX_CHARX or self.size + len(encoded) > MAX_CHARX:
            raise ValueError("The CHARX card is over 100 MB. Try the PNG card or export without the voice.")
        return output.getvalue()


def _living(archive, lib, npc_id, meta, portrait, expressions):
    if not meta or meta.get("stale"):
        return None, {}
    label = "living portrait"
    try:
        manifest = json.loads(_asset(lib, npc_id, meta["file"], label))
        digest = hashlib.sha256(portrait).hexdigest()
        if manifest["portrait"]["sha256"] != digest:
            return None, {}
        frames = manifest["frames"]
        if not isinstance(frames, dict) or not frames:
            raise ValueError
    except (TypeError, KeyError, ValueError):
        raise ValueError("The saved living portrait is unreadable. Rebuild it in Forge and try again.") from None
    source = {}
    for key, frame in frames.items():
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", key) or not isinstance(frame, dict) or frame.get("file") != f"living/{key}.png":
            raise ValueError("The saved living portrait has an invalid frame. Rebuild it in Forge and try again.")
        name = f"living portrait frame {key}"
        picture = _image(_asset(lib, npc_id, frame["file"], name), name)
        path = f"{LIVING}/{key}.png"
        archive.add(path, picture, "other", f"living-{key}")
        frame["file"] = path
        expr = expressions.get(key)
        source[key] = expr if expr and expr.get("created") == frame.get("source_created") else None
    uri = archive.add(f"{LIVING}/manifest.json", _json(manifest), "other", "living-portrait-manifest")
    return {"manifest": uri, "portrait_sha256": digest}, source


def voice_selection(npc):
    saved = npc.get("voice") or {}
    if selected := saved.get("lab_voice"):
        return selected
    # Older stock choices are still portable by their model and speaker. Old reference WAVs never enter a card.
    if saved.get("mode") == "stock" and saved.get("stock_voice"):
        engine = record.speaker_for(saved)
        if model := engines.PRESET_MODELS.get(engine):
            return {"name": saved["stock_voice"], "voice_id": saved["stock_voice"], "model": model}
    return None


def needs_permission(selected):
    return bool(selected and selected.get("identity_id") and selected.get("cloned_from_person") is not False)


def consent_for(selected):
    """Tie the sharing choice to the Cast voice shown when the dialog opened."""
    return hashlib.sha256(_json([selected.get(key) for key in ("lab_id", "identity_id", "name", "model")])).hexdigest()


def _persona(selected, audition, permission):
    client = LabClient.discover()
    if client.health.get("lab_id") != selected.get("lab_id"):
        raise LabError("Start the Voice Lab library where this Cast voice was made, or pick another voice in Forge.")
    identity = selected["identity_id"]
    current = client.voice(identity)
    data = client.voice_file(identity, audition, permission=permission)
    if len(data) > MAX_MEMBER:
        raise ValueError("The Voice Lab file is over 20 MB. Choose another voice or export without it.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as file:
            if file.getinfo("manifest.json").file_size > MAX_JSON:
                raise ValueError
            manifest = json.loads(file.read("manifest.json"))
        voice = manifest["contents"]["voice"]
        if manifest["schema"] != "fels-voice-portable-v1" or manifest["kind"] != "persona" or voice["id"] != identity:
            raise ValueError
        sample = voice.get("sample")
        origin = sample.get("source_type") if sample else None
        if not voice.get("audition") or (voice.get("kind") == "reference" and origin not in {"designed", "recording"}):
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError, AttributeError, zipfile.BadZipFile):
        raise LabError("Voice Lab returned an unreadable voice file. Check the voice in Cast or export without it.") from None
    cloned = origin == "recording"
    if cloned and permission is not True:
        raise LabError("Confirm that you may share the person's recording, or export without the voice.")
    return data, {**selected, **current, "licence": voice.get("licence") or current.get("licence"), "cloned_from_person": cloned}


def _licence(pictures, living_sources, selected):
    report = licences.check({"assets": pictures})
    rows = {row["id"]: {"name": row["name"], "licence": row["licence"], "used_for": ", ".join(row["used_for"])}
            for row in report["models"]}
    unknown = report["unknown"][:]
    statuses = [report["verdict"]]
    if living_sources:
        living_report = licences.check({"assets": {"expressions": {k: v for k, v in living_sources.items() if v}}})
        for model in living_report["models"]:
            if model["id"] in rows:
                rows[model["id"]]["used_for"] += ", living portrait"
            else:
                rows[model["id"]] = {"name": model["name"], "licence": model["licence"], "used_for": "living portrait"}
        if living_report["verdict"] in licences.RANK:
            statuses.append(living_report["verdict"])
        unknown.extend(note.replace("The expressions", "The living portrait") for note in living_report["unknown"])
        if None in living_sources.values():
            unknown.append("The model for one or more living portrait frames could not be verified.")
            statuses.append("unclear")
    registry = licences.load()
    if selected:
        old_engine = engines.LAB_MODELS.get(selected["model"], selected["model"])
        selector = registry["by_selector"].get(f"voice:{old_engine}")
        model = registry["by_id"].get(selector, {})
        terms = selected.get("licence") or {}
        status = terms.get("rating") or (licences.effective(selector) if selector else "unclear")
        statuses.append(status if status in licences.RANK else "unclear")
        rows["voice"] = {"name": selected.get("model_name") or selected.get("label") or model.get("name") or selected["model"],
                         "licence": model.get("licence") or "See Voice Lab's output terms", "used_for": "voice",
                         **({"reason": terms["reason"]} if terms.get("reason") else {})}
    verdict = licences.worst(statuses)
    label = next(status["label"] for status in registry["statuses"] if status["id"] == verdict)
    return {"rating": RATINGS[verdict], "models": list(rows.values()), "line": f"{label}.",
            **({"unknown": unknown} if unknown else {})}


def build(lib, npc, form, *, include_voice=True, permission=False):
    """Return one complete PNG or CHARX. A live Lab is needed only for a portable Cast voice."""
    if not allowed(npc):
        raise ValueError("Cards export characters from Forge's core settings. Open a character from a core setting and try again.")
    assets = npc.get("assets") or {}
    portrait = assets.get("portrait") or {}
    if not portrait.get("file"):
        raise ValueError("Make a portrait for this character, then export its card.")
    npc_id = npc["id"]
    base = _image(_asset(lib, npc_id, portrait["file"], "portrait"), "portrait")
    pictures = {"portrait": portrait}
    lines = _lines(npc)
    selected = voice_selection(npc) if include_voice else None
    if needs_permission(selected) and (permission is not True or form == "png"):
        selected = None
    sheet_uri = living = voice_uri = None
    sources = {}
    archive = _Archive() if form == "charx" else None
    if archive:
        archive.add("assets/icon/images/main.png", base, "icon", "main")
        sheet_uri = archive.add(SHEET, _json(_sheet(npc)), "other", "character-sheet")
        expressions = assets.get("expressions") or {}
        for expr_id, name in EMOTIONS.items():
            meta = expressions.get(expr_id)
            if meta and meta.get("file") and not meta.get("stale"):
                image = _image(_asset(lib, npc_id, meta["file"], f"{name} expression"), f"{name} expression")
                archive.add(f"assets/emotion/images/{name}.png", image, "emotion", name)
                pictures.setdefault("expressions", {})[expr_id] = meta
        sprite = assets.get("sprite") or {}
        if sprite.get("file") and not sprite.get("stale"):
            image = _image(_asset(lib, npc_id, sprite["file"], "full-body sprite"), "full-body sprite")
            archive.add("assets/other/images/sprite.png", image, "other", "sprite")
            pictures["sprite"] = sprite
        living, sources = _living(archive, lib, npc_id, assets.get("living"), base, expressions)
        if selected and selected.get("identity_id"):
            voice, selected = _persona(selected, lines["greeting"][0][:400], permission)
            voice_uri = archive.add(PERSONA, voice, "other", "voice")

    licence = _licence(pictures, sources, selected)
    extra = {
        "contract": fels_suite.CONTRACT, "made_by": {"app": APP, "version": __version__, "id": npc_id},
        "ai_generated": True, "licence": licence, "sheet": sheet_uri, "living_portrait": living,
        "voice": {"name": selected["name"], "model": selected["model"], "preset": selected.get("voice_id"),
                  "file": voice_uri, "cloned_from_person": selected.get("cloned_from_person") is True} if selected else None,
    }
    data = _card_text(npc, lines, licence)
    data.update(extensions={"fels": extra}, assets=archive.assets if archive else [
        {"type": "icon", "name": "main", "ext": "png", "uri": "ccdefault:"}],
        group_only_greetings=[], modification_date=int(time.time()))
    if npc.get("created"):
        data["creation_date"] = int(datetime.datetime.fromisoformat(npc["created"]).timestamp())
    card = {"spec": "chara_card_v3", "spec_version": "3.0", "data": data}
    return archive.finish(card) if archive else _png_card(base, card)
