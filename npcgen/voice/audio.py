"""Audio helpers for voice clips: 16-bit PCM WAV in and out, silence trimming, loudness, the mouth-opening track.

Standard library `wave` + numpy only. Every clip a worker makes goes through finish() before it is stored, so every voice in the
library has the same loudness and no dead air at either end (what a game wants to play back to back).
"""
import io
import wave

import numpy as np

TARGET_RMS_DB = -20.0          # speech loudness every clip is brought to (dBFS, RMS of the voiced part)
PEAK_LIMIT = 0.95
SILENCE_DB = -45.0             # quieter than this (relative to full scale) counts as silence at the ends
PAD_SECONDS = 0.08             # kept before the first and after the last voiced moment
MOUTH_FRAME_MS = 40            # one mouth value per 40 ms (25 per second): smooth enough for a two-frame talking mouth
MAX_UPLOAD_SECONDS = 40


def read_wav(data):
    """(float32 mono samples in -1..1, sample rate) from WAV bytes. 8/16/24/32-bit PCM, any channel count."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(frames)
    except (wave.Error, EOFError) as e:
        raise ValueError(f"Not a readable WAV file ({e})") from None
    if width == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = (np.where(v >= 1 << 23, v - (1 << 24), v)).astype(np.float32) / float(1 << 23)
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / float(1 << 31)
    else:
        raise ValueError(f"Unsupported WAV sample width: {width} bytes")
    if channels > 1:
        x = x[: len(x) // channels * channels].reshape(-1, channels).mean(axis=1)
    return np.ascontiguousarray(x, dtype=np.float32), int(rate)


def write_wav(samples, rate):
    """16-bit PCM mono WAV bytes."""
    x = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    pcm = (x * 32767.0).round().astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.tobytes())
    return out.getvalue()


def _frame_rms_db(x, rate, frame_ms=20):
    n = max(1, int(rate * frame_ms / 1000))
    usable = len(x) // n * n
    if usable == 0:
        return np.array([-120.0]), n
    frames = x[:usable].reshape(-1, n)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    return 20 * np.log10(rms), n


def trim(x, rate, silence_db=SILENCE_DB, pad=PAD_SECONDS):
    """Cut leading and trailing silence, keeping `pad` seconds around the voice. A silent clip comes back unchanged."""
    db, n = _frame_rms_db(x, rate)
    voiced = np.nonzero(db > silence_db)[0]
    if voiced.size == 0:
        return x
    keep = int(pad * rate)
    start = max(0, voiced[0] * n - keep)
    end = min(len(x), (voiced[-1] + 1) * n + keep)
    return x[start:end]


def normalize(x, rate, target_db=TARGET_RMS_DB, peak=PEAK_LIMIT):
    """Bring the voiced part to `target_db` RMS, never letting a peak pass `peak`."""
    db, n = _frame_rms_db(x, rate)
    voiced = db > max(SILENCE_DB, db.max() - 30)
    if not voiced.any():
        return x
    level = 10 * np.log10(np.mean(10 ** (db[voiced] / 10)))
    gain = 10 ** ((target_db - level) / 20)
    top = float(np.max(np.abs(x))) if len(x) else 0.0
    if top * gain > peak:
        gain = peak / top
    return (x * gain).astype(np.float32)


def mouth_track(x, rate, frame_ms=MOUTH_FRAME_MS):
    """How open the mouth is, 0..1, every `frame_ms`: loudness mapped between near-silence and the clip's loud speech."""
    db, _ = _frame_rms_db(x, rate, frame_ms)
    lo, hi = -42.0, float(np.percentile(db, 95)) if len(db) else -20.0
    if hi - lo < 6:
        hi = lo + 6
    open_ = np.clip((db - lo) / (hi - lo), 0.0, 1.0)
    return [round(float(v), 2) for v in open_]


def finish(data):
    """Worker output bytes -> (final WAV bytes, seconds, mouth track): trimmed, loudness-matched, 16-bit."""
    x, rate = read_wav(data)
    x = normalize(trim(x, rate), rate)
    return write_wav(x, rate), round(len(x) / rate, 2), mouth_track(x, rate)


def duration(data):
    x, rate = read_wav(data)
    return round(len(x) / rate, 2)


def check_upload(data, max_seconds=MAX_UPLOAD_SECONDS):
    """A recording to clone: readable, long enough to carry a voice, short enough to be a sample. Returns (wav, seconds)."""
    x, rate = read_wav(data)
    seconds = len(x) / rate if rate else 0
    if rate < 8000:
        raise ValueError("The recording's sample rate is too low (under 8 kHz)")
    if seconds < 3:
        raise ValueError("The recording is too short: 3 seconds or more of clear speech is needed")
    if seconds > max_seconds:
        raise ValueError(f"The recording is too long: keep it under {max_seconds} seconds")
    db, _ = _frame_rms_db(x, rate)
    if float(db.max()) < -50:
        raise ValueError("The recording is silent")
    return write_wav(normalize(trim(x, rate), rate), rate), round(seconds, 2)
