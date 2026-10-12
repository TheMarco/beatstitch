#!/usr/bin/env python3
"""Offline BPM comparison using the app's existing Beat This observations.

Development experiment only: no app settings, projects or import behavior change.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import time

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[_name] = '1'

import numpy as np

import djmix
from benchmark_tempo import ROOT, annotations, score, sha256, summarize
import studio_structure
from studio_beat_tempo import tempo_from_beats
import studio_timing


def identity():
    import beat_this
    package = Path(beat_this.__file__).parent
    checkpoint = studio_structure.model_path()
    if sha256(checkpoint) != studio_structure.MODEL_SHA256:
        raise ValueError('Beat This checkpoint differs from the app model.')
    files = ('prototype_beat_this_tempo.py', 'benchmark_tempo.py', 'studio_structure.py',
             'studio_structure_features.py', 'studio_beat_tempo.py', 'studio_timing.py', 'studio_timing_features.py', 'djmix.py')
    return {'model': 'Beat This final0', 'model_sha256': studio_structure.MODEL_SHA256,
            'python': platform.python_version(),
            'source_sha256': {name: sha256(ROOT / name) for name in files},
            'package_source_sha256': {str(p.relative_to(package)): sha256(p) for p in sorted(package.rglob('*.py'))},
            'dependencies': {name: importlib.metadata.version(name) for name in
                             ('beat-this', 'torch', 'torchaudio', 'numpy', 'scipy', 'soxr', 'einops', 'rotary-embedding-torch')},
            'ffmpeg': djmix.run(['ffmpeg', '-version']).stdout.decode().splitlines()[0],
            'input': 'Complete preview, existing app beat frontend and minimal postprocessor; CPU float32.',
            'bpm': 'Median of accepted 16-interval linear-fit periods, hop 8; optional unchanged app timing refinement.',
            'uses_reference_or_previous_bpm': False}


def evaluate(record, root, detector):
    fingerprint = hashlib.sha256(json.dumps([record, detector], sort_keys=True).encode()).hexdigest()[:24]
    directory = root / 'beat-this' / fingerprint
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / 'result.json'
    source = root / 'audio' / (record['id'] + '.mp3')
    if hashlib.md5(source.read_bytes()).hexdigest() != record['md5']:
        raise ValueError('Audio differs from the published checksum.')
    if cache.exists():
        prior = json.loads(cache.read_text())
        if prior.get('record') != record or prior.get('detector') != detector:
            raise ValueError('Cached recording or detector differs.')
        if prior['status'] != 'failed':
            return prior, True
    row = {'record': record, 'detector': detector, 'status': 'failed'}
    started = time.monotonic()
    try:
        duration = djmix.probe(source)['duration']
        observation_path = directory / 'observations.npz'
        if observation_path.exists():
            with np.load(observation_path, allow_pickle=False) as data:
                beats, downbeats = data['beats'].copy(), data['downbeats'].copy()
        else:
            observations = studio_structure._infer(source, duration, observation_path, lambda message: None)
            beats, downbeats = observations['beats'], observations['downbeats']
        estimate = tempo_from_beats(beats, duration)
        row.update(duration=duration, beat_count=len(beats), downbeat_count=len(downbeats),
                   beat_tempo=estimate, observations_sha256=sha256(observation_path))
        if estimate['bpm'] is None:
            row.update(status='abstained', reason='Insufficient consecutive beat windows.')
        else:
            initial = estimate['bpm']
            timing = studio_timing.analyze(source, initial, duration, directory / 'timing')
            final = timing['bpm'] if timing['bpm'] is not None else initial
            row.update(status='estimated', initial_bpm=initial, bpm=final, timing=timing,
                       initial_score=score(initial, record['bpm']), score=score(final, record['bpm']))
    except Exception as error:
        row['error'] = f'{type(error).__name__}: {error}'
    row['seconds'] = round(time.monotonic() - started, 3)
    temporary = cache.with_suffix('.tmp')
    temporary.write_text(json.dumps(row, indent=2, allow_nan=False) + '\n')
    temporary.replace(cache)
    return row, False


def compare(baseline, rows):
    prior = {row['record']['id']: row for row in baseline}
    corrected, regressed = [], []
    for row in rows:
        before = prior[row['record']['id']]
        if before['record'] != row['record']:
            raise ValueError('Paired recordings differ.')
        old = before.get('score', {}).get('accuracy1_4pct', False)
        new = row.get('score', {}).get('accuracy1_4pct', False)
        if new and not old: corrected.append(row['record']['id'])
        if old and not new: regressed.append(row['record']['id'])
    return {'metric': 'Accuracy1 within 4%', 'corrected': corrected, 'regressed': regressed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=32)
    args = parser.parse_args()
    plan_path = args.out.with_suffix('.plan.json')
    if args.limit < 1 or args.out.exists() or plan_path.exists():
        parser.error('Use a positive limit and new report/plan paths.')
    baseline = json.loads(args.baseline.read_text())
    catalog = [r for r in annotations(args.root / 'annotations.zip') if r['bpm'] and r['split'] == 'calibration']
    if (baseline['status'] != 'finished' or len(baseline['results']) != baseline['selected']
            or [r['record'] for r in baseline['results']] != catalog[:baseline['selected']]
            or args.limit > baseline['selected']):
        parser.error('Baseline must contain the complete, preselected development records.')
    records = [r['record'] for r in baseline['results'][:args.limit]]
    detector = identity()
    plan = {'status': 'frozen_before_inference', 'records': records, 'detector': detector,
            'baseline_sha256': sha256(args.baseline), 'baseline': str(args.baseline.resolve()),
            'purpose': 'Development comparison, not final accuracy or confidence calibration.'}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with plan_path.open('x') as handle:
        json.dump(plan, handle, indent=2, allow_nan=False)
    report = {'status': 'running', 'selected': len(records), 'plan': str(plan_path.resolve()),
              'plan_sha256': sha256(plan_path), 'detector': detector, 'reused_count': 0, 'results': [],
              'limitations': baseline['limitations'] + [
                  'Beat This training-recording overlap has not been audited; this is not an unseen-data claim.',
                  'Accepted window count, residual and coverage are not calibrated confidence.',
                  'The model is already packaged for musical analysis; this prototype does not alter app BPM imports.']}
    def save():
        report['summary'] = summarize(report['results'])
        report['comparison'] = compare(baseline['results'], report['results'])
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        temporary.replace(args.out)
    save()
    for record in records:
        try:
            row, reused = evaluate(record, args.root, detector)
        except Exception as error:
            row, reused = {'record': record, 'status': 'failed', 'error': f'{type(error).__name__}: {error}'}, False
        report['results'].append(row)
        report['reused_count'] += int(reused)
        save()
        print(f'{len(report["results"])}/{len(records)} {record["id"]}: {row["status"]} '
              f'{row.get("bpm", row.get("error"))} reference={record["bpm"]} reused={reused}', flush=True)
    report['status'] = 'finished'
    save()
    print(json.dumps({'summary': report['summary'], 'comparison': report['comparison']}))
    return int(report['summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
