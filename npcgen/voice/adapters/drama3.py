"""Fish Audio Drama 3 through Voice Lab's pinned portable Vulkan installation."""
import os
from pathlib import Path
import sys


class Engine:
    name = 'drama3'

    def __init__(self):
        self.lab = Path(os.environ['VOICE_LAB']).resolve()
        sys.path.insert(0,str(self.lab))
        from drama3_runtime import SpeechRuntime
        self.runtime = SpeechRuntime(root=self.lab, receipt_path=os.environ.get('DRAMA3_INSTALL_RECEIPT'))
        self.device = self.runtime.device
        self.model_identity = {}

    def load(self, parts):
        if parts == ['clone']:
            self.runtime._select_device(cloning=True)
        self.model_identity = self.runtime.load()
        self.device = self.runtime.device

    def production(self, text, references, description, direction, settings, on_audio):
        samples,rate,self.model_identity=self.runtime.generate(text,references=references,
            description=description,direction=direction,settings=settings,on_audio=on_audio,cpu_only=True)
        self.device=self.runtime.device
        self.model_identity['effective_controls']=self.model_identity['effective_settings']
        return samples,rate

    def unload(self):
        self.runtime.close()
        self.model_identity = {}

    def voices(self):
        return []

    def _generate(self, text, instruct, seed, params, references=None):
        from drama3_runtime import DEFAULTS
        settings = {key:value for key,value in (params or {}).items() if key in DEFAULTS}
        settings['seed'] = int(seed)
        # Forge stores validated integer performance controls as JSON numbers.
        if 'top_k' in settings:
            settings['top_k'] = int(settings['top_k'])
        samples, rate, self.model_identity = self.runtime.generate(text,
            direction=instruct or '',references=references,settings=settings)
        self.device = self.runtime.device
        self.model_identity['effective_controls'] = self.model_identity['effective_settings']
        return samples,rate

    def design(self, text, instruct, language, seed, params):
        return self._generate(text,instruct,seed,params)

    def clone(self, text, ref, ref_text, language, seed, instruct, params):
        return self._generate(text,instruct,seed,params,[{'path':ref,'text':ref_text}])

    def stock(self, *args):
        raise ValueError('Drama 3 has no fixed stock voices; design or clone a voice.')
