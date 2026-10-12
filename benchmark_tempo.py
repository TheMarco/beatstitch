#!/usr/bin/env python3
"""Fixed GiantSteps-v2 development benchmark of the app's actual BPM import path."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import time
from unittest.mock import patch
import zipfile

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[_name] = '1'

import djmix
from benchmark_key_confidence import AUDIO_URL, download

REVISION = 'd51ab2422e76abacfaa86616a57054bc222ec9fd'
ARCHIVE_SHA256 = '05b908c799be0e616e0632d7019228fc0e424c106c1873c822fa12957d7f0a87'
ROOT = Path(__file__).resolve().parent


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def partition(checksum):
    bucket = int(hashlib.sha256(('dj-tempo-v1:' + checksum).encode()).hexdigest()[:8], 16) % 10
    return 'calibration' if bucket < 6 else 'validation' if bucket < 8 else 'test'


def annotations(archive):
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError('Use the pinned GiantSteps tempo annotation archive.')
    prefix = f'giantsteps-tempo-dataset-{REVISION}/'
    rows = []
    with zipfile.ZipFile(archive) as data:
        for name in data.namelist():
            match = re.fullmatch(re.escape(prefix) + r'annotations_v2/tempo/(\d+\.LOFI)\.bpm', name)
            if not match:
                continue
            identity = match[1]
            label = data.read(name).decode().strip()
            bpm = float(label)
            checksum = data.read(prefix + 'md5/' + identity + '.md5').decode().strip()
            if not math.isfinite(bpm) or bpm < 0 or not re.fullmatch('[0-9a-f]{32}', checksum):
                raise ValueError('Invalid tempo annotation or audio checksum.')
            rows.append({'id': identity, 'bpm': bpm if bpm else None, 'original_label': label,
                         'md5': checksum, 'split': partition(checksum)})
    if len(rows) != 664 or len({r['id'] for r in rows}) != 664:
        raise ValueError('Expected every one of the 664 pinned tempo records.')
    return sorted(rows, key=lambda row: hashlib.sha256(('dj-tempo-order-v1:' + row['id']).encode()).digest())


def score(estimate, reference):
    if (type(estimate) not in (int, float) or not math.isfinite(estimate) or estimate <= 0
            or type(reference) not in (int, float) or not math.isfinite(reference) or reference <= 0):
        raise ValueError('Score only positive finite BPM estimates and reference labels.')
    difference = abs(estimate - reference)
    ratio = estimate / reference
    categories = [(1., 'reference'), (.5, 'half'), (2., 'double'), (1 / 3, 'third'), (3., 'triple')]
    matching = [label for factor, label in categories if abs(ratio / factor - 1) <= .04 + 1e-12]
    return {'absolute_bpm_error': difference, 'relative_error_percent': 100 * difference / reference,
            'within_0_1_bpm': difference <= .1 + 1e-12,
            'within_0_5_bpm': difference <= .5 + 1e-12,
            'accuracy1_4pct': abs(ratio - 1) <= .04 + 1e-12,
            'accuracy2_4pct': bool(matching), 'tempo_multiple': matching[0] if matching else 'other'}


def summarize(rows):
    estimated = [r for r in rows if r['status'] == 'estimated']
    def metrics(field):
        values = [r[field] for r in estimated]
        result = {name: sum(v[name] for v in values) for name in
                  ('within_0_1_bpm', 'within_0_5_bpm', 'accuracy1_4pct', 'accuracy2_4pct')}
        result.update(tempo_multiples=dict(Counter(v['tempo_multiple'] for v in values)),
                      median_absolute_bpm_error=statistics.median(v['absolute_bpm_error'] for v in values) if values else None)
        return result
    return {'attempted': len(rows), 'estimated': len(estimated),
            'abstained': sum(r['status'] == 'abstained' for r in rows),
            'failed': sum(r['status'] == 'failed' for r in rows),
            'initial': metrics('initial_score'), 'final': metrics('score'),
            'timing_states': dict(Counter(r['timing']['state'] for r in estimated)),
            'refinement_used': sum(r['timing']['bpm'] is not None for r in estimated),
            'improved_at_0_5_bpm': sum(r['score']['within_0_5_bpm'] and not r['initial_score']['within_0_5_bpm'] for r in estimated),
            'regressed_at_0_5_bpm': sum(not r['score']['within_0_5_bpm'] and r['initial_score']['within_0_5_bpm'] for r in estimated),
            'accuracy_denominator': len(rows)}


def detector_identity():
    files = ('studio_engine.py', 'analyze_audio.py', 'studio_timing.py', 'studio_timing_features.py',
             'djmix.py', 'benchmark_tempo.py')
    return {'source_sha256': {name: sha256(ROOT / name) for name in files},
            'python': platform.python_version(),
            'dependencies': {name: importlib.metadata.version(name) for name in ('librosa', 'numpy', 'scipy', 'numba')},
            'ffmpeg': djmix.run(['ffmpeg', '-version']).stdout.decode().splitlines()[0],
            'input': 'App import BPM path: opening min(60s, duration), then complete preview timing refinement.',
            'excluded_step': 'Only the unrelated source-key detector is stubbed. Tempo/cue code runs unchanged.'}


def evaluate(record, root, detector):
    from studio_engine import Track, estimate_track
    import studio_timing
    root = Path(root)
    identity = hashlib.sha256(json.dumps([record, detector], sort_keys=True).encode()).hexdigest()[:24]
    cache = root / 'results' / (identity + '.json')
    if cache.exists():
        row = json.loads(cache.read_text())
        if row.get('record') != record or row.get('detector') != detector:
            raise ValueError('Cached recording or detector identity changed.')
        if row['status'] != 'failed':
            return row, True
    row = {'record': record, 'detector': detector, 'status': 'failed'}
    started = time.monotonic()
    try:
        source = root / 'audio' / (record['id'] + '.mp3')
        if hashlib.md5(source.read_bytes()).hexdigest() != record['md5']:
            raise ValueError('Audio differs from the published checksum.')
        duration = djmix.probe(source)['duration']
        track = Track(record['id'], str(source.resolve()), record['id'], duration,
                      cue_out=duration, key_origin='pending')
        timing_function = studio_timing.analyze

        def observe_timing(path, bpm, duration, cache, progress):
            row['initial_bpm'] = bpm
            return timing_function(path, bpm, duration, cache, progress)

        with patch('studio_key.analyze_key', return_value={'state': 'unavailable', 'reason': 'Not part of BPM benchmark.'}), \
                patch('studio_timing.analyze', side_effect=observe_timing):
            estimate_track(track, root / 'timing-cache' / identity)
        row.update(status='estimated', duration=duration, bpm=track.bpm, timing=track.timing,
                   app_status=track.status, cue_in=track.cue_in, cue_out=track.cue_out,
                   initial_score=score(row['initial_bpm'], record['bpm']), score=score(track.bpm, record['bpm']))
    except Exception as error:
        row['error'] = f'{type(error).__name__}: {error}'
        if isinstance(error, djmix.MixError) and ('No stable candidate grid' in str(error) or 'effectively silent' in str(error)):
            row['status'] = 'abstained'
    row['seconds'] = round(time.monotonic() - started, 3)
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix('.tmp')
    temporary.write_text(json.dumps(row, indent=2, allow_nan=False) + '\n')
    temporary.replace(cache)
    return row, False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--limit', type=int, default=32)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--reuse-audio', type=Path, default=Path('outputs/key-benchmark/audio'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    plan_path = args.out.with_suffix('.plan.json')
    if args.out.exists() or plan_path.exists() or args.limit < 1:
        parser.error('Choose new output paths and a positive limit.')
    catalog = annotations(args.root / 'annotations.zip')
    eligible = [r for r in catalog if r['bpm'] is not None and r['split'] == 'calibration']
    records = eligible[:args.limit]
    detector = detector_identity()
    excluded = [r for r in catalog if r['bpm'] is None]
    plan = {'status': 'frozen_before_inference', 'revision': REVISION, 'annotation_sha256': ARCHIVE_SHA256,
            'split': 'calibration', 'eligible': len(eligible), 'selected': len(records), 'records': records,
            'excluded_no_tempo_label': excluded, 'detector': detector,
            'metrics': ['absolute BPM error', 'within 0.1 BPM', 'within 0.5 BPM',
                        'Accuracy1 within 4%', 'Accuracy2 allowing half/double/third/triple within 4%'],
            'primary_descriptive_tolerance_bpm': .5}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with plan_path.open('x') as handle:
        json.dump(plan, handle, indent=2, allow_nan=False)
    report = {'status': 'running', 'split': 'calibration', 'selected': len(records),
              'plan': str(plan_path.resolve()), 'plan_sha256': sha256(plan_path), 'detector': detector,
              'reused_count': 0, 'results': [], 'limitations': [
                  'Small development pilot; no fitted or calibrated confidence is claimed.',
                  'Crowdsourced global BPM labels do not establish exact beat timing, phase or drift.',
                  'Tight 0.1/0.5-BPM checks describe closeness to these labels, not annotation precision.',
                  'Three clips with no tempo label are excluded and listed in the plan; not negative-test evidence.',
                  'Deterministic partitions group identical audio; related mixes may cross partitions.',
                  'Source-key benchmarking may already have used some recordings; this protocol is fixed before BPM results.',
                  'Failures/abstentions stay in the attempted-recording accuracy denominator.',
                  'Accuracy2 accepts tempo multiples that can still produce a poor DJ blend.',
              ]}

    def save():
        report['summary'] = summarize(report['results'])
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        temporary.replace(args.out)

    save()
    for record in records:
        path = args.root / 'audio' / (record['id'] + '.mp3')
        prior = args.reuse_audio / path.name
        try:
            if not path.exists() and prior.exists():
                if hashlib.md5(prior.read_bytes()).hexdigest() != record['md5']:
                    raise ValueError('Existing key-benchmark audio differs from the tempo checksum.')
                path.parent.mkdir(parents=True, exist_ok=True)
                os.link(prior, path)
            if args.download:
                download(AUDIO_URL + path.name, path, maximum=16 * 1024 * 1024, md5=record['md5'])
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
    print(json.dumps(report['summary']))
    return int(report['summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
