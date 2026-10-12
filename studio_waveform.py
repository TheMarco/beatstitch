"""Display-only waveform data: frequency-band envelopes and rendered-mix levels.

Nothing here feeds the audio path. Band envelopes are cached beside the library
cache and rebuilt whenever the source file's identity changes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid
import zipfile
from contextlib import closing

import numpy as np

import djmix

VERSION = 2
RATE = 22050
BINS_PER_SECOND = 50
HOP = RATE // BINS_PER_SECOND
# Low band carries kicks and bass, mid carries vocals and leads, high carries hats.
LOW_CUT, HIGH_CUT = 150, 2500
CHUNK_BINS = 256
# Compact envelope arrays, including concatenation/normalization temporaries.
MAX_BINS = 1_000_000


def _signature(path: Path):
    path = path.resolve()
    stat = path.stat()
    return [VERSION, str(path), stat.st_size, stat.st_mtime_ns]


def band_envelope(path: Path, cache_dir: Path | None, identity: str) -> np.ndarray:
    """Return an (n, 3) float32 array of low/mid/high envelopes, each scaled to 0..1."""
    from studio_process import check_cancelled
    check_cancelled()
    path = Path(path)
    signature = json.dumps(_signature(path))
    cached = Path(cache_dir) / f"{identity}.bands.npz" if cache_dir else None
    if cached and cached.is_file():
        try:
            # Inspect sizes before NumPy expands an old or damaged archive.
            with zipfile.ZipFile(cached) as archive:
                if sum(entry.file_size for entry in archive.infolist()) > MAX_BINS * 3 * 8 + 65536:
                    raise ValueError('Oversized waveform cache')
            with np.load(cached) as data:
                if str(data["signature"]) == signature:
                    values = data['bands']
                    if (values.ndim == 2 and values.shape[1] == 3 and len(values) <= MAX_BINS
                            and np.isfinite(values).all() and np.all((values >= 0) & (values <= 1))):
                        check_cancelled()
                        return values.astype(np.float32)
        except djmix.MixError:
            raise
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
            pass
    from scipy.signal import butter, sosfilt
    filters = (butter(4, LOW_CUT, "lowpass", fs=RATE, output="sos"),
               butter(2, [LOW_CUT, HIGH_CUT], "bandpass", fs=RATE, output="sos"),
               butter(4, HIGH_CUT, "highpass", fs=RATE, output="sos"))
    states = [np.zeros((len(sos), 2)) for sos in filters]
    pieces, count = [], 0
    remainder = np.zeros(0, dtype=np.float32)
    stream = djmix.stream_ffmpeg('-i', str(path), '-map', '0:a:0', '-ar', str(RATE), '-ac', '1',
                                '-f', 'f32le', '-', chunk_bytes=CHUNK_BINS * HOP * 4)
    with closing(stream):
        for raw in stream:
            check_cancelled()
            if len(raw) % 4: raise djmix.MixError('Incomplete samples while reading a waveform.')
            audio = np.concatenate((remainder, np.frombuffer(raw, dtype='<f4')))
            if not np.isfinite(audio).all(): raise djmix.MixError('Invalid samples while reading a waveform.')
            whole = len(audio) // HOP * HOP
            remainder = audio[whole:].copy()
            if not whole: continue
            bins = whole // HOP; count += bins
            if count > MAX_BINS:
                raise djmix.MixError('This source is too long for a detailed waveform. The source overview remains available.')
            chunk = np.empty((bins, 3))
            for index, sos in enumerate(filters):
                band, states[index] = sosfilt(sos, audio[:whole], zi=states[index])
                band = band.reshape(bins, HOP)
                chunk[:, index] = np.sqrt(np.mean(band * band, axis=1))
            pieces.append(chunk)
    if count < 2: return np.zeros((0, 3), dtype=np.float32)
    levels = np.concatenate(pieces); pieces.clear()
    bands = np.empty((count, 3), dtype=np.float32)
    for index in range(3):
        check_cancelled()
        rms = levels[:, index]
        reference = max(float(np.percentile(rms, 99.5)), 1e-6)
        bands[:, index] = np.clip(rms / reference, 0, 1) ** 0.85
    if json.dumps(_signature(path)) != signature:
        raise djmix.MixError('The source changed while its waveform was being read. Load its current audio again.')
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        from studio_storage import require_space
        require_space(cached.parent, bands.size * 4 + 4096, 'save its detailed waveform')
        temporary = cached.with_name(f".{uuid.uuid4().hex}.npz")
        try:
            with temporary.open("wb") as handle:
                np.savez_compressed(handle, signature=signature, bands=bands.astype(np.float16))
            check_cancelled()
            os.replace(temporary, cached)
        finally: temporary.unlink(missing_ok=True)
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
