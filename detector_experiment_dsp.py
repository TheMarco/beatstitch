"""Measure physical pitch and tempo behavior of the unchanged render engine."""
from pathlib import Path
import argparse
import json
import math
import tempfile
import time

import numpy as np
import soundfile as sf
from scipy.signal import periodogram

import djmix
from detector_pilot import atomic_json, digest, file_hash
from detector_experiment_drift import measure_rendered_attacks

RATE = 48000
SPEEDS = (.8, .95, 1., 1.05, 1.2)


def run(output, limit=None):
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError('limit must be a positive integer')
    root = Path(__file__).resolve().parent
    code = {n: file_hash(root / n) for n in ('detector_experiment_dsp.py', 'djmix.py',
            'detector_experiment_drift.py', 'studio_timing_features.py', 'studio_analysis_audio.py')}
    identity = {'code_sha256': code, 'rubberband_sha256': file_hash(djmix.tool_path('rubberband')),
                'ffmpeg_sha256': file_hash(djmix.tool_path('ffmpeg')),
                'tone_hz': 440., 'pitch_bound_cents': 5., 'timing_bound_ms': 30.,
                'length_bound_frames': 2, 'speeds': SPEEDS, 'pitch_range': [-12, 12]}
    key = digest(identity); path = output / 'dsp-report.json'
    report = json.loads(path.read_text()) if path.exists() else {
        'identity': identity, 'identity_sha256': key, 'rows': [],
        'limitations': ['Physical frequency, clock and finite-sample checks do not establish perceptual sound quality.'],
        'ui_promotion_allowed': False}
    if report['identity_sha256'] != key:
        raise ValueError('DSP experiment changed; preserve prior output and use a new directory.')
    rows = {(r['kind'], r['semitones'], r['speed']): r for r in report['rows']}
    cases = [('tone', 2, 1.)]
    cases += [('tone', pitch, speed) for pitch in range(-12, 13) for speed in SPEEDS
              if (pitch, speed) != (2, 1.)]
    cases += [('pulse', pitch, speed) for pitch in (-12, -2, 0, 2, 12) for speed in SPEEDS]
    report['planned_cases'] = len(cases)
    for kind, pitch, speed in (cases if limit is None else cases[:limit]):
        if (kind, pitch, speed) in rows: continue
        started = time.monotonic()
        row = {'kind': kind, 'semitones': pitch, 'speed': speed, 'status': 'failed'}
        try:
            with tempfile.TemporaryDirectory(prefix='dsp-control-', dir=output) as directory:
                work = Path(directory); source = work / 'source.wav'; result = work / 'rendered.wav'
                duration = 12.; t = np.arange(round(duration * RATE)) / RATE
                if kind == 'tone':
                    mono = .2 * np.sin(2 * np.pi * 440 * t)
                    mono *= np.minimum(np.clip(t / .02, 0, 1), np.clip((duration - t) / .02, 0, 1))
                else:
                    mono = np.zeros(len(t)); beats = np.arange(.5, 11.6, .5)
                    u = np.arange(round(.12 * RATE)) / RATE
                    kick = .4 * np.sin(2 * np.pi * 80 * u) * np.exp(-u / .04)
                    for beat in beats:
                        i = round(beat * RATE); mono[i:i + len(kick)] += kick
                sf.write(source, np.column_stack((mono, mono)), RATE, subtype='FLOAT')
                row['source_sha256'] = file_hash(source)
                djmix.transform(source, result, 120., 120. * speed, pitch, formants=True)
                samples, rate = sf.read(result, dtype='float32', always_2d=True)
                if rate != RATE or samples.shape[1] != 2 or not np.isfinite(samples).all():
                    raise ValueError('Invalid rendered samples or format.')
                expected_frames = round(len(t) / speed)
                row.update(output_sha256=file_hash(result), frames=len(samples), expected_frames=expected_frames,
                           frame_error=len(samples) - expected_frames, peak=float(np.max(abs(samples))))
                length_ok = abs(row['frame_error']) <= identity['length_bound_frames']
                if kind == 'tone':
                    a, b = round(2 * RATE / speed), round(10 * RATE / speed)
                    frequencies, power = periodogram(samples[a:b, 0], fs=RATE, window='hann', nfft=(b-a)*4)
                    observed = float(frequencies[int(np.argmax(power))])
                    expected = 440 * 2 ** (pitch / 12)
                    cents = 1200 * math.log2(observed / expected) if observed > 0 else None
                    row.update(expected_hz=expected, observed_hz=observed, pitch_error_cents=cents)
                    passed = length_ok and cents is not None and abs(cents) <= identity['pitch_bound_cents']
                else:
                    # Ignore source boundaries; subtract the known acoustic
                    # envelope bias instead of declaring it a render delay.
                    selected = beats[2:-2]
                    before = measure_rendered_attacks(source, selected)
                    after = measure_rendered_attacks(result, selected / speed)
                    errors = (np.asarray(after['observed_seconds'])
                              - np.asarray(before['observed_seconds']) / speed) * 1000
                    row.update(timing_error_ms=errors.tolist(), timing_p95_abs_ms=float(np.percentile(abs(errors), 95)),
                               timing_max_abs_ms=float(np.max(abs(errors))))
                    passed = length_ok and row['timing_max_abs_ms'] <= identity['timing_bound_ms']
                row['status'] = 'passed' if passed else 'failed'
        except Exception as error:
            row['error'] = f'{type(error).__name__}: {error}'[:1800]
        row['seconds'] = round(time.monotonic() - started, 3)
        report['rows'].append(row); rows[(kind, pitch, speed)] = row
        report['status'] = 'running'
        atomic_json(path, report)
        print(f"DSP {kind} pitch{pitch:+} speed{speed}: {row['status']}", flush=True)
    report['status'] = ('diagnostic' if limit is not None else
                        'completed_with_failures' if any(r['status'] == 'failed' for r in report['rows']) else 'completed')
    report['summary'] = {'attempted': len(report['rows']), 'passed': sum(r['status'] == 'passed' for r in report['rows']),
                         'failed': sum(r['status'] == 'failed' for r in report['rows'])}
    atomic_json(path, report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True); parser.add_argument('--limit', type=int)
    args = parser.parse_args(); result = run(args.out, args.limit)
    raise SystemExit(int(result['summary']['failed'] > 0))
