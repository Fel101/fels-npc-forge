"""Bridge to the locally verified VoxCPM2 worker; existing environments are read-only."""
import os
import hashlib
from pathlib import Path
import sys


class Engine:
    name = "voxcpm2"

    def __init__(self):
        lab = Path(os.environ.get("VOICE_LAB", ""))
        if not (lab / "voxcpm_worker.py").is_file():
            raise RuntimeError("VOICE_LAB must point to the pinned local Voice Lab installation")
        # The dedicated VoxCPM environment uses this existing ROCm wheel overlay.
        overlay = lab / ".venv-fqwen/Lib/site-packages"
        sys.path.insert(0, str(lab))
        sys.path.append(str(overlay))
        from voxcpm_worker import VoxCPMWorker
        from ._torch import setup
        self.torch, self.device = setup()

        class ForgeVoxCPM(VoxCPMWorker):
            @staticmethod
            def _reference(path):
                # Forge resolves NPC-library containment and SHA-256 before sending
                # a clone request. Do not apply the separate Lab Cast root list.
                reference = Path(path).resolve()
                if not reference.is_file() or reference.suffix.lower() != ".wav":
                    raise ValueError("Forge VoxCPM2 requires an existing pinned WAV reference")
                return reference

        self.worker = ForgeVoxCPM(self.device)
        self.model_identity = {}

    def load(self, parts):
        info = self.worker.load()
        self.model_identity = {key: info.get(key) for key in ("checkpoint", "checkpoint_revision", "backend", "backend_version", "runtime_identity")}
        self.model_identity["forge_adapter_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def unload(self):
        self.worker.unload()
        self.model_identity = {}

    def voices(self):
        return []

    def _generate(self, text, mode, seed, params, **kwargs):
        samples, rate, info = self.worker.generate({"text": text, "mode": mode, "seed": seed,
            "cfg_value": params.get("cfg_value", 2.), "inference_timesteps": params.get("inference_timesteps", 10), **kwargs})
        self.model_identity.update(effective_controls=info["effective_controls"], sample_rate=rate,
                                   direction_status=info.get("direction_status"), reference_sha256=info.get("reference_sha256"))
        return samples, rate

    def design(self, text, instruct, language, seed, params):
        return self._generate(text, "design", seed, params, voice_description=instruct)

    def clone(self, text, ref, ref_text, language, seed, instruct, params):
        return self._generate(text, "clone", seed, params, reference_path=str(ref), direction=instruct or "")

    def stock(self, text, voice, speed, language, params):
        raise ValueError("VoxCPM2 has no reference-free preset speaker in Forge")
