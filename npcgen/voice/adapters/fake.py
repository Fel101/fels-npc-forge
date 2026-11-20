"""A stand-in engine for tests: no model, no torch. Makes a tone whose length follows the text (about 15 characters a second).

NPCFORGE_FAKE_SLEEP (seconds) makes every clip slow, to test timeouts and cancelling. Text containing "[fail]" raises.
"""
import os
import time

import numpy as np

RATE = 24000


class Engine:
    name = "fake"
    device = "cpu"

    def __init__(self):
        self.loaded = []

    def _tone(self, text, seed, hz):
        if "[fail]" in text:
            raise RuntimeError("the fake engine was asked to fail")
        time.sleep(float(os.environ.get("NPCFORGE_FAKE_SLEEP") or 0))
        seconds = max(0.5, len(text) / 15.0)
        t = np.arange(int(RATE * seconds)) / RATE
        envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)            # a "syllable" rhythm, so the mouth track moves
        rng = np.random.default_rng(int(seed))
        pad = np.zeros(int(RATE * 0.3), dtype=np.float32)            # silence at both ends, so trimming has work to do
        tone = (0.3 * envelope * np.sin(2 * np.pi * (hz + rng.integers(0, 20)) * t)).astype(np.float32)
        return np.concatenate([pad, tone, pad]), RATE

    def load(self, parts):
        self.loaded = list(parts)

    def unload(self):
        self.loaded = []

    def voices(self):
        return ["fake_a", "fake_b"]

    def design(self, text, instruct, language, seed, params):
        return self._tone(text, seed, 220)

    def clone(self, text, ref, ref_text, language, seed, instruct, params):
        if not os.path.isfile(ref):
            raise FileNotFoundError(f"reference clip not found: {ref}")
        return self._tone(text, seed, 330)

    def stock(self, text, voice, speed, language, params):
        return self._tone(text, 0, 440)
