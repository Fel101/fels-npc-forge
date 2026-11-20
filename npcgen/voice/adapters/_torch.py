"""Shared set-up for the torch engines (imported only inside a worker)."""
import os
import random


def setup():
    """Import torch with the settings measured on AMD GPUs; returns (torch, device).

    On ROCm, AMD's convolution library (MIOpen) re-tunes for every new input length, which took 10-15 s per new sentence
    length (up to 80 s the first time) even with MIOPEN_FIND_MODE=FAST; PyTorch's own kernels take about 1 s. So cudnn (MIOpen
    on ROCm) is switched off there. A PC with two AMD GPUs sets HIP_VISIBLE_DEVICES in config.json's engine "env".
    """
    import torch
    if getattr(torch.version, "hip", None):
        torch.backends.cudnn.enabled = False
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if os.environ.get("NPCFORGE_VOICE_DEVICE"):
        device = os.environ["NPCFORGE_VOICE_DEVICE"]
    return torch, device


def seed_all(torch, seed):
    seed = int(seed) % (2 ** 32)
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except Exception:
        pass
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def to_numpy(audio):
    import numpy as np
    if hasattr(audio, "detach"):
        audio = audio.detach().float().cpu().numpy()
    return np.asarray(audio, dtype=np.float32).reshape(-1)


def free(torch):
    import gc
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
