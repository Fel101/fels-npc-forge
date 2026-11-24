"""Voice model metadata for markup and existing voice records. Voice Lab owns model availability."""
import pathlib
import json

DEFAULT_ENGINE = "qwen"

# Legacy Forge ids are still used for markup and model licence notes in saved dialogue packs.
DESIGN_MODELS = {"qwen": "qwen3-tts-1.7b-voice-design", "breeze": "breeze-tts-2", "voxcpm2": "voxcpm2", "drama3": "drama-3"}
PRESET_MODELS = {"kokoro": "kokoro", "qwen_custom": "qwen3-tts-1.7b-custom-voice"}

LAB_MODELS = {
    "qwen3-tts-1.7b-voice-design": "qwen",
    "qwen3-tts-1.7b-base": "qwen", "qwen3-tts-0.6b-base": "qwen",
    "qwen3-tts-1.7b-custom-voice": "qwen_custom", "breeze-tts-2": "breeze",
    "turbo": "chatterbox", "orig": "chatterbox_original", "drama-3": "drama3",
}

LANGUAGES_QWEN = ["English", "Chinese", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian"]

ENGINES = {
    "qwen": {
        "label": "Qwen3-TTS 1.7B",
        "caps": {"design": True, "clone": True, "stock": False, "direction": False, "tags": [], "accents": False},
        "languages": LANGUAGES_QWEN,
        "commercial_ok": True, "licence": "Apache-2.0",

        "note": "Makes a voice from your description, clones, speaks 10 languages. Fast (about half the clip's length to "
                "make it) and the most reliable words, even for cartoon and chibi voices. About 8 GB of GPU memory.",
    },
    "breeze": {
        "label": "Breeze TTS 2 (3B)",
        "caps": {"design": True, "clone": True, "stock": False, "direction": True,
                 "tags": ["laugh", "chuckle", "sigh", "gasp", "cough"], "accents": True},
        "languages": ["English", "Chinese"],
        "commercial_ok": False, "licence": "BreezeBlue Research and Non-Commercial License",

        "note": "The most natural-sounding, follows a description closely, laughs and sighs on cue, and can steer a cloned voice's "
                "mood. Slower (about 1.5x the clip's length), 14 GB of GPU memory, English and Chinese. Can garble very "
                "exaggerated cartoon voices. Non-commercial licence: not for a game you sell.",
    },
    "kokoro": {
        "label": "Kokoro (stock voices)",
        "caps": {"design": False, "clone": False, "stock": True, "direction": False, "tags": [], "accents": False},
        "languages": ["English"],
        "commercial_ok": True, "licence": "Apache-2.0",

        "note": "Cached English stock voices (American and British). Only locally available embeddings are offered. Fast and light, but it cannot make a voice from "
                "a description or copy one, and it has no moods.",
    },
    "chatterbox": {
        "label": "Chatterbox Turbo",
        "caps": {"design": False, "clone": True, "stock": False, "direction": False, "tags": ["laugh", "chuckle", "cough"],
                 "accents": False},
        "languages": ["English"],
        "commercial_ok": True, "licence": "MIT (clips carry an inaudible watermark)",

        "note": "Speaks in a copied voice (a designed take, a recording or another character's) and laughs or chuckles on cue. "
                "It cannot make a voice from a description, and moods come only from the tags.",
    },
}

ENGINES["voxcpm2"] = {
    "label": "VoxCPM2 (Voice Lab)", "adapter": "voxcpm2",
    "caps": {"design": True, "clone": True, "stock": False, "direction": False, "tags": [], "accents": False},
    "languages": ["English", "Chinese"], "commercial_ok": True, "licence": "Apache-2.0",

    "note": "Reuses the pinned local Voice Lab source and ROCm overlay. Designs or clones a reference at 48 kHz; "
            "CFG and inference steps are effective. Experimental clone mood direction is not exposed. Language follows the written text; "
            "Forge currently offers English/Chinese. No native streaming is exposed.",
}

ENGINES["qwen_custom"] = {
    "label": "Qwen 1.7B CustomVoice", "adapter": "qwen_custom",
    "caps": {"design": False, "clone": False, "stock": True, "direction": True, "tags": [], "accents": False},
    "languages": ["Auto", *LANGUAGES_QWEN], "commercial_ok": True, "licence": "Apache-2.0",

    "note": "Reference-free preset speakers from the pinned CustomVoice checkpoint, with supported acting direction. "
            "Speaker discovery reads its cached configuration; generation verifies the loaded model's speaker/language list.",
}
ENGINES["chatterbox_original"] = {
    "label": "Chatterbox Original", "adapter": "chatterbox_original",
    "caps": {"design": False, "clone": True, "stock": False, "direction": False, "tags": [], "accents": False},
    "languages": ["English"], "commercial_ok": True, "licence": "MIT (native Perth watermark)",

    "note": "Pinned Original checkpoint with reference cloning and effective exaggeration, CFG weight and sampling. "
            "No text acting-direction, Turbo sound tags or native speed control. Original and Turbo remain distinct.",
}
ENGINES['dramabox'] = {
    'label': 'DramaBox', 'adapter': 'dramabox',
    'caps': {'design': True, 'clone': True, 'stock': False, 'direction': True, 'tags': [], 'accents': False},
    'languages': ['English'], 'commercial_ok': True,
    'licence': 'LTX-2 Community License + Gemma terms', 'licence_conditions': ("Commercial use has conditions. Entities with annual revenue of at least $10 million need a paid LTX license. "
        "Keep license notices and use restrictions."),

    'note': 'Prompted English voice design and reference cloning with acting direction. Uses the verified local '
            'DramaBox/NF4 Gemma installation; about ten seconds per short warm clip in the audition. '
            'Reference: 3–40 seconds, about 10 recommended; the first 30 seconds are used. '
            'Expressive speech may retain lip-sync fallback. Compilation and RE-USE denoising are disabled.',
}
CUSTOM_REPO = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
ENGINES['drama3'] = {
    'label':'Fish Audio Drama 3',
    'caps':{'design':True,'clone':True,'stock':False,'direction':True,
            'tags':['laugh','chuckle','sigh','gasp','cough'],'accents':False},
    'languages':['Auto',*json.loads(pathlib.Path(__file__).with_name('drama3_languages.json').read_text('utf-8')).values()],
    'commercial_ok':True, 'licence':'Fish Audio Community License 1.0',
    'licence_conditions':'Built with Fish Audio. Commercial use is conditional on annual revenue below USD 500,000; '
        'hosted speech or voice-cloning services require a separate Fish Audio agreement. Read the full license.',

    'note':'35B MoE / 3B active, pinned Q4_K conversion through speech.cpp 0.8.10 on Windows Vulkan. '
        'GPU design; CPU cloning with an exact reference transcript after a Vulkan cloning graphics-driver crash. '
        'CPU cloning can take several minutes. Delivery cues are experimental and may be spoken or ignored. '
        'Native event and speaker tags are supported. Language follows the text. No native watermark is added.'}
CUSTOM_REVISION = "0c0e3051f131929182e2c023b9537f8b1c68adfe"
ORIGINAL_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"

# Kokoro's English voices, with the grade its authors give each (A best). Grades order the stock matches; nothing else about
# the voices is claimed here.
STOCK_VOICES = {
    "qwen_custom": [{"id": name, "label": name.replace("_", " ")} for name in
                    ("Serena", "Vivian", "Uncle_Fu", "Ryan", "Aiden", "Ono_Anna", "Sohee", "Eric", "Dylan")],
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


def stock_voices(engine_id):
    return STOCK_VOICES.get(engine_id, [])


def choices():
    """Display metadata for previous Forge records. New voice choices come from GET /api/voice/lab."""
    return [{"id": engine_id, **model, "installed": False, "loaded": False,
             "install_hint": "Choose an available voice from Voice Lab."}
            for engine_id, model in ENGINES.items()]
