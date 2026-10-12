"""Local beat/downbeat inference and conservative, reviewable musical cues.

Beat This supplies observations, not ground truth. Four-beat bar checks reject
extra detections and leave gaps where the observations do not support a grid.
Section scores measure feature changes; neither score is a calibrated accuracy
probability, and no verse/drop/vocal labels are invented.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import zipfile

import numpy as np

import djmix
from studio_cache import digest, source_signature
from studio_storage import managed_cache
from studio_beat_tempo import VERSION as TEMPO_VERSION, tempo_from_beats

VERSION = 2
MODEL_SHA256 = '8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331'
MODEL_URL = 'https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt'
FPS = 50


def model_path():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets/models/beat-this-final0.ckpt'


def current_analysis(track, check_source=False):
    """Only use a grid checked against this applied source BPM."""
    result = track.structure
    if result.get('version') != VERSION or result.get('source_bpm') != track.bpm:
        return {}
    if check_source:
        try:
            if result.get('source') != source_signature(track.path):
                return {}
        except OSError:
            return {}
    return result


def current_tempo_estimate(track):
    """An independent beat estimate remains usable while the draft BPM changes."""
    result = track.structure
    estimate = result.get('tempo_estimate', {})
    if (not isinstance(estimate, dict) or result.get('version') != VERSION or result.get('model_sha256') != MODEL_SHA256
            or estimate.get('version') != TEMPO_VERSION or estimate.get('duration') != track.duration):
        return {}
    try:
        if result.get('source') != source_signature(track.path):
            return {}
    except OSError:
        return {}
    bpm = estimate.get('bpm')
    if bpm is not None and (type(bpm) not in (float, int) or not math.isfinite(bpm) or not 20 <= bpm <= 300):
        return {}
    return estimate


def current_opening_tempo(track):
    """Keep the opening-passage alternative independent of the chosen source BPM."""
    result = getattr(track, 'opening_tempo', {})
    if not isinstance(result, dict):
        return {}
    bpm = result.get('bpm')
    if (result.get('version') != 1 or result.get('duration') != track.duration
            or type(bpm) not in (float, int) or not math.isfinite(bpm) or not 20 <= bpm <= 300):
        return {}
    try:
        return result if result.get('source') == source_signature(track.path) else {}
    except OSError:
        return {}


def _peak_values(logits, times):
    # Postprocessing can snap a downbeat to the nearby beat. Preserve the model
    # evidence within its own 70-ms tolerance, not only at the snapped frame.
    values = []
    for time in times:
        frame = round(time * FPS)
        logit = np.max(logits[max(0, frame - 3):min(len(logits), frame + 4)])
        values.append(float(1 / (1 + np.exp(-np.clip(logit, -40, 40)))))
    return np.array(values)


def coherent_grid(beats, downbeats, beat_strength, downbeat_strength, bpm, duration):
    """Keep observed four-beat bars on a coherent path; never fill missing bars."""
    beats, downbeats = np.asarray(beats, float), np.asarray(downbeats, float)
    beat_strength, downbeat_strength = np.asarray(beat_strength, float), np.asarray(downbeat_strength, float)
    if (not math.isfinite(bpm) or not 20 <= bpm <= 300 or not math.isfinite(duration) or duration <= 0
            or beats.ndim != 1 or downbeats.ndim != 1 or beat_strength.shape != beats.shape
            or downbeat_strength.shape != downbeats.shape):
        raise ValueError('Invalid beat-grid observations.')
    for values, times in ((beat_strength, beats), (downbeat_strength, downbeats)):
        if (not np.isfinite(values).all() or not np.isfinite(times).all() or np.any(np.diff(times) <= 0)
                or np.any(times < 0) or np.any(times >= duration) or np.any((values < 0) | (values > 1))):
            raise ValueError('Beat observations must be ordered, finite and inside the song.')
    period = 60 / bpm
    empty = {'beats': [], 'downbeats': [], 'bars': [], 'coverage': 0., 'state': 'uncertain',
             'bpm': None, 'tempo_relation': 'unknown', 'model_beat_strength': 0., 'model_bar_strength': 0.}
    if len(beats) < 16 or len(downbeats) < 5:
        return empty
    intervals = np.diff(beats)
    sensible = intervals[(intervals >= .2) & (intervals <= 1.5)]
    if not len(sensible):
        return empty
    # A small histogram tolerates the detector's 20-ms output quantization.
    hist, edges = np.histogram(sensible, bins=np.arange(.19, 1.53, .02))
    center = int(np.argmax(np.convolve(hist, np.ones(3), mode='same')))
    local = sensible[(sensible >= edges[max(0, center - 1)]) & (sensible < edges[min(len(edges) - 1, center + 2)])]
    observed_period = float(np.median(local)) if len(local) else period
    ratio = (60 / observed_period) / bpm
    if .46 < ratio < .54 or 1.85 < ratio < 2.15:
        return {**empty, 'bpm': 60 / observed_period, 'tempo_relation': 'half-double'}
    bar = period * 4
    supports = {}

    def bar_support(a, b):
        if (a, b) not in supports:
            targets = np.linspace(downbeats[a], downbeats[b], 5)[:-1]
            near = [int(np.argmin(abs(beats - t))) for t in targets]
            supports[a, b] = [i for i, t in zip(near, targets)
                              if abs(beats[i] - t) <= .18 * period and beat_strength[i] >= .6]
        return supports[a, b]

    scores = np.zeros(len(downbeats))
    parents = np.full(len(downbeats), -1, dtype=int)
    for i, time in enumerate(downbeats):
        scores[i] = 1 + downbeat_strength[i]
        left = np.searchsorted(downbeats, time - 8.25 * bar)
        for j in range(left, i):
            gap = time - downbeats[j]
            count = round(gap / bar)
            if not 1 <= count <= 8:
                continue
            tolerance = min(.18, .12 * bar) * math.sqrt(count)
            error = abs(gap - count * bar)
            if error > tolerance:
                continue
            if count == 1 and (min(downbeat_strength[j], downbeat_strength[i]) < .65
                               or len(set(bar_support(j, i))) < 4):
                continue
            score = scores[j] + 1 + downbeat_strength[i] - .7 * (count - 1) - .4 * (error / tolerance) ** 2
            if score > scores[i]:
                scores[i], parents[i] = score, j
    chosen = []
    cursor = int(np.argmax(scores))
    while cursor >= 0:
        chosen.append(cursor)
        cursor = parents[cursor]
    chosen.reverse()
    bars, kept_beats, bar_strengths, beat_strengths = [], [], [], []
    number = 0
    for a, b in zip(chosen, chosen[1:]):
        start, end = downbeats[a], downbeats[b]
        count = round((end - start) / bar)
        current_number, number = number, number + count
        if count != 1 or min(downbeat_strength[a], downbeat_strength[b]) < .65:
            continue
        supported = bar_support(a, b)
        if len(set(supported)) < 4:
            continue
        bars.append({'start': float(start), 'end': float(end), 'number': int(current_number)})
        kept_beats.extend(float(beats[i]) for i in supported)
        beat_strengths.extend(float(beat_strength[i]) for i in supported)
        bar_strengths.extend((float(downbeat_strength[a]), float(downbeat_strength[b])))
    if len(bars) < 4:
        return empty
    coverage = sum(b['end'] - b['start'] for b in bars) / duration
    measured_bpm = float(240 / np.median([b['end'] - b['start'] for b in bars]))
    return {'beats': sorted(set(kept_beats)),
            'downbeats': sorted({b[key] for b in bars for key in ('start', 'end')}),
            'bars': bars, 'coverage': coverage, 'bpm': measured_bpm, 'tempo_relation': 'consistent',
            'state': 'supported' if coverage >= .5 and len(bars) >= 8 else 'partial',
            'model_beat_strength': float(np.mean(beat_strengths)),
            'model_bar_strength': float(np.mean(bar_strengths))}


def cue_suggestions(analysis, duration):
    bars = analysis.get('bars', [])
    result = [{'kind': 'end', 'time': float(duration), 'label': 'Keep original ending',
               'reason': 'Keeps the final sound and its tail. Listen to decide how the next song should enter.'}]
    if not bars:
        return result
    starts = [(bars[0]['start'], 'First supported bar', 'A detected four-beat bar near the beginning.')]
    for boundary in analysis.get('boundaries', []):
        starts.append((boundary['time'], boundary['label'], 'A detected change in the sound, aligned to a supported bar.'))
    phrase = analysis.get('phrase')
    if phrase:
        starts.append((phrase['anchor'], 'Recurring phrase pattern', 'Section changes suggest an eight-bar pattern; listen to confirm.'))
    chosen = []
    for time, label, reason in sorted(starts):
        if time > min(90, duration * .3) or duration - time < 16 * 60 / analysis['bpm']:
            continue
        if any(abs(time - prior) < 4 * 240 / analysis['bpm'] for prior in chosen):
            continue
        chosen.append(time)
        result.append({'kind': 'start', 'time': float(time), 'label': label, 'reason': reason})
        if len(chosen) >= 3:
            break
    endings = [b for b in analysis.get('boundaries', []) if duration * .65 < b['time'] < duration - 2]
    for boundary in endings[-2:]:
        result.append({'kind': 'end', 'time': float(boundary['time']), 'label': boundary['label'],
                       'reason': f"End on a section change. Omits the last {duration - boundary['time']:.1f} seconds; "
                                 'listen for unfinished vocals or reverb before keeping it.'})
    return result


def _infer(path, duration, directory, progress):
    from studio_storage import require_space
    from studio_process import check_cancelled
    from studio_structure_features import decoded_audio, extract, RATE
    check_cancelled()
    identity = source_signature(path)
    import torch
    from beat_this.inference import Audio2Frames
    from beat_this.model.postprocessor import Postprocessor
    checkpoint = model_path()
    if not checkpoint.is_file() or hashlib.sha256(checkpoint.read_bytes()).hexdigest() != MODEL_SHA256:
        raise djmix.MixError('The beat-analysis model is missing or damaged. Reinstall the full app, or run download_structure_model.py from source.')
    threads = torch.get_num_threads()
    torch.set_num_threads(min(4, max(1, os.cpu_count() or 1)))
    try:
        with decoded_audio(path, duration, directory.parent, progress) as (raw, samples):
            progress('Loading local beat and bar detector')
            runner = Audio2Frames(str(checkpoint), device='cpu')
            with torch.inference_mode():
                logmel, rms = extract(raw, samples, lambda audio: runner.signal2spect(audio, RATE), progress)
        count = 0
        total = math.ceil(duration * FPS / 1488)

        def before_part(*_):
            nonlocal count
            check_cancelled()
            count += 1
            progress(f'Finding beats and bar starts · part {count} of {total}')

        hook = runner.model.register_forward_pre_hook(before_part)
        try:
            with torch.inference_mode():
                spect = torch.from_numpy(logmel)
                beat_logits, downbeat_logits = runner.spect2frames(spect)
                beats, downbeats = Postprocessor()(beat_logits, downbeat_logits)
        finally:
            hook.remove()
        keep_b, keep_d = beats < duration, downbeats < duration
        beats, downbeats = beats[keep_b], downbeats[keep_d]
        arrays = dict(beats=beats, downbeats=downbeats, logmel=logmel, rms=rms,
                      beat_strength=_peak_values(beat_logits.numpy(), beats),
                      downbeat_strength=_peak_values(downbeat_logits.numpy(), downbeats))
    finally:
        torch.set_num_threads(threads)
    progress('Saving reusable beat observations')
    check_cancelled()
    if source_signature(path) != identity:
        raise djmix.MixError('The song changed during analysis. Analyze it again.')
    require_space(directory.parent, sum(v.nbytes for v in arrays.values()) + 65536, 'save musical analysis')
    with tempfile.TemporaryDirectory(prefix='.pending-structure-cache-', dir=directory.parent) as pending:
        stage = Path(pending) / 'observations.npz'
        with stage.open('wb') as handle:
            np.savez_compressed(handle, **arrays)
        check_cancelled()
        os.replace(stage, directory)
    return arrays


def _observations(path, duration, cache_dir, signature, progress):
    from studio_structure_features import MEL_BINS
    if abs(djmix.probe(path)['duration'] - duration) > .1:
        raise djmix.MixError('The song changed on disk. Re-import it before analysis.')
    prediction_file = cache_dir / (digest([1, MODEL_SHA256, signature]) + '.npz')
    try:
        with zipfile.ZipFile(prediction_file) as archive:
            limit = (math.ceil(duration * FPS) + 1) * (MEL_BINS * 4 + 64) + 65536
            if sum(entry.file_size for entry in archive.infolist()) > limit:
                raise ValueError('Oversized musical observations')
        with np.load(prediction_file, allow_pickle=False) as data:
            arrays = {k: data[k] for k in ('beats', 'downbeats', 'beat_strength', 'downbeat_strength', 'logmel', 'rms')}
        if not all(np.isfinite(v).all() for v in arrays.values()):
            raise ValueError('Invalid observations')
        progress('Reusing beat observations')
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        arrays = _infer(path, duration, prediction_file, progress)
    return arrays


@managed_cache
def analyze_tempo(path, duration, cache_dir, progress=lambda message: None):
    """Estimate tempo from observations without requiring an existing source BPM."""
    from studio_structure_features import MAX_SECONDS
    from studio_process import check_cancelled
    check_cancelled()
    path, cache_dir = Path(path), Path(cache_dir)
    if not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
        raise djmix.MixError("Beat analysis supports songs up to four hours.")
    signature = source_signature(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    arrays = _observations(path, duration, cache_dir, signature, progress)
    result = {**tempo_from_beats(arrays["beats"], duration), "version": TEMPO_VERSION,
              "duration": duration, "source": signature, "model_sha256": MODEL_SHA256}
    check_cancelled()
    if source_signature(path) != signature:
        raise djmix.MixError("The song changed during analysis. Analyze it again.")
    return result


@managed_cache
def analyze_song(path, bpm, duration, cache_dir, progress=lambda message: None):
    from studio_sections import analyze_sections
    from studio_structure_features import MAX_SECONDS
    from studio_process import check_cancelled
    check_cancelled()
    path, cache_dir = Path(path), Path(cache_dir)
    if not math.isfinite(bpm) or not 20 <= bpm <= 300:
        raise djmix.MixError('Enter the source BPM before finding musical cues.')
    if not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
        raise djmix.MixError('Musical analysis supports songs up to four hours. Choose a shorter source or set its beat grid and cues manually.')
    signature = source_signature(path)
    key = [VERSION, MODEL_SHA256, signature, bpm, duration, TEMPO_VERSION]
    cache_dir.mkdir(parents=True, exist_ok=True)
    result_file = cache_dir / (digest(key) + '.json')
    try:
        cached = json.loads(result_file.read_text())
        if cached['key'] == key and cached['analysis']['version'] == VERSION:
            progress('Reusing musical cues')
            return cached['analysis']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    arrays = _observations(path, duration, cache_dir, signature, progress)
    progress('Checking four-beat bars and section changes')
    grid = coherent_grid(arrays['beats'], arrays['downbeats'], arrays['beat_strength'], arrays['downbeat_strength'], bpm, duration)
    sections = analyze_sections(grid['bars'], arrays['logmel'], arrays['rms'], FPS)
    result = {**grid, **sections, 'version': VERSION, 'source': signature, 'source_bpm': bpm,
              'model': 'Beat This final0', 'model_sha256': MODEL_SHA256}
    result['tempo_estimate'] = {**tempo_from_beats(arrays['beats'], duration),
                                'version': TEMPO_VERSION, 'duration': duration}
    result['cues'] = cue_suggestions(result, duration)
    if source_signature(path) != signature:
        raise djmix.MixError('The song changed during analysis. Analyze it again.')
    progress('Musical cues ready to review')
    check_cancelled()
    payload = json.dumps({'key': key, 'analysis': result}, allow_nan=False)
    from studio_storage import require_space
    require_space(cache_dir, len(payload.encode('utf-8')), 'save musical cues')
    with tempfile.TemporaryDirectory(prefix='.pending-structure-result-', dir=cache_dir) as pending:
        stage = Path(pending) / 'result.json'
        stage.write_text(payload, encoding='utf-8')
        check_cancelled()
        os.replace(stage, result_file)
    return result
