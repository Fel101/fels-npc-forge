"""Breeze TTS 2 (BreezeBlue, 3B), through the inference code of the Apache-2.0 ComfyUI node Saganaki22/ComfyUI-Breeze-TTS-2.

That code is not part of NPC Forge: BREEZE_CODE names the folder that holds it as a package called `breeze` (loader.py,
native.py, runtime.py, int8.py, vendor/; scripts/install_voice.py sets this up). It runs without ComfyUI: every comfy import is
optional, attention "sdpa" is plain PyTorch, and "cuda_graphs" decode records the depth decoder's 15 small steps per audio
frame once and replays them (HIP graphs on ROCm). Measured on an R9700: RTF about 1.5 with cuda_graphs (about 4 without),
14 GB of GPU memory. The weights are under BreezeBlue's non-commercial licence.

Three ways to pick the voice, as in the official infer.py:
  design          a written description ("voice design"), cfg 4, the same text without it as the CFG negative;
  clone           a reference clip and its exact transcript, cfg 1;
  clone + instruct   that voice steered by an instruction ("voice direction"), cfg 4, the plain clone as the negative.

Environment: BREEZE_CODE, BREEZE_MODELS (optional: the folder holding breezetts2/<repo>/), BREEZE_DECODE (cuda_graphs|eager).
"""
import os
import pathlib
import sys

from ._torch import free, seed_all, setup

SAMPLE_CFG = 4.0


class Engine:
    name = "breeze"

    def __init__(self):
        self.torch, self.device = setup()
        code = os.environ.get("BREEZE_CODE")
        if not code or not (pathlib.Path(code) / "breeze" / "loader.py").is_file():
            raise RuntimeError("BREEZE_CODE must name the folder holding the Breeze inference package (breeze/loader.py); "
                               "run scripts/install_voice.py breeze")
        if code not in sys.path:
            sys.path.insert(0, code)
        from breeze import loader, native, runtime
        self.loader, self.native, self.runtime = loader, native, runtime
        models = os.environ.get("BREEZE_MODELS")
        if models:
            loader.model_dirs = lambda: [pathlib.Path(models) / loader.MODEL_FOLDER_NAME]
        selected=os.environ.get('BREEZE_MODEL_DIR')
        if selected:
            root=pathlib.Path(selected)
            loader.resolve_model_dir=lambda repo_choice,download_if_missing:(root,'Breeze-TTS-2-bf16.safetensors')
        self.bundle = None
        self.refs = {}                                   # reference clip path -> (mtime, codec codes)

    def _load(self):
        if self.bundle is None:
            decode = os.environ.get("BREEZE_DECODE") or ("cuda_graphs" if self.device == "cuda" else "eager")
            self.bundle = self.loader.load_breeze_bundle(repo_choice=self.loader.BF16_LABEL, dtype_name="auto",
                                                         device_name=self.device, attention_choice="sdpa",
                                                         download_if_missing=os.environ.get("HF_HUB_OFFLINE") != "1",
                                                         decode_mode=decode)
        return self.bundle

    def load(self, parts):
        self._load()

    def unload(self):
        if self.bundle is not None:
            try:
                self.loader.unload_breeze_bundle(self.bundle, reason="worker unload", hard=True)
            except Exception:
                pass
        self.bundle = None
        self.refs.clear()
        free(self.torch)

    def voices(self):
        return []

    def _codes(self, ref):
        import numpy as np
        import soundfile as sf
        mtime = os.path.getmtime(ref)
        hit = self.refs.get(ref)
        if hit and hit[0] == mtime:
            return hit[1]
        data, rate = sf.read(str(ref), dtype="float32", always_2d=True)
        wav = self.torch.from_numpy(np.ascontiguousarray(data.mean(axis=1)))
        with self.torch.inference_mode():
            codes = self.runtime.encode_reference_audio(self._load().codec, wav, rate)
        self.refs[ref] = (mtime, codes)
        return codes

    def _generate(self, cond, negative, ref_codes, cfg, seed, params):
        bundle, rt = self._load(), self.runtime
        seed_all(self.torch, seed)
        rt.fix_seed(int(seed) % (2 ** 31))
        embeds, mask, positions, prefill = rt.build_generation_batch(
            bundle.model, bundle.tokenizer, cond_segments=cond, negative_segments=negative if cfg != 1.0 else None,
            ref_codes=ref_codes, cfg_scale=float(cfg), device=bundle.device)
        frames = min(1500, rt.MAX_SEQ_LEN - 1 - prefill)
        if frames < 64:
            raise ValueError("The text and voice prompt are too long for Breeze TTS 2: use a shorter line or reference clip")
        temp = float(params.get("temperature", 0.9))
        gen = rt.GenerationParams(max_new_tokens=frames, temperature=temp, top_k=50, top_p=1.0, repetition_penalty=1.1,
                                  depth_temperature=temp, depth_top_k=50, depth_top_p=1.0)
        with self.torch.inference_mode(), self.native.attention_runtime(bundle.attention):
            codes = rt.generate_codes(bundle.model, inputs_embeds=embeds, attention_mask=mask, base_positions=positions,
                                      prefill_len=prefill, cfg_scale=float(cfg), params=gen, progress_callback=None,
                                      decode_mode=bundle.decode_mode)
            wav = rt.decode_codes(bundle.codec, codes)
        if wav.numel() == 0 or not bool(self.torch.isfinite(wav).all()):
            raise RuntimeError("Breeze TTS 2 produced no usable audio")
        return wav.numpy().reshape(-1), rt.SAMPLE_RATE

    def design(self, text, instruct, language, seed, params):
        rt = self.runtime
        cfg = float(params.get("cfg", SAMPLE_CFG))
        return self._generate(rt.design_segments(text, instruct), rt.design_negative_segments(text), None, cfg, seed, params)

    def clone(self, text, ref, ref_text, language, seed, instruct, params):
        rt = self.runtime
        if not ref_text:
            raise ValueError("Breeze TTS 2 needs the reference clip's exact words to copy a voice")
        codes = self._codes(str(ref))
        if instruct:
            cond = rt.ref_segments(ref_text, text, instruct, with_instruction=True)
            negative = rt.ref_segments(ref_text, text, "", with_instruction=False)
            return self._generate(cond, negative, codes, float(params.get("cfg", SAMPLE_CFG)), seed, params)
        return self._generate(rt.ref_segments(ref_text, text, "", with_instruction=False), None, codes, 1.0, seed, params)

    def stock(self, text, voice, speed, language, params):
        raise ValueError("Breeze TTS 2 has no stock voices: describe one or clone one")
