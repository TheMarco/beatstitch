"""Display-only waveform data: frequency-band envelopes and rendered-mix levels.

Nothing here feeds the audio path. Band envelopes are cached beside the library
cache and rebuilt whenever the source file's identity changes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import numpy as np

import djmix

VERSION = 2
RATE = 22050
BINS_PER_SECOND = 50
HOP = RATE // BINS_PER_SECOND
# Low band carries kicks and bass, mid carries vocals and leads, high carries hats.
LOW_CUT, HIGH_CUT = 150, 2500


def _signature(path: Path):
    path = path.resolve()
    stat = path.stat()
    return [VERSION, str(path), stat.st_size, stat.st_mtime_ns]


def band_envelope(path: Path, cache_dir: Path | None, identity: str) -> np.ndarray:
    """Return an (n, 3) float32 array of low/mid/high envelopes, each scaled to 0..1."""
    path = Path(path)
    signature = json.dumps(_signature(path))
    cached = Path(cache_dir) / f"{identity}.bands.npz" if cache_dir else None
    if cached and cached.is_file():
        try:
            with np.load(cached) as data:
                if str(data["signature"]) == signature:
                    return data["bands"].astype(np.float32)
        except (OSError, ValueError, KeyError):
            pass
    from scipy.signal import butter, sosfilt
    raw = djmix.ffmpeg("-i", str(path), "-map", "0:a:0", "-ar", str(RATE), "-ac", "1",
                       "-f", "f32le", "-").stdout
    audio = np.frombuffer(raw, dtype="<f4")
    count = len(audio) // HOP
    if count < 2:
        return np.zeros((0, 3), dtype=np.float32)
    filters = (butter(4, LOW_CUT, "lowpass", fs=RATE, output="sos"),
               butter(2, [LOW_CUT, HIGH_CUT], "bandpass", fs=RATE, output="sos"),
               butter(4, HIGH_CUT, "highpass", fs=RATE, output="sos"))
    bands = np.empty((count, 3), dtype=np.float32)
    for index, sos in enumerate(filters):
        band = sosfilt(sos, audio)[:count * HOP].reshape(count, HOP)
        rms = np.sqrt(np.mean(band * band, axis=1))
        reference = max(float(np.percentile(rms, 99.5)), 1e-6)
        bands[:, index] = np.clip(rms / reference, 0, 1) ** 0.85
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        temporary = cached.with_name(f".{uuid.uuid4().hex}.npz")
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, signature=signature, bands=bands.astype(np.float16))
        os.replace(temporary, cached)
    return bands


def level_envelope(path: Path, rate: int = BINS_PER_SECOND) -> np.ndarray:
    """Per-channel peak of a rendered WAV, for the transport level meter."""
    import soundfile as sf
    info = sf.info(str(path))
    hop = max(1, info.samplerate // rate)
    peaks = []
    remainder = np.zeros((0, max(1, info.channels)), dtype=np.float32)
    for block in sf.blocks(str(path), blocksize=hop * 2000, dtype="float32", always_2d=True):
        block = np.concatenate((remainder, block))
        whole = len(block) // hop * hop
        if whole:
            peaks.append(np.abs(block[:whole]).reshape(-1, hop, block.shape[1]).max(axis=1))
        remainder = block[whole:]
    if len(remainder):
        peaks.append(np.abs(remainder).max(axis=0, keepdims=True))
    if not peaks:
        return np.zeros((0, 2), dtype=np.float32)
    result = np.concatenate(peaks)
    if result.shape[1] == 1:
        result = np.repeat(result, 2, axis=1)
    return result[:, :2].astype(np.float32)
