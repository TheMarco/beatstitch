"""Conservative timing analysis and optional offline beat correction.

The detector is intended for fairly regular EDM kick patterns, not general
beat/downbeat recognition. Only repeated, agreeing pulse observations become
anchors. Long gaps return smoothly to ordinary fixed-tempo processing.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
from pathlib import Path
import uuid

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import correlate, find_peaks

import djmix
from studio_storage import managed_cache

VERSION = 1
RENDER_VERSION = 2


def signature(path):
    path = Path(path).resolve()
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns]


def features(path, cache=None, progress=lambda message: None):
    from studio_timing_features import extract
    return extract(path, cache, progress)


def fit_grid(onset, nominal_bpm):
    if len(onset) < 16000 or not np.isfinite(onset).all() or np.max(onset) < 1e-7:
        return None
    lo, hi = round(60000 / nominal_bpm * .85), round(60000 / nominal_bpm * 1.15)
    hi = min(hi, (len(onset)-1)//4)
    periods = np.arange(max(1, lo), hi + 1)
    if not len(periods):
        return None
    centered = onset - np.mean(onset)
    ac = correlate(centered, centered, mode='full', method='fft')[len(onset)-1:]
    score = sum(ac[periods*m] / (len(onset)-periods*m) / np.sqrt(m) for m in (1, 2, 4))
    peaks, _ = find_peaks(score)
    best = peaks[np.argsort(score[peaks])[-4:]] if len(peaks) else [int(np.argmax(score))]
    trial = sorted(set(int(periods[i]+d) for i in best for d in range(-3, 4)
                       if periods[0] <= periods[i]+d <= hi))
    bounded = np.minimum(onset, np.percentile(onset, 98))
    indices = np.arange(len(onset))
    choices = []
    for period in trial:
        folded = np.bincount(indices % period, weights=bounded, minlength=period)
        folded = gaussian_filter1d(folded, 5, mode='wrap') / (len(onset)/period)
        phase = int(np.argmax(folded))
        choices.append((folded[phase], period, phase))
    _, period, phase = max(choices)
    predicted = np.arange(phase, len(onset), period)
    predicted = predicted[(predicted > 120) & (predicted < len(onset)-120)]
    if len(predicted) < 12:
        return None
    found = np.array([p-90+int(np.argmax(onset[p-90:p+91])) for p in predicted])
    strength = onset[found]
    found = found / 1000
    strong = strength > .22*np.median(strength)
    numbers = np.arange(len(found)); keep = strong.copy()
    if sum(keep) < 12:
        return None
    for _ in range(3):
        slope, intercept = np.polyfit(numbers[keep], found[keep], 1)
        deviations = found - (intercept+slope*numbers)
        keep = strong & (abs(deviations) < .04)
        if sum(keep) < 12:
            return None
    quality = float(np.mean(keep))
    prominence = float(np.median(strength[keep]) / max(1e-9, np.percentile(onset, 95)))
    if quality < .8 or prominence < 1.1:
        return None
    return {'bpm': float(60/slope), 'quality': quality, 'beats': found[keep].tolist()}


def summarize(windows, duration):
    """Build a reviewable suggestion; source seconds map to corrected seconds.

    Correction preserves the file endpoints, not every clip endpoint. Layout
    uses this exact map, so a cue inside a corrected region stays on its source
    event even when its position moves slightly in the rendered arrangement.
    """
    empty = {'bpm': None, 'spread_percent': 0., 'coverage': 0., 'regions': [],
             'map': [], 'state': 'uncertain', 'windows': len(windows)}
    if len(windows) < 3:
        return empty
    tempos = np.array([w['bpm'] for w in windows])
    nominal = float(np.median(tempos)); period = 60/nominal
    counts = Counter(round(b*1000) for w in windows if w['quality'] >= .88 for b in w['beats'])
    beats = sorted(k/1000 for k, count in counts.items() if count >= 2)
    groups, group = [], []
    for beat in beats:
        if group:
            delta = beat-group[-1]; number = round(delta/period)
            if delta > 16:
                groups.append(group); group = []
            elif number < 1 or abs(delta-number*period) > .08:
                continue  # Competing offbeat phase: never make it a warp anchor.
        group.append(beat)
    if group:
        groups.append(group)
    fitted = []
    for group in groups:
        if len(group) < 24 or group[-1]-group[0] < 12:
            continue
        source = np.asarray(group)
        numbers = np.r_[0, np.cumsum(np.round(np.diff(source)/period))]
        slope, intercept = np.polyfit(numbers, source, 1)
        fitted.append((source, numbers, slope, intercept))
    if not fitted:
        return empty
    # Long-baseline fits avoid small BPM errors from a short opening excerpt.
    weights = np.array([s[-1]-s[0] for s, _, _, _ in fitted])
    bpm = float(60 / np.average([p for _, _, p, _ in fitted], weights=weights))
    rounded = round(bpm)
    # An integer is a candidate only when every long fit supports it. Do not
    # erase genuine tempo variation merely because EDM often uses integer BPM.
    if rounded >= 20 and all(abs((60/rounded-p)*n[-1]) <= .02 for _, n, p, _ in fitted):
        bpm = float(rounded)
    points = [(0., 0.), (duration, duration)]
    regions = []
    covered = 0.
    detected_drift = False
    for source, numbers, slope, intercept in fitted:
        residual = source-(intercept+slope*numbers)
        drift = float(np.percentile(abs(residual), 95)*1000)
        # A wrong BPM estimate alone must not trigger time warping.
        if drift < 20 or np.ptp(gaussian_filter1d(residual, 3)) < .04:
            continue
        detected_drift = True
        desired = numbers*60/bpm
        desired += float(np.mean(source-desired))
        # Sparse sections do not invent beat anchors. Keep fixed timing in the
        # gap, with up to two seconds of gentle transition at either boundary.
        split = np.flatnonzero(np.diff(source) > 4*period)+1
        for indices in np.split(np.arange(len(source)), split):
            s, d = source[indices], desired[indices]
            if len(s) < 24 or s[-1]-s[0] < 12:
                continue
            # A single displaced attack must not force a sharp speed change.
            # Remove both ends of implausible intervals, then validate the map.
            rates = np.diff(d)/np.diff(s)
            bad = np.flatnonzero((rates < .88) | (rates > 1.12))
            keep = np.ones(len(s), dtype=bool)
            keep[bad] = False; keep[bad+1] = False
            s, d = s[keep], d[keep]
            if len(s) < 24 or np.max(np.diff(s)) > 4*period:
                continue
            before, after = max(0., s[0]-2), min(duration, s[-1]+2)
            pairs = np.column_stack((np.r_[before, s, after], np.r_[before, d, after]))
            if (before >= s[0] or after <= s[-1] or np.max(abs(d-s)) > .5 or
                    np.any(np.diff(pairs[:, 0]) <= 0)):
                continue
            rates = np.diff(pairs[:, 1])/np.diff(pairs[:, 0])
            if np.any((rates < .88) | (rates > 1.12)):
                continue
            if regions and before <= regions[-1]['end']+2:
                continue
            points.extend(map(tuple, pairs))
            covered += s[-1]-s[0]
            regions.append({'start': float(s[0]), 'end': float(s[-1]),
                            'p95_ms': drift, 'beats': s.tolist(), 'grid': d.tolist()})
    points = sorted(set(points))
    low, high = np.percentile(tempos, [10, 90])
    return {'bpm': bpm, 'spread_percent': float(100*(high-low)/nominal),
            'coverage': float(covered/duration), 'regions': regions,
            'map': [list(p) for p in points] if regions else [],
            'state': 'drift' if regions else ('uncertain' if detected_drift else 'steady'), 'windows': len(windows)}


def analyze(path, bpm, duration, cache=None, progress=lambda message: None):
    return _analyze(path, bpm, duration, cache, progress)


@managed_cache
def _analyze(path, bpm, duration, cache_dir, progress):
    from studio_cache import digest
    from studio_process import check_cancelled
    check_cancelled()
    if not math.isfinite(bpm) or not 20 <= bpm <= 300:
        raise djmix.MixError('Enter an approximate BPM before timing analysis.')
    identity = signature(path)
    key = [VERSION, identity, float(bpm), float(duration)]
    destination = Path(cache_dir)/(digest(key)+'.json') if cache_dir else None
    if destination and destination.exists():
        try:
            result = json.loads(destination.read_text())
            if result['key'] == key:
                progress('Reusing timing analysis')
                return result['timing']
        except (OSError, ValueError, KeyError, TypeError):
            pass
    progress('Reading rhythm across the whole song')
    onset = features(path, cache_dir, progress)
    windows = []
    for offset in range(0, len(onset)-16000+1, 6000):
        check_cancelled()
        if offset % 30000 == 0:
            progress(f'Analyzing timing · {offset//1000}s / {int(duration)}s')
        window = fit_grid(onset[offset:offset+16000], bpm)
        if window:
            window['beats'] = [b+offset/1000 for b in window['beats']]
            windows.append(window)
    result = {**summarize(windows, duration), 'version': VERSION, 'source': identity}
    if signature(path) != identity:
        raise djmix.MixError('The source changed during timing analysis. Please analyze it again.')
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        data = json.dumps({'key': key, 'timing': result}, allow_nan=False)
        from studio_storage import require_space
        require_space(destination.parent, len(data.encode('utf-8')), 'save timing analysis')
        try:
            check_cancelled()
            temporary.write_text(data)
            check_cancelled()
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return result


def mapping(track, clip):
    if not clip.stabilize:
        return None
    timing = track.timing
    if not timing or timing.get('version') != VERSION:
        raise djmix.MixError(f'Analyze timing for {track.name} before enabling stabilization.')
    if timing.get('state') != 'drift' or not timing.get('map'):
        return None  # Stable / inconclusive analysis never causes extra processing.
    points = np.asarray(timing['map'], dtype=float)
    if (points.ndim != 2 or points.shape[1] != 2 or len(points) < 2 or
            not np.isfinite(points).all() or np.any(np.diff(points, axis=0) <= 0) or
            not np.allclose(points[[0, -1]], [[0, 0], [track.duration, track.duration]], atol=1e-6) or
            np.any(abs(points[:, 0]-points[:, 1]) > .5) or
            np.any((np.diff(points[:, 1])/np.diff(points[:, 0]) < .88) |
                   (np.diff(points[:, 1])/np.diff(points[:, 0]) > 1.12))):
        raise djmix.MixError(f'Invalid timing map for {track.name}. Analyze timing again.')
    return points


def corrected_time(track, clip, seconds):
    points = mapping(track, clip)
    return seconds if points is None else np.interp(seconds, points[:, 0], points[:, 1])


def prepare(track, clip, target, work, label, gain):
    """Render the complete clip once, so every later preview is the same audio."""
    points = mapping(track, clip)
    if points is None:
        raise djmix.MixError('No correction map is available.')
    if track.timing['source'] != signature(track.path):
        raise djmix.MixError(f'{track.name} changed on disk. Analyze its timing again.')
    ratio = track.bpm/target
    # Fixed two-second context is tied to clip cues, never to the preview range.
    start = max(0., clip.cue_in-2)
    end = min(track.duration, clip.cue_out+2)
    decoded, stretched = work/f'{label}-decode.wav', work/f'{label}-stretch.wav'
    djmix.ffmpeg('-i', track.path, '-ss', f'{start:.12f}', '-t', f'{end-start:.12f}',
                 '-map', '0:a:0', '-ar', str(djmix.SR), '-ac', '2',
                 '-af', 'volume=-12dB', '-c:a', 'pcm_f32le', str(decoded))
    # Rubber Band 4.0 R2 applies its resampler after the keyframe stretch when
    # pitch and a time map are combined: our +2 st fixture lost beat alignment.
    # Keep the audited R3 pitch-only pass at exactly the original duration,
    # then apply the accepted R2 timing correction with no pitch resampling.
    if clip.shift:
        pitched = work/f'{label}-pitch.wav'
        djmix.transform(decoded, pitched, track.bpm, track.bpm, clip.shift, True)
        decoded = pitched
    import soundfile as sf
    n = sf.info(decoded).frames
    actual_end = start+n/djmix.SR
    source = np.r_[start, points[(points[:, 0] > start) & (points[:, 0] < actual_end), 0], actual_end]
    destination = np.interp(source, points[:, 0], points[:, 1])
    destination = (destination-destination[0])*ratio
    a = np.round((source-start)*djmix.SR).astype(int)
    b = np.round(destination*djmix.SR).astype(int)
    time_map = work/f'{label}-time-map.txt'
    time_map.write_text(''.join(f'{x} {y}\n' for x, y in zip(a[1:-1], b[1:-1]))
                        + f'{n-1} {b[-1]-1}\n')
    args = ['rubberband', '--fast', '--crisp', '4', '--centre-focus', '--quiet',
            '--time', str(b[-1]/n), '--timemap', str(time_map)]
    djmix.run([*args, str(decoded), str(stretched)])
    first = round((corrected_time(track, clip, clip.cue_in)-corrected_time(track, clip, start))*ratio*djmix.SR)
    frames = round((corrected_time(track, clip, clip.cue_out)-corrected_time(track, clip, clip.cue_in))*ratio*djmix.SR)
    raw = work/f'{label}.raw'
    djmix.ffmpeg('-i', str(stretched), '-af',
                 f'atrim=start_sample={first}:end_sample={first+frames},volume={gain+12:.12f}dB',
                 '-f', 'f32le', '-ar', str(djmix.SR), '-ac', '2', str(raw))
    found = raw.stat().st_size//8
    if abs(found-frames) > 2:
        raise djmix.MixError(f'Stabilized render length mismatch: expected {frames}, got {found}.')
    with raw.open('ab' if found < frames else 'r+b') as handle:
        if found < frames:
            handle.write(b'\0'*8*(frames-found))
        else:
            handle.truncate(frames*8)
    return raw
