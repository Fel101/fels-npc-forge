"""Pinned Original checkpoint, distinct from Turbo, with effective native controls."""
import hashlib
import importlib.metadata
import os
import sys
from pathlib import Path

from ._torch import free, seed_all, setup, to_numpy

REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"


class Engine:
    name = "chatterbox_original"

    def __init__(self):
        lab = Path(os.environ.get("VOICE_LAB", ""))
        if not (lab / "chatterbox_support.py").is_file():
            raise RuntimeError("VOICE_LAB must point to the pinned Chatterbox runtime")
        sys.path.insert(0, str(lab))
        self.torch, self.device = setup()
        self.model, self.reference = None, None
        self.model_identity = {}

    def load(self, parts):
        if self.model is None:
            from chatterbox.tts import ChatterboxTTS
            lab = Path(os.environ.get("VOICE_LAB", ""))
            path = lab / f"hf-cache/hub/models--ResembleAI--chatterbox/snapshots/{REVISION}"
            if not path.is_dir():
                raise RuntimeError("The pinned Chatterbox Original snapshot is missing")
            self.model = ChatterboxTTS.from_local(path, self.device)
            self.model_identity = {"checkpoint": "ResembleAI/chatterbox", "checkpoint_revision": REVISION,
                "backend": "chatterbox-tts", "backend_version": importlib.metadata.version("chatterbox-tts"),
                "forge_adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "native_watermark": "Perth"}

    def unload(self):
        self.model, self.reference = None, None
        self.model_identity = {}
        free(self.torch)

    def voices(self):
        return []

    def clone(self, text, ref, ref_text, language, seed, instruct, params):
        self.load([])
        if language != "English" or instruct:
            raise ValueError("Original supports English cloning with numeric controls, not text direction")
        from chatterbox_support import normalize_chatterbox_sampling
        controls = normalize_chatterbox_sampling("orig", {key: params[key] for key in
            ("exaggeration", "cfg_weight", "temperature", "top_p", "min_p", "repetition_penalty") if key in params})
        digest = hashlib.sha256(Path(ref).read_bytes()).hexdigest()
        key = (str(ref), digest, controls["exaggeration"])
        if self.reference != key:
            self.model.prepare_conditionals(str(ref), exaggeration=controls["exaggeration"])
            self.reference = key
        seed_all(self.torch, seed)
        result = self.model.generate(text, **controls)
        self.model_identity.update(effective_controls=controls, seed_requested=seed, reference_sha256=digest,
                                   sample_rate=self.model.sr)
        return to_numpy(result), self.model.sr

    def design(self, *args):
        raise ValueError("Original clones a reference; it cannot design a voice from a description")

    def stock(self, *args):
        raise ValueError("Choose a cloning reference for Chatterbox Original in Forge")
