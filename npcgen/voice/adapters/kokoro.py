"""Kokoro 82M (hexgrad): fixed stock voices, no description and no cloning. RTF 0.12-0.30 on an R9700, 1.4 GB.

American voices (a*_) use Kokoro's American English pronunciation, British ones (b*_) its British English.
"""
import os
from pathlib import Path
import numpy as np

from ._torch import free, setup, to_numpy

REPO = "hexgrad/Kokoro-82M"
VOICES = ["af_heart", "af_bella", "af_nicole", "af_aoede", "af_kore", "af_sarah", "af_nova", "af_alloy", "af_sky", "af_jessica",
          "af_river", "am_michael", "am_fenrir", "am_puck", "am_echo", "am_eric", "am_liam", "am_onyx", "am_santa", "am_adam",
          "bf_emma", "bf_isabella", "bf_alice", "bf_lily", "bm_george", "bm_fable", "bm_lewis", "bm_daniel"]
RATE = 24000


class Engine:
    name = "kokoro"

    def __init__(self):
        self.torch, self.device = setup()
        self.pipes = {}
        self.model = None

    def _pipe(self, lang):
        if lang not in self.pipes:
            from kokoro import KPipeline
            local=os.environ.get('KOKORO_MODEL_DIR')
            if local:
                from kokoro import KModel
                root=Path(local)
                if self.model is None:
                    self.model=KModel(repo_id=REPO,config=str(root/'config.json'),model=str(root/'kokoro-v1_0.pth')).to(self.device).eval()
                self.pipes[lang]=KPipeline(lang_code=lang,repo_id=REPO,model=self.model,device=self.device)
            else:self.pipes[lang] = KPipeline(lang_code=lang, repo_id=REPO, device=self.device)
        return self.pipes[lang]

    def load(self, parts):
        self._pipe("a")
        if "all_voices" in (parts or []):            # the installer: fetch every voice now, NPC Forge runs offline later
            for voice in VOICES:
                self._pipe("b" if voice.startswith("b") else "a").load_voice(voice)

    def unload(self):
        self.pipes.clear()
        self.model = None
        free(self.torch)

    def voices(self):
        local=os.environ.get('KOKORO_MODEL_DIR')
        return [voice for voice in VOICES if not local or (Path(local)/'voices'/f'{voice}.pt').is_file()]

    def stock(self, text, voice, speed, language, params):
        if voice not in VOICES:
            raise ValueError(f"Unknown Kokoro voice: {voice}")
        pipe = self._pipe("b" if voice.startswith("b") else "a")
        local=os.environ.get('KOKORO_MODEL_DIR')
        if local:
            path=Path(local)/'voices'/f'{voice}.pt'
            if not path.is_file():raise ValueError(f'{voice} has no embedding in the queued Kokoro checkpoint')
            pipe.voices[voice]=pipe.load_single_voice(str(path))
        parts = [to_numpy(audio) for _gs, _ps, audio in pipe(text, voice=voice, speed=max(0.5, min(2.0, speed)),
                                                               split_pattern=r"\n+") if audio is not None]
        return (np.concatenate(parts) if parts else np.zeros(0, np.float32)), RATE

    def design(self, text, instruct, language, seed, params):
        raise ValueError("Kokoro cannot make a voice from a description: pick one of its stock voices")

    def clone(self, text, ref, ref_text, language, seed, instruct, params):
        raise ValueError("Kokoro cannot copy a voice: pick one of its stock voices")
