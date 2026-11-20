"""A character's voice as a game-ready ZIP ("Voice pack"):

  <name>/voice.wav              cloning reference, or optional preset audition (see file_role)
  <name>/lines/<line id>.wav    every voiced dialogue line that is up to date (16-bit PCM, trimmed, loudness-matched)
  <name>/lines.json             per line: id, category, text (as written and without sound tags), mood, file, seconds, and a
                                mouth-opening track (0..1, 25 values a second) for lip-syncing a talking portrait or sprite
  <name>/dialogue.json, dialogue.yarn   the dialogue pack with the audio files (Yarn: #line:<id> tags)
  <name>/README.txt             what is in it and under which licence the voice was made

WAV, not OGG: Godot and Unreal both import WAV directly, and NPC Forge has no Vorbis encoder of its own.
"""
import io
import json
import zipfile

from .. import dialogue, export
from ..library import now
from . import alignment, audio, character_rig, effects, engines, game_exports, markup, record
from . import mesh_rig


def voiced_lines(npc):
    """[(line id, category, dialogue row, voice_lines meta)] for every voiced line that is still up to date."""
    out = []
    metas = npc.get("voice_lines") or {}
    for category, rows in ((npc.get("dialogue") or {}).get("lines") or {}).items():
        for row in rows:
            meta = metas.get(row["id"])
            if meta and not record.line_is_stale(npc, row["id"]):
                out.append((row["id"], category, row, meta))
    return out


def build(lib, npc):
    """The Voice pack ZIP bytes. Raises ValueError when the character has no voice."""
    v = record.voice_of(npc)
    ref = v.get("reference")
    if not ref and not record.is_preset(v):
        raise ValueError("This character has no voice yet")
    ref = ref or {}
    p = npc["profile"]
    stem = export.slugify(f'{p.get("first_name", "")}-{p.get("last_name", "")}') or npc["id"]
    lines = voiced_lines(npc)
    made_with = sorted({e for e in (v.get("engine"), record.speaker_for(v), ref.get("engine")) if e})
    licences = {e: engines.ENGINES[e]["licence"] for e in made_with if e in engines.ENGINES}
    manifest = {
        "npc": {"id": npc["id"], "name": f'{p.get("first_name", "")} {p.get("last_name", "")}'.strip()},
        "voice": {"file": "voice.wav" if ref.get("file") else None, "mode": v.get("mode"), "engine": v.get("engine"), "speaker": record.speaker_for(v),
                  "description": record.instruction(v) if v.get("mode") == "design" else "", "language": v.get("language"),
                  "stock_voice": v.get("stock_voice"), "commercial_ok": record.commercial_ok(v), "licences": licences},
        "mouth_fps": 1000 // audio.MOUTH_FRAME_MS,
        "lines": [{"id": line_id, "category": category, "text": row["text"], "plain_text": markup.plain(row["text"],meta.get('engine') or record.speaker_for(v)),
                   "mood": meta.get("mood", "neutral"), "file": f"lines/{line_id}.wav", "seconds": meta.get("seconds"),
                   "mouth": meta.get("mouth") or [], "yarn_line": f"line:{line_id}"} for line_id, category, row, meta in lines],
        "exported": now(),
    }
    manifest["voice"].update(reference_version=ref.get("id"), reference_sha256=ref.get("sha256"),
                             library_binding=v.get("library_binding"), reference_provenance=ref, preset_identity=v.get("preset_identity"))
    manifest["voice"].update(identity_version=record.identity_id(v), file_role="audition" if record.is_preset(v) else "cloning_reference")
    for packed, (line_id, _, _, meta) in zip(manifest["lines"], lines):
        packed["reference_version"] = meta.get("reference")
        packed["voice_snapshot"] = meta.get("voice_snapshot")
        for key in ("synthesis_text", "pronunciations_applied", "performance", "render_signature", "synthesis_signature", "model_identity", "effects", "postprocessing", "lip_sync"):
            packed[key] = meta.get(key)
        packed['lip_sync'] = alignment.for_delivery(effects.check_delivery(lib.read_asset(npc['id'], meta['file']), meta), meta, v)
        packed['lip_sync_file'] = f'lip-sync/{line_id}.json'
        if meta.get("original"):
            packed["original"] = {**meta["original"], "file": f"originals/{meta['original']['sha256']}.wav"}
    audio_map = {line_id: f"lines/{line_id}.wav" for line_id, _, _, _ in lines}
    readme = [
        f"Voice pack: {manifest['npc']['name']}",
        "",
        "voice.wav is the character's voice; lines/ holds each voiced dialogue line. lines.json lists them with their text, mood,",
        f"length and a mouth-opening track ({manifest['mouth_fps']} values a second, 0 = closed, 1 = wide open) for lip sync.",
        "dialogue.json and dialogue.yarn are the dialogue pack with the audio files (Yarn: #line:<id> tags).",
        "",
        "Made with: " + ", ".join(f"{engines.ENGINES[e]['label']} ({licences[e]})" for e in made_with if e in licences),
    ]
    if not record.commercial_ok(v):
        readme += ["", "NON-COMMERCIAL: a model used for this voice has a non-commercial licence. Do not ship it in a game you sell."]
    if v.get("mode") == "upload":
        readme += ["", "This voice was copied from a recording you uploaded, confirming you have the right to use it."]
    if record.is_preset(v):
        readme += ["", "This is a reference-free model preset. lines.json stores its speaker/checkpoint identity.",
                   "An optional voice.wav is audition media, never a cloning reference. Rendered lines can be imported directly."]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        if ref.get("file"):
            z.writestr(f"{stem}/voice.wav", lib.read_asset(npc["id"], ref["file"]))
        originals = set()
        for line_id, _, _, meta in lines:
            z.writestr(f"{stem}/lines/{line_id}.wav", effects.check_delivery(lib.read_asset(npc["id"], meta["file"]), meta))
            original = meta.get("original")
            if original and original["sha256"] not in originals:
                z.writestr(f"{stem}/originals/{original['sha256']}.wav", effects.read_original(lib, npc["id"], original))
                originals.add(original["sha256"])
        for line in manifest['lines']:
            z.writestr(f"{stem}/{line['lip_sync_file']}", json.dumps(line['lip_sync'], indent=2, ensure_ascii=False))
            line['game_playback'] = game_exports.write_clip(z, f'{stem}/', line['id'], line['lip_sync'], line['mouth'], line['file'])
        game_exports.write_support(z, f'{stem}/')
        manifest['character_rigs'] = character_rig.write_exports(z, lib, npc, f'{stem}/')
        mesh=mesh_rig.write_export(z,lib,npc,f'{stem}/')
        if mesh:manifest['character_rigs']['mesh']=mesh
        z.writestr(f"{stem}/lines.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        if npc.get("dialogue"):
            z.writestr(f"{stem}/dialogue.json", dialogue.render(npc["dialogue"], "json", audio_map)[0])
            z.writestr(f"{stem}/dialogue.yarn", dialogue.render(npc["dialogue"], "yarn", audio_map)[0])
        z.writestr(f"{stem}/README.txt", "\n".join(readme) + "\n")
    return out.getvalue(), stem
