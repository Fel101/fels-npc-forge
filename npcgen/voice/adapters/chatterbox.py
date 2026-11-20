"""Chatterbox Turbo (Resemble AI, 350M): speaks in a copied voice and understands [laugh] [chuckle] [cough].

It ignores exaggeration and cfg (its own code says so), so tags are its only mood control. RTF about 0.9 on an R9700, 3.3 GB.
Every clip carries Resemble's inaudible Perth watermark. The voice analysis of a reference clip is done once and reused.
"""
import os
import tempfile

from ._torch import free, seed_all, setup, to_numpy


class Engine:
    name = "chatterbox"

    def __init__(self):
        self.torch, self.device = setup()
        self.model = None
        self.ref = None

    def _load(self):
        if self.model is None:
            from chatterbox.tts_turbo import ChatterboxTurboTTS
            local=os.environ.get('CHATTERBOX_MODEL_DIR')
            self.model = ChatterboxTurboTTS.from_local(local,self.device) if local else ChatterboxTurboTTS.from_pretrained(device=self.device)
        return self.model

    def load(self, parts):
        self._load()

    def unload(self):
        self.model = None
        self.ref = None
        free(self.torch)

    def voices(self):
        return []

    def _long_enough(self, ref):
        """Chatterbox refuses a voice sample under 5 s (an assertion in its code). A short one is padded with silence into a
        temporary copy; a take script reads about 8-10 s, so this only matters for short uploads or copies."""
        import numpy as np
        import soundfile as sf
        data, rate = sf.read(str(ref), dtype="float32", always_2d=True)
        if len(data) >= 5.3 * rate:
            return str(ref)
        padded = np.concatenate([data.mean(axis=1), np.zeros(int(5.5 * rate) - len(data), dtype=np.float32)])
        path = os.path.join(tempfile.gettempdir(), f"npcforge_chatterbox_ref_{os.getpid()}.wav")
        sf.write(path, padded, rate)
        return path

    def clone(self, text, ref, ref_text, language, seed, instruct, params):
        model = self._load()
        key = (str(ref), os.path.getmtime(ref))
        if key != self.ref:
            model.prepare_conditionals(self._long_enough(ref))
            self.ref = key
        seed_all(self.torch, seed)
        wav = model.generate(text)
        return to_numpy(wav), model.sr

    def design(self, text, instruct, language, seed, params):
        raise ValueError("Chatterbox Turbo cannot make a voice from a description: design one with Qwen3-TTS or Breeze first")

    def stock(self, text, voice, speed, language, params):
        raise ValueError("Chatterbox Turbo has no stock voices here: give it a voice to copy")
