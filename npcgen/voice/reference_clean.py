"""Background-noise cleanup for a voice reference (the Voice Lab recipe, denoise.md).

FFmpeg's afftdn filter at a fixed noise floor (-25 dB by default) is the recipe that produced the accepted
Samantha reference in Voice Lab. The filter keeps the timeline, so the words and their timing do not change.

This module makes no identity claim. Cleaning removes noise and that alone moves a speaker embedding, so a clip
cannot be checked against its own noisy original. Only a clean reference of the same speaker can show that the
voice survived, and a library reference has none, so the caller records the identity as not checked.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import audio

DEFAULT_NF = -25.0
NF_RANGE = (-80.0, -20.0)  # afftdn accepts only these noise floors (dB)


def validate_nf(nf):
    if isinstance(nf, bool) or not isinstance(nf, (int, float)) or not np.isfinite(nf):
        raise ValueError('Noise floor must be a number')
    if not NF_RANGE[0] <= nf <= NF_RANGE[1]:
        raise ValueError('Noise floor must be between -80 and -20 dB')
    return float(nf)


def quiet_floor_dbfs(data):
    """10th percentile of 100 ms RMS, in dBFS: a stand-in for the noise floor."""
    samples, rate = audio.read_wav(data)
    frame = max(1, int(rate * 0.1))
    count = len(samples) // frame
    if count == 0:
        raise ValueError('The reference is too short to measure')
    rms = np.sqrt(np.mean(samples[:count * frame].reshape(count, frame) ** 2, axis=1)) + 1e-10
    return float(20 * np.log10(np.percentile(rms, 10)))


def clean(data, nf=DEFAULT_NF):
    """(cleaned WAV bytes, measurements) for WAV bytes. Raises ValueError when FFmpeg is missing or fails."""
    nf = validate_nf(nf)
    if shutil.which('ffmpeg') is None:
        raise ValueError('FFmpeg is needed to clean a reference')
    audio_filter = f'afftdn=nf={nf:g}'
    before = quiet_floor_dbfs(data)
    with tempfile.TemporaryDirectory(prefix='npcforge-clean-') as tmp:
        source, target = Path(tmp) / 'in.wav', Path(tmp) / 'out.wav'
        source.write_bytes(data)
        result = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-af', audio_filter,
                                 '-c:a', 'pcm_s16le', str(target)], capture_output=True, text=True, check=False, timeout=120)
        if result.returncode or not target.is_file():
            raise ValueError('FFmpeg could not clean the reference: ' + (result.stderr.strip()[-300:] or 'no output'))
        cleaned = target.read_bytes()
    after = quiet_floor_dbfs(cleaned)
    return cleaned, {'filter': audio_filter, 'quiet_floor_dbfs': {'before': round(before, 2), 'after': round(after, 2),
                                                                  'reduction_db': round(before - after, 2)}}
