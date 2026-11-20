"""Reference-free CustomVoice through the pinned, verified Voice Lab adapter."""
import hashlib
import os
from pathlib import Path
import sys


class Engine:
    name = "qwen_custom"

    def __init__(self):
        lab = Path(os.environ.get("VOICE_LAB", ""))
        if not (lab / "qwen_worker.py").is_file():
            raise RuntimeError("VOICE_LAB must point to the existing pinned Qwen runtime")
        sys.path.insert(0, str(lab))
        from ._torch import setup
        from qwen_worker import QwenWorker
        self.torch, self.device = setup()
        self.worker = QwenWorker("qwen3-tts-1.7b-custom-voice", self.device)
        self.model_identity = {}

    def load(self, parts):
        info = self.worker.load()
        self.model_identity = {key: info.get(key) for key in ("checkpoint", "checkpoint_revision", "backend", "backend_version", "runtime_identity", "supported_speakers", "supported_languages")}
        self.model_identity["forge_adapter_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def unload(self):
        self.worker.unload()
        self.model_identity = {}

    def voices(self):
        self.load([])
        return self.worker.supported_speakers

    def stock(self, text, voice, speed, language, params):
        controls = {key: params[key] for key in ("temperature", "top_k", "top_p", "repetition_penalty", "max_new_tokens") if key in params}
        samples, rate, info = self.worker.generate({"text": text, "mode": "custom_voice", "speaker": voice,
            "language": language, "seed": params.get("seed", 42), "direction": params.get("direction", ""), **controls})
        self.model_identity.update(effective_controls=info["sampling"], speaker=info["speaker"], language=info["language"],
                                   direction=info.get("direction"), reference_required=False)
        return samples, rate

    def design(self, *args):
        raise ValueError("CustomVoice uses a model-provided preset, not a voice description")

    def clone(self, *args):
        raise ValueError("CustomVoice does not accept cloning references; select its saved preset identity")
