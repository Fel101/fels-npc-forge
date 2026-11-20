"""Qwen3-TTS through faster-qwen3-tts (graph replay: the GPU work of one audio frame is recorded once and replayed).

Two models, each loaded when first needed and kept: VoiceDesign 1.7B (a voice from a description) and Base 1.7B (cloning a
reference clip). Measured on an R9700: design RTF 0.43-0.57, clone 0.5-0.7, about 5 GB each. The stock qwen-tts package without
graph replay ran at RTF about 3, so this package is the one to use.

Environment: QWEN_DESIGN_MODEL / QWEN_CLONE_MODEL (Hugging Face ids or local folders), HF_HOME, HF_HUB_OFFLINE.
"""
import os
import pathlib

from ._torch import free, seed_all, setup, to_numpy

DESIGN_REPO = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
CLONE_REPO = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"


def _fix_rope():
    """qwen-tts-hf registers a RoPE initialiser that reads config.rope_theta, which the codec's MimiConfig no longer has on
    transformers 5.15+ (it moved to config.rope_parameters): loading failed with AttributeError. Register the same function
    reading either place. Library files are left untouched."""
    import torch
    import qwen_tts  # noqa: F401  (registers the original first, so this one replaces it)
    from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS

    def default_rope(config, device=None, seq_len=None, layer_type=None):
        base = getattr(config, "rope_theta", None)
        if base is None:
            base = (getattr(config, "rope_parameters", None) or {}).get("rope_theta", 10000.0)
        partial = getattr(config, "partial_rotary_factor", 1.0)
        head_dim = getattr(config, "head_dim", None) or config.hidden_size // config.num_attention_heads
        dim = int(head_dim * partial)
        steps = torch.arange(0, dim, 2, dtype=torch.int64).to(device=device, dtype=torch.float)
        return 1.0 / (base ** (steps / dim)), 1.0

    ROPE_INIT_FUNCTIONS["default"] = default_rope


def _local(repo):
    if pathlib.Path(repo).is_dir():
        return repo
    from huggingface_hub import snapshot_download
    offline = os.environ.get("HF_HUB_OFFLINE") in ("1", "true", "True")
    # A local folder, not the repo id: with an id, transformers' tokenizer loader asks the Hub about it even offline.
    return snapshot_download(repo, local_files_only=offline)


class Engine:
    name = "qwen"

    def __init__(self):
        self.torch, self.device = setup()
        _fix_rope()
        self.models = {}

    def _model(self, kind):
        if kind not in self.models:
            from faster_qwen3_tts import FasterQwen3TTS
            repo = os.environ.get("QWEN_DESIGN_MODEL" if kind == "design" else "QWEN_CLONE_MODEL") or \
                (DESIGN_REPO if kind == "design" else CLONE_REPO)
            model = FasterQwen3TTS.from_pretrained(_local(repo), device=self.device, dtype=self.torch.bfloat16,
                                                   attn_implementation="sdpa")
            model.warmup(prefill_len=100)
            self.models[kind] = model
        return self.models[kind]

    def load(self, parts):
        for kind in parts or ["design"]:
            if kind in ("design", "clone"):
                self._model(kind)

    def unload(self):
        self.models.clear()
        free(self.torch)

    def voices(self):
        return []

    def design(self, text, instruct, language, seed, params):
        model = self._model("design")
        seed_all(self.torch, seed)
        wavs, rate = model.generate_voice_design(text=text, instruct=instruct, language=language,
                                                 temperature=float(params.get("temperature", 0.9)))
        return to_numpy(wavs[0]), rate

    def clone(self, text, ref, ref_text, language, seed, instruct, params):
        model = self._model("clone")
        seed_all(self.torch, seed)
        kwargs = {"instruct": instruct} if instruct else {}
        wavs, rate = model.generate_voice_clone(text=text, language=language, ref_audio=str(ref), ref_text=ref_text or "",
                                                xvec_only=not ref_text, temperature=float(params.get("temperature", 0.9)),
                                                **kwargs)
        return to_numpy(wavs[0]), rate

    def stock(self, text, voice, speed, language, params):
        raise ValueError("Qwen3-TTS has no stock voices: describe one or clone one")
