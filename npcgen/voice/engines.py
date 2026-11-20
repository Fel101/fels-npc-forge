"""The voice engines: what each text-to-speech model can do, how fast it is, what it needs and how it may be used.

Measured on a Radeon AI PRO R9700 (32 GB) under Windows/ROCm, 22-24 Sep 2026 (see the voice-lab bake-offs). RTF = time to
make a clip / its length; under 1 is faster than speech.

  qwen        Qwen3-TTS 1.7B through faster-qwen3-tts (graph replay). A new voice from a written description at RTF 0.43-0.57,
              cloning at 0.5-0.7, first streamed sound after 0.55 s, 8 GB peak. Ten languages. About 3% of words wrong even
              for cartoon and chibi voices. Apache-2.0. It IGNORES ACCENTS in a description (2026-09-24: 69 takes, British,
              Japanese, French and Scottish in five wordings, plus the language set to Japanese or French: always clean
              American English, and Whisper's language check heard no accent in any). Cloning an accented take keeps a
              lighter accent with every word clear.
  breeze      Breeze TTS 2 (3B), graph decode. The top-rated open model for naturalness (Artificial Analysis arena), sound
              events such as (laugh), trained to steer a cloned voice ("voice direction"). RTF about 1.5, 14 GB peak, English
              and Chinese. Garbled an exaggerated cartoon voice (9% of words wrong on average). Weights non-commercial.
              Does accents when the description leads with one and spells it out (describe.compose): a Japanese accent
              read as Japanese to Whisper (P 0.8) where the old wording read plain English. Strong accents can blur words.
  kokoro      Kokoro 82M: fixed stock voices, no description or cloning. RTF 0.12-0.30, 1.4 GB. Apache-2.0.
  chatterbox  Chatterbox Turbo (350M): clones a voice sample, [laugh] [chuckle] [cough] tags. RTF about 0.9, 3.3 GB. It
              ignores its exaggeration and cfg settings (its own code says so), so its only mood control is the tags. MIT;
              every clip carries Resemble's inaudible Perth watermark.

Where each engine's Python lives comes from config.json ("voice" -> "engines" -> id -> "python", "env"); an engine without a
working Python is listed as not installed.
"""
import pathlib
import json

DEFAULT_ENGINE = "qwen"

LANGUAGES_QWEN = ["English", "Chinese", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian"]

ENGINES = {
    "qwen": {
        "label": "Qwen3-TTS 1.7B",
        "adapter": "qwen",
        "caps": {"design": True, "clone": True, "stock": False, "direction": False, "tags": [], "accents": False},
        "languages": LANGUAGES_QWEN,
        "commercial_ok": True, "licence": "Apache-2.0",
        "min_free_vram_mb": 9000, "min_free_ram_mb": 8000, "rtf": 0.5,
        "separate_parts": True,                 # VoiceDesign and Base are two models: one is unloaded before the other loads
        "note": "Makes a voice from your description, clones, speaks 10 languages. Fast (about half the clip's length to "
                "make it) and the most reliable words, even for cartoon and chibi voices. About 8 GB of GPU memory.",
    },
    "breeze": {
        "label": "Breeze TTS 2 (3B)",
        "adapter": "breeze",
        "caps": {"design": True, "clone": True, "stock": False, "direction": True,
                 "tags": ["laugh", "chuckle", "sigh", "gasp", "cough"], "accents": True},
        "languages": ["English", "Chinese"],
        "commercial_ok": False, "licence": "BreezeBlue Research and Non-Commercial License",
        "min_free_vram_mb": 16000, "min_free_ram_mb": 12000, "rtf": 1.5,
        "note": "The most natural-sounding, follows a description closely, laughs and sighs on cue, and can steer a cloned voice's "
                "mood. Slower (about 1.5x the clip's length), 14 GB of GPU memory, English and Chinese. Can garble very "
                "exaggerated cartoon voices. Non-commercial licence: not for a game you sell.",
    },
    "kokoro": {
        "label": "Kokoro (stock voices)",
        "adapter": "kokoro",
        "caps": {"design": False, "clone": False, "stock": True, "direction": False, "tags": [], "accents": False},
        "languages": ["English"],
        "commercial_ok": True, "licence": "Apache-2.0",
        "min_free_vram_mb": 2500, "min_free_ram_mb": 4000, "rtf": 0.2,
        "note": "Cached English stock voices (American and British). Only locally available embeddings are offered. Fast and light, but it cannot make a voice from "
                "a description or copy one, and it has no moods.",
    },
    "chatterbox": {
        "label": "Chatterbox Turbo",
        "adapter": "chatterbox",
        "caps": {"design": False, "clone": True, "stock": False, "direction": False, "tags": ["laugh", "chuckle", "cough"],
                 "accents": False},
        "languages": ["English"],
        "commercial_ok": True, "licence": "MIT (clips carry an inaudible watermark)",
        "min_free_vram_mb": 5000, "min_free_ram_mb": 6000, "rtf": 0.9,
        "note": "Speaks in a copied voice (a designed take, a recording or another character's) and laughs or chuckles on cue. "
                "It cannot make a voice from a description, and moods come only from the tags.",
    },
}

ENGINES["voxcpm2"] = {
    "label": "VoxCPM2 (Voice Lab)", "adapter": "voxcpm2",
    "caps": {"design": True, "clone": True, "stock": False, "direction": False, "tags": [], "accents": False},
    "languages": ["English", "Chinese"], "commercial_ok": True, "licence": "Apache-2.0",
    "min_free_vram_mb": 9000, "min_free_ram_mb": 10000, "rtf": 4.,
    "note": "Reuses the pinned local Voice Lab source and ROCm overlay. Designs or clones a reference at 48 kHz; "
            "CFG and inference steps are effective. Experimental clone mood direction is not exposed. Language follows the written text; "
            "Forge currently offers English/Chinese. No native streaming is exposed.",
}

ENGINES["qwen_custom"] = {
    "label": "Qwen 1.7B CustomVoice", "adapter": "qwen_custom",
    "caps": {"design": False, "clone": False, "stock": True, "direction": True, "tags": [], "accents": False},
    "languages": ["Auto", *LANGUAGES_QWEN], "commercial_ok": True, "licence": "Apache-2.0",
    "min_free_vram_mb": 9000, "min_free_ram_mb": 10000, "rtf": .6,
    "note": "Reference-free preset speakers from the pinned CustomVoice checkpoint, with supported acting direction. "
            "Speaker discovery reads its cached configuration; generation verifies the loaded model's speaker/language list.",
}
ENGINES["chatterbox_original"] = {
    "label": "Chatterbox Original", "adapter": "chatterbox_original",
    "caps": {"design": False, "clone": True, "stock": False, "direction": False, "tags": [], "accents": False},
    "languages": ["English"], "commercial_ok": True, "licence": "MIT (native Perth watermark)",
    "min_free_vram_mb": 6000, "min_free_ram_mb": 10000, "rtf": 2.,
    "note": "Pinned Original checkpoint with reference cloning and effective exaggeration, CFG weight and sampling. "
            "No text acting-direction, Turbo sound tags or native speed control. Original and Turbo remain distinct.",
}
LAB_IDS = {"voxcpm2": "voxcpm2", "qwen_custom": "qwen3-tts-1.7b-custom-voice", "chatterbox_original": "orig"}
from . import dramabox_runtime
ENGINES['dramabox'] = {
    'label': 'DramaBox', 'adapter': 'dramabox',
    'caps': {'design': True, 'clone': True, 'stock': False, 'direction': True, 'tags': [], 'accents': False},
    'languages': ['English'], 'commercial_ok': True,
    'licence': 'LTX-2 Community License + Gemma terms', 'licence_conditions': dramabox_runtime.LICENCE_CONDITIONS,
    'min_free_vram_mb': 23000, 'min_free_ram_mb': 16000, 'rtf': 2.,
    'note': 'Prompted English voice design and reference cloning with acting direction. Uses the verified local '
            'DramaBox/NF4 Gemma installation; about ten seconds per short warm clip in the audition. '
            'Reference: 3–40 seconds, about 10 recommended; the first 30 seconds are used. '
            'Expressive speech may retain lip-sync fallback. Compilation and RE-USE denoising are disabled.',
}
CUSTOM_REPO = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
ENGINES['drama3'] = {
    'label':'Fish Audio Drama 3', 'adapter':'drama3',
    'caps':{'design':True,'clone':True,'stock':False,'direction':True,
            'tags':['laugh','chuckle','sigh','gasp','cough'],'accents':False},
    'languages':['Auto',*json.loads(pathlib.Path(__file__).with_name('drama3_languages.json').read_text('utf-8')).values()],
    'commercial_ok':True, 'licence':'Fish Audio Community License 1.0',
    'licence_conditions':'Built with Fish Audio. Commercial use is conditional on annual revenue below USD 500,000; '
        'hosted speech or voice-cloning services require a separate Fish Audio agreement. Read the full license.',
    'min_free_vram_mb':25000,'min_free_ram_mb':26000,'rtf':4.,
    'note':'35B MoE / 3B active, pinned Q4_K conversion through speech.cpp 0.8.10 on Windows Vulkan. '
        'GPU design; CPU cloning with an exact reference transcript after a Vulkan cloning graphics-driver crash. '
        'CPU cloning can take several minutes. Delivery cues are experimental and may be spoken or ignored. '
        'Native event and speaker tags are supported. Language follows the text. No native watermark is added.'}
CUSTOM_REVISION = "0c0e3051f131929182e2c023b9537f8b1c68adfe"
ORIGINAL_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"

# Kokoro's English voices, with the grade its authors give each (A best). Grades order the stock matches; nothing else about
# the voices is claimed here.
STOCK_VOICES = {
    "kokoro": [
        {"id": vid, "label": vid.split("_", 1)[1].capitalize(), "gender": "female" if vid[1] == "f" else "male",
         "accent": "American" if vid[0] == "a" else "British", "grade": grade}
        for vid, grade in [
            ("af_heart", "A"), ("af_bella", "A-"), ("af_nicole", "B-"), ("af_aoede", "C+"), ("af_kore", "C+"),
            ("af_sarah", "C+"), ("af_nova", "C"), ("af_alloy", "C"), ("af_sky", "C-"), ("af_jessica", "D"), ("af_river", "D"),
            ("am_michael", "C+"), ("am_fenrir", "C+"), ("am_puck", "C+"), ("am_echo", "D"), ("am_eric", "D"), ("am_liam", "D"),
            ("am_onyx", "D"), ("am_santa", "D"), ("am_adam", "F+"),
            ("bf_emma", "B-"), ("bf_isabella", "C"), ("bf_alice", "D"), ("bf_lily", "D"),
            ("bm_george", "C"), ("bm_fable", "C"), ("bm_lewis", "D+"), ("bm_daniel", "D"),
        ]
    ],
}

# Written without pronouns: they are dropped into "{Subject} sounds ..." and "Speak ..." for anyone.
MOODS = {
    "neutral": "",
    "happy": "happy and warm, smiling while speaking",
    "excited": "excited and energetic",
    "sad": "sad and quiet, close to tears",
    "angry": "angry and sharp",
    "surprised": "surprised, caught off guard",
    "scared": "scared, the voice shaking",
    "whisper": "whispering softly, very close",
    "flirty": "playful and flirtatious",
    "tired": "tired and drowsy",
}

# The neutral markup the dialogue lines use; each adapter turns it into its own syntax or drops it.
TAGS = ["laugh", "chuckle", "sigh", "gasp", "cough"]


def spec(engine_id):
    return ENGINES.get(engine_id)


def clean(engine_id, default=DEFAULT_ENGINE):
    return engine_id if isinstance(engine_id, str) and engine_id in ENGINES else default


def can(engine_id, capability):
    s = ENGINES.get(engine_id)
    return bool(s and s["caps"].get(capability))


def lab_root(cfg):
    voice = (cfg or {}).get("voice") or {}
    root = voice.get("lab_path")
    if not root:
        python = (voice.get("engines") or {}).get("qwen", {}).get("python")
        root = str(pathlib.Path(python).parent.parent.parent) if python else ""
    lab = pathlib.Path(root) if root else None
    return lab.resolve() if lab and (lab / "catalog/engines.json").is_file() else None


def stock_voices(engine_id, cfg=None):
    if engine_id=='kokoro' and cfg is not None:
        from .binding import available_stock
        return available_stock(cfg)
    if engine_id != "qwen_custom":
        return STOCK_VOICES.get(engine_id, [])
    if cfg is None:
        from ..config import load
        cfg = load()
    lab = lab_root(cfg)
    if not lab:
        return []
    path = lab / f"hf-cache/hub/models--Qwen--Qwen3-TTS-12Hz-1.7B-CustomVoice/snapshots/{CUSTOM_REVISION}/config.json"
    try:
        talker = json.loads(path.read_text("utf-8"))["talker_config"]
        return [{"id": "_".join(part.capitalize() for part in key.split("_")),
                 "label": key.replace('_', ' ').title(), "checkpoint": CUSTOM_REPO, "revision": CUSTOM_REVISION}
                for key in talker["spk_id"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def engine_config(cfg, engine_id):
    """{"python": path or "", "env": {...}} for an engine, from config.json's "voice" section."""
    voice = (cfg or {}).get("voice") or {}
    row = (voice.get("engines") or {}).get(engine_id) or {}
    if engine_id == 'drama3':
        lab = lab_root(cfg)
        if lab:
            default = {'python':str(lab/'.venv/Scripts/python.exe'),
                'env':{'VOICE_LAB':str(lab),'DRAMA3_INSTALL_RECEIPT':str(lab/'out/drama3/installation.json')}}
            row = {**default,**row,'env':{**default['env'],**row.get('env',{})}}
    if engine_id == 'dramabox':
        default = dramabox_runtime.defaults(lab_root(cfg))
        row = {**default, **row, 'env': {**default['env'], **row.get('env', {})}}
    if engine_id in LAB_IDS and not row:
        lab = lab_root(cfg)
        if lab:
            try:
                entry = next(e for e in json.loads((lab / "catalog/engines.json").read_text("utf-8"))["engines"] if e["id"] == LAB_IDS[engine_id])
                row = {"python": str(lab / entry["runtime"]["python"]), "env": {
                    "VOICE_LAB": str(lab.resolve()), "HF_HOME": str(lab / "hf-cache"), "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1", "HIP_VISIBLE_DEVICES": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}}
            except (ValueError, KeyError, StopIteration, OSError):
                pass
    return {"python": str(row.get("python") or ""), "env": {str(k): str(v) for k, v in (row.get("env") or {}).items()}}


def installed(cfg, engine_id):
    """True when config.json names a Python for this engine and that file exists."""
    conf = engine_config(cfg, engine_id)
    py = conf["python"]
    if not py or not pathlib.Path(py).is_file():
        return False
    if engine_id == 'drama3':
        try:
            receipt=pathlib.Path(conf['env']['DRAMA3_INSTALL_RECEIPT'])
            data=json.loads(receipt.read_text('utf-8'))
            return (pathlib.Path(data['model_path']).is_file() and pathlib.Path(data['model_path']).stat().st_size==20787264352
                    and pathlib.Path(data['runtime_path']).is_file())
        except (OSError,ValueError,KeyError):
            return False
    if engine_id == "voxcpm2":
        lab = pathlib.Path(conf["env"].get("VOICE_LAB", ""))
        return all((lab / rel).exists() for rel in ("voxcpm_worker.py", ".venv-fqwen/Lib/site-packages/torch",
            "out/m6/source/src/voxcpm", "hf-cache/models--openbmb--VoxCPM2/snapshots/32279effe8c19989596f05d353d1447f51d9e915/config.json"))
    if engine_id in ("qwen_custom", "chatterbox_original"):
        lab = pathlib.Path(conf["env"].get("VOICE_LAB", ""))
        if engine_id == "qwen_custom":
            snapshot = lab / f"hf-cache/hub/models--Qwen--Qwen3-TTS-12Hz-1.7B-CustomVoice/snapshots/{CUSTOM_REVISION}"
            return bool(stock_voices(engine_id, cfg)) and (lab / "qwen_worker.py").is_file() and all(
                (snapshot / name).is_file() for name in ("model.safetensors", "generation_config.json", "tokenizer_config.json",
                    "vocab.json", "merges.txt", "speech_tokenizer/config.json", "speech_tokenizer/model.safetensors"))
        snapshot = lab / f"hf-cache/hub/models--ResembleAI--chatterbox/snapshots/{ORIGINAL_REVISION}"
        return (lab / "chatterbox_support.py").is_file() and all((snapshot / name).is_file() for name in
            ("ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"))
    return True


def choices(cfg, status=None):
    """What the designer lists: every engine with its capabilities and whether it can run here."""
    status = status or {}
    rows = []
    for engine_id, s in ENGINES.items():
        from .binding import discover
        local=discover(cfg,engine_id)
        caps=dict(s['caps'])
        if local['parts']:
            for part in ('design','clone','stock'):
                if part in local['parts']:caps[part]=bool(caps.get(part) and local['parts'][part]['available'])
            if engine_id=='kokoro':caps['stock']=bool(caps['stock'] and stock_voices(engine_id,cfg))
        rows.append({
            "id": engine_id, "label": s["label"], "note": s["note"], "caps": caps, "languages": s["languages"],
            "commercial_ok": s["commercial_ok"], "licence": s["licence"], "rtf": s["rtf"],
            **({'licence_conditions': s['licence_conditions']} if s.get('licence_conditions') else {}),
            "installed": installed(cfg, engine_id) and local['status']!='unavailable', "loaded": bool(status.get(engine_id)),
            "runtime_binding": {'schema':1,'status':local['status'],'parts':{part:{key:value for key,value in row.items() if key not in ('directory','variable')} for part,row in local['parts'].items()}},
            "install_hint": ("Run Voice Lab tools/install_drama3.py; Forge reuses its verified local model and portable runtime (docs/voice-drama3.md)" if engine_id == 'drama3' else
                             "Restore the verified local DramaBox installation or set voice.engines.dramabox paths (docs/voice-dramabox.md)" if engine_id == 'dramabox' else
                             "Set voice.lab_path to the existing pinned Voice Lab installation" if engine_id in LAB_IDS
                             else f"python scripts/install_voice.py {engine_id}"),
        })
    return rows
