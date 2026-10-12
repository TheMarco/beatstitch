"""Cached, cue-specific audio activity estimates for ordering, not quality scores.

The transparent heuristic combines attack density, brightness, spectral spread
and sustained level. It is not a trained or calibrated perception model. Features
are gain invariant above the silence floor; no song is judged by mastering gain.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
from scipy.signal import find_peaks

import djmix
from studio_cache import digest, source_signature
from studio_storage import managed_cache, require_space

VERSION = 1
RATE = 22050
CHUNK_SECONDS = 20
NFFT, HOP = 2048, 256


def audio_features(audio, rate=RATE):
    """One bounded stereo window. Averaging channel power avoids phase cancellation."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        audio = audio[:, None]
    if (audio.ndim != 2 or audio.shape[1] not in (1, 2) or rate != RATE
            or len(audio) < NFFT or not np.isfinite(audio).all()):
        raise ValueError('Energy analysis needs finite mono or stereo audio at 22050 Hz.')
    seconds = len(audio) / rate
    peak = float(np.max(np.abs(audio)))
    if peak < 1e-5:
        return {'seconds': seconds, 'audible': False}
    audio = audio / peak
    frames = np.lib.stride_tricks.sliding_window_view(audio, NFFT, axis=0)[::HOP]
    power = np.mean(np.abs(np.fft.rfft(frames * np.hanning(NFFT), axis=-1)) ** 2, axis=1)
    frequencies = np.fft.rfftfreq(NFFT, 1 / rate)
    edges = (20, 80, 160, 320, 640, 1280, 2560, 5120, rate / 2 + 1)
    bands = np.stack([power[:, (frequencies >= a) & (frequencies < b)].sum(axis=1)
                      for a, b in zip(edges, edges[1:])], axis=1)
    total = bands.sum(axis=1)
    reference = max(float(np.percentile(total, 95)), 1e-12)
    active = total > reference * 1e-4
    if np.count_nonzero(active) * HOP / rate < .25:
        return {'seconds': seconds, 'audible': False}
    # Positive log-band change measures attacks without using overall file gain.
    log_bands = np.log1p(100 * bands / reference)
    flux = np.maximum(0, np.diff(log_bands, axis=0)).mean(axis=1)
    peaks, _ = find_peaks(flux, prominence=.08, distance=round(.09 * rate / HOP))
    average = bands[active].sum(axis=0)
    fractions = average / max(float(average.sum()), 1e-12)
    spread = float(-np.sum(fractions * np.log(np.maximum(fractions, 1e-12))) / math.log(len(fractions)))
    brightness = float(power[:, frequencies >= 2000].sum() / max(float(power.sum()), 1e-12))
    sample_rms = float(np.sqrt(np.mean(audio.astype(float) ** 2)))
    sample_peak = max(float(np.percentile(np.abs(audio), 99.5)), 1e-8)
    fullness = min(1., sample_rms / sample_peak)
    return {'seconds': seconds, 'audible': True, 'attacks_per_second': len(peaks) / seconds,
            'brightness': brightness, 'spread': spread, 'fullness': fullness}


def summarize(windows, tempo_ratio=1.):
    """Keep the continuous score for ordering; display only a coarse 1–5 estimate."""
    total = sum(w['seconds'] for w in windows)
    usable = [w for w in windows if w['audible']]
    audible = sum(w['seconds'] for w in usable)
    if audible < 8 or not total or audible / total < .5:
        return {'value': None, 'reason': 'Too little audible audio for an energy estimate.'}
    scores = []
    for window in usable:
        attack = min(1., window['attacks_per_second'] * tempo_ratio / 6)
        bright = min(1., math.sqrt(window['brightness'] / .35))
        full = min(1., window['fullness'] / .65)
        scores.append(1 + 4 * (.45 * attack + .20 * bright + .20 * full + .15 * window['spread']))
    weights = np.asarray([w['seconds'] for w in usable])
    value = float(np.average(scores, weights=weights))
    # This spread describes observed variation, never statistical confidence.
    low, high = float(min(scores)), float(max(scores))
    means = {k: float(np.average([w[k] for w in usable], weights=weights))
             for k in ('attacks_per_second', 'brightness', 'spread', 'fullness')}
    reason = (f"Audio activity estimate for the kept clip: {means['attacks_per_second'] * tempo_ratio:.1f} "
              f"attacks/sec at mix tempo; {means['brightness'] * 100:.0f}% high-frequency power. "
              'Also considers spectral spread and sustained level, without using mastering gain. '
              'Not a measured emotion or quality score; use your own rating if it feels wrong.')
    if high - low >= 1.25:
        reason += ' Activity varies substantially across the clip.'
    return {'value': value, 'rating': min(5, max(1, round(value))), 'range': [low, high],
            'reason': reason, 'features': means}


def _valid_windows(value):
    if not isinstance(value, list) or not value:
        return False
    for w in value:
        if not isinstance(w, dict) or type(w.get('audible')) is not bool:
            return False
        keys = ('seconds', 'attacks_per_second', 'brightness', 'spread', 'fullness') if w['audible'] else ('seconds',)
        if any(type(w.get(k)) not in (int, float) or not math.isfinite(w[k]) or w[k] < 0 for k in keys):
            return False
        if not 0 < w['seconds'] <= CHUNK_SECONDS + .1:
            return False
        if w['audible'] and any(w[k] > 1.000001 for k in ('brightness', 'spread', 'fullness')):
            return False
    return True


@managed_cache
def analyze_clip(path, start, end, cache_dir, progress=lambda _: None):
    path, cache_dir = Path(path), Path(cache_dir)
    if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
        raise djmix.MixError('Energy analysis needs a valid clip start and end.')
    signature = source_signature(path)
    key = [VERSION, signature, start, end]
    folder = cache_dir / 'energy'; folder.mkdir(parents=True, exist_ok=True)
    result_file = folder / (digest(key) + '.json')
    try:
        cached = json.loads(result_file.read_text())
        if (cached['key'] == key and _valid_windows(cached['windows'])
                and abs(sum(w['seconds'] for w in cached['windows']) - (end - start)) < .15):
            progress('Reusing energy analysis')
            os.utime(result_file, None)
            return cached['windows']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    require_space(folder, 65536 + int((end - start) / CHUNK_SECONDS) * 512, 'save energy estimates')
    windows = []
    count = math.ceil((end - start) / CHUNK_SECONDS)
    for i in range(count):
        progress(f'Estimating energy · part {i + 1} of {count}')
        offset = start + i * CHUNK_SECONDS
        duration = min(CHUNK_SECONDS, end - offset)
        raw = djmix.ffmpeg('-ss', f'{offset:.9f}', '-i', str(path), '-t', f'{duration:.9f}',
                           '-map', '0:a:0', '-ar', str(RATE), '-ac', '2', '-f', 'f32le', '-').stdout
        audio = np.frombuffer(raw, dtype='<f4').reshape(-1, 2)
        if abs(len(audio) / RATE - duration) > .15:
            raise djmix.MixError('The source ends before the selected clip. Check its end cue.')
        if len(audio) >= NFFT:
            windows.append(audio_features(audio))
    progress('Saving energy estimate')
    if source_signature(path) != signature:
        raise djmix.MixError('The source changed during energy analysis. Try again.')
    with tempfile.NamedTemporaryFile(mode='w', dir=folder, prefix=result_file.stem + '.', suffix='.tmp', delete=False) as stream:
        stage = Path(stream.name)
        try:
            json.dump({'key': key, 'windows': windows}, stream, allow_nan=False)
            stream.close(); os.replace(stage, result_file)
        finally:
            stage.unlink(missing_ok=True)
    return windows


def estimate_mix_energy(session, cache_dir, progress=lambda _: None):
    """Missing/unusable audio stays unknown; cancellation stops the whole job."""
    result = {}
    for i, clip in enumerate(session.clips):
        if clip.energy is not None:
            continue
        track = session.tracks[clip.track_id]
        def report(message):
            progress(f'{track.name} ({i + 1}/{len(session.clips)}) · {message}')
        report('Checking energy')
        try:
            windows = analyze_clip(track.path, clip.cue_in, clip.cue_out, cache_dir, report)
            result[clip.id] = summarize(windows, session.target_bpm / track.bpm)
        except (OSError, ValueError, djmix.MixError) as error:
            if str(error) == 'Operation cancelled.':
                raise
            result[clip.id] = {'value': None, 'reason': f'Energy unavailable: {error}'}
    return result
