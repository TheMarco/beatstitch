#!/usr/bin/env python3
"""Evaluate the official madmom key CNN on existing calibration excerpts only."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import time

import djmix

SOURCE_REVISION = '27f032e8947204902c675e5e341a3faf5dc86dae'
MODEL_REVISION = '7e3dc1b0cad499792767074d03c38b194b9b0a79'


def model_identity(root):
    manifest = json.loads((root / 'sources.json').read_text())
    if (manifest['source_revision'] != SOURCE_REVISION or
            manifest['model_revision'] != MODEL_REVISION):
        raise ValueError('Use the pinned official source and model revisions.')
    # Verify the exact downloaded model before madmom deserializes it.
    path = root / 'models/key/2018/key_cnn.pkl'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != manifest['files']['models/key/2018/key_cnn.pkl']['sha256']:
        raise ValueError('Model differs from the recorded official download.')
    return path, {'library': 'madmom', 'source_revision': SOURCE_REVISION,
                  'model_revision': MODEL_REVISION, 'model_sha256': digest,
                  'python': platform.python_version(),
                  'dependencies': {name: importlib.metadata.version(name)
                                   for name in ('madmom', 'numpy', 'scipy', 'mido')},
                  'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'ffmpeg': djmix.run(['ffmpeg', '-version']).stdout.decode().splitlines()[0]}


def summarize(rows):
    estimated = [row for row in rows if row['status'] == 'estimated']
    exact = sum(row.get('exact', False) for row in rows)
    return {'attempted': len(rows), 'estimated': len(estimated),
            'abstained': sum(row['status'] == 'abstained' for row in rows),
            'failed': sum(row['status'] == 'failed' for row in rows),
            'exact': exact, 'correct_fraction_of_attempts': exact / len(rows) if rows else None,
            'top_three': sum(row.get('top_three', False) for row in rows),
            'baseline_exact': sum(row['baseline_exact'] for row in rows),
            'improved': sum(row.get('exact', False) and not row['baseline_exact'] for row in rows),
            'regressed': sum(not row.get('exact', False) and row['baseline_exact'] for row in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('--research-root', type=Path, default=Path('outputs/madmom-research'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--limit', type=int, help='Bounded smoke check; omit for the complete baseline selection.')
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Choose a new output filename; existing results are preserved.')
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive.')
    baseline = json.loads(args.baseline.read_text())
    if baseline.get('status') != 'finished' or baseline.get('split') != 'calibration':
        parser.error('Prototype comparison requires a completed calibration-partition report.')
    records = baseline['results'][:args.limit]
    if not records or any(row['record']['split'] != 'calibration' for row in records):
        parser.error('Every selected recording must belong to the calibration partition.')
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        os.environ[name] = '1'
    import numpy as np
    from madmom.audio.signal import Signal
    from madmom.features.key import CNNKeyRecognitionProcessor, KEY_LABELS

    model, detector = model_identity(args.research_root.resolve())
    processor = CNNKeyRecognitionProcessor(nn_files=[str(model)])
    labels = [djmix.key_name(djmix.parse_key(label)) for label in KEY_LABELS]
    report = {'status': 'running', 'baseline': str(args.baseline.resolve()),
              'baseline_sha256': hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
              'split': 'calibration', 'selected': len(records), 'detector': detector,
              'input': 'Same first-minute excerpts; FFmpeg mono 44.1 kHz float32',
              'limitations': baseline['limitations'] + [
                  'CNN softmax scores are model outputs, not calibrated correctness probabilities.',
                  'The paper reports distinct training and GiantSteps test recordings; '
                  'these model weights have no per-recording training manifest.'],
              'results': []}

    def save():
        report['summary'] = summarize(report['results'])
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        temporary.replace(args.out)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    save()
    for old in records:
        row = {'record': old['record'], 'baseline_exact': old.get('exact', False), 'status': 'failed'}
        started = time.monotonic()
        try:
            source = args.baseline.parent / 'audio' / (row['record']['id'] + '.mp3')
            if hashlib.md5(source.read_bytes()).hexdigest() != row['record']['md5']:
                raise ValueError('Source does not match the benchmark checksum.')
            duration = old.get('duration', min(60., djmix.probe(source)['duration']))
            decoded = djmix.ffmpeg('-i', str(source), '-t', str(duration), '-map', '0:a:0',
                                   '-ar', '44100', '-ac', '1', '-f', 'f32le', '-').stdout
            audio = np.frombuffer(decoded, dtype='<f4').copy()
            if not len(audio) or not np.isfinite(audio).all():
                raise ValueError('Decoded audio is empty or non-finite.')
            row.update(duration=duration, frames=len(audio))
            if np.max(np.abs(audio)) < 1e-5:
                row.update(status='abstained', reason='Effectively silent excerpt.')
            else:
                prediction = np.asarray(processor(Signal(audio, sample_rate=44100)))
                if (prediction.shape != (1, 24) or not np.isfinite(prediction).all() or
                        np.min(prediction) < 0 or not np.isclose(prediction.sum(), 1., atol=1e-5)):
                    raise ValueError('Invalid key model output.')
                scores = prediction[0]
                order = np.argsort(-scores)
                candidates = [{'key': labels[index], 'softmax': float(scores[index])}
                              for index in order]
                row.update(status='estimated', key=candidates[0]['key'], candidates=candidates,
                           exact=candidates[0]['key'] == row['record']['key'],
                           top_three=any(c['key'] == row['record']['key'] for c in candidates[:3]))
        except Exception as error:
            row['error'] = f'{type(error).__name__}: {error}'
        row['seconds'] = round(time.monotonic() - started, 3)
        report['results'].append(row)
        save()
        print(f'{len(report["results"])}/{len(records)}: {row["record"]["id"]} '
              f'{row["status"]} {row.get("key", row.get("error", ""))}', flush=True)
    report['status'] = 'finished'
    save()
    print(json.dumps(report['summary']))
    return int(report['summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
