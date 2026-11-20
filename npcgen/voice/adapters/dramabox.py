"""Prompted DramaBox design/cloning using the verified offline AMD installation."""
import hashlib
import math
import os
from pathlib import Path
import sys
import wave

from .. import dramabox_runtime
from ._torch import free, seed_all, setup

LIMITS = {'cfg_scale': (1., 5., 2.5), 'stg_scale': (0., 3., 1.5), 'duration_multiplier': (.8, 1.5, 1.1)}


def controls(params):
    params = params or {}
    result = {}
    for name, (lo, hi, default) in LIMITS.items():
        value = params.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f'DramaBox {name} must be a finite number from {lo:g} to {hi:g}')
        result[name] = float(value)
    return result


def prompt(text, instruct=None, clone=False):
    # Keep the speech in one quoted group; user quotes must not become stage cues.
    spoken = str(text).strip().replace('"', '’')
    if not spoken: raise ValueError('DramaBox needs nonempty dialogue')
    direction = (instruct or '').strip().replace('"', '’')
    prefix = 'The reference speaker' if clone else 'An adult speaker'
    return f'{prefix}. {direction or "Speaks naturally."} "{spoken}"'


class Engine:
    name = 'dramabox'

    def __init__(self):
        self.source, self.model_dir, self.gemma_dir = dramabox_runtime.assets(os.environ)
        for site in os.environ.get('DRAMABOX_SITE_OVERLAYS', '').split(os.pathsep):
            if site and site not in sys.path: sys.path.append(site)
        sys.path[:0] = [str(self.source / 'src'), str(self.source / 'ltx2')]
        self.torch, self.device = setup()
        if self.device != 'cuda': raise RuntimeError('DramaBox requires a working GPU runtime')
        self.server = None
        self.watermarker = None
        self.model_identity = {}

    def load(self, parts):
        if self.server is not None: return
        from inference_server import TTSServer
        import perth
        self.server = TTSServer(checkpoint=str(self.model_dir / 'dramabox-dit-v1.safetensors'),
            full_checkpoint=str(self.model_dir / 'dramabox-audio-components.safetensors'),
            gemma_root=str(self.gemma_dir), device=self.device, dtype='bf16', compile_model=False, bnb_4bit=True)
        self.watermarker = perth.PerthImplicitWatermarker()
        self.model_identity = {'backend': 'DramaBox', 'backend_revision': dramabox_runtime.source_revision(self.source),
            'native_watermark': 'Perth', 'compile': False, 'denoise_reference': False,
            'inference_steps': 30, 'sample_rate': 48000}

    def unload(self):
        self.server = self.watermarker = None
        self.model_identity = {}
        free(self.torch)

    def voices(self): return []

    def _generate(self, text, instruct, language, seed, params, ref=None):
        if language != 'English': raise ValueError('DramaBox currently supports English only')
        effective = controls(params)
        seed_all(self.torch, seed)
        request = {'prompt': prompt(text, instruct, clone=ref is not None), 'seed': int(seed),
                   'denoise_ref': False, **effective}
        reference = {}
        if ref is not None:
            path = Path(ref)
            with wave.open(str(path)) as reader: seconds = reader.getnframes() / reader.getframerate()
            if seconds < 3 or seconds > 40: raise ValueError('DramaBox needs a 3–40 second reference; about 10 seconds is recommended')
            request.update(voice_ref=str(path), ref_duration=min(30., seconds))
            reference = {'reference_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'reference_seconds': seconds, 'reference_seconds_used': min(30., seconds)}
        self.load([])
        from inference_server import estimate_duration
        if estimate_duration(request['prompt'], effective['duration_multiplier']) > 40:
            waveform, rate = self.server.generate_long(max_chunk_duration=40, target_chunk_duration=30, **request)
        else:
            waveform, rate = self.server.generate(**request)
        import numpy as np
        native = waveform.detach().cpu().float().numpy()
        samples = np.asarray(self.watermarker.apply_watermark(native.mean(axis=0), sample_rate=rate), dtype=np.float32)
        if samples.ndim != 1 or not len(samples) or not np.isfinite(samples).all():
            raise RuntimeError('DramaBox returned invalid delivered speech')
        for key in ('reference_sha256', 'reference_seconds', 'reference_seconds_used'):
            self.model_identity.pop(key, None)
        self.model_identity.update(effective_controls=effective, seed_requested=int(seed),
            performance_prompt=request['prompt'], sample_rate=int(rate), watermark_applied=True, **reference)
        return samples, rate

    def design(self, text, instruct, language, seed, params):
        return self._generate(text, instruct, language, seed, params)

    def clone(self, text, ref, ref_text, language, seed, instruct, params):
        return self._generate(text, instruct, language, seed, params, ref=ref)

    def stock(self, *args): raise ValueError('DramaBox has no stock voices')
