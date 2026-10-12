#!/usr/bin/env python3
"""Extend the frozen OpenKeyScan pilot across the complete development partition."""
from __future__ import annotations

import argparse
import copy
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys

import djmix
from benchmark_key_confidence import annotations, summarize as baseline_summary
from benchmark_madmom_incremental import checked_baseline, checked_predictions
from prototype_openkeyscan_key import MODEL, REVISION, sha256, summarize


def checked_comparison(value, detector, baseline_rows, madmom_rows):
    rows = checked_predictions(value, detector, baseline_rows)
    for key, row in rows.items():
        prior = madmom_rows[key]
        if (row['record'] != prior['record'] or row.get('madmom_key') != prior.get('key')
                or row.get('madmom_exact') != prior.get('exact', False)):
            raise ValueError('Cached comparison differs from the madmom reference.')
        if row['status'] == 'estimated':
            if (row.get('duration') != prior['duration'] or row.get('frames') != prior['frames']
                    or row.get('float_wav_roundtrip') != 'passed'):
                raise ValueError('Cached comparison differs from the verified input excerpt.')
            if row.get('exact') != (row['key'] == row['record']['key']):
                raise ValueError('Cached correctness does not match the reference key.')
    return rows


def verify_runtime(detector, root):
    manifest = json.loads((root / 'sources.json').read_text())
    if (detector['library'] != 'OpenKeyScan' or detector['source_revision'] != REVISION
            or detector['model'] != MODEL or detector['source_manifest'] != manifest
            or detector['device'] != 'cpu' or detector['threads'] != 1):
        raise ValueError('Cached detector differs from the pinned source/model configuration.')
    for name, identity in manifest['files'].items():
        if sha256(root / 'source' / name) != identity['sha256']:
            raise ValueError(f'Official source/model changed: {name}')
    if (detector['model_sha256'] != sha256(root / 'source' / MODEL)
            or detector['runner_sha256'] != sha256(Path(__file__).with_name('prototype_openkeyscan_key.py'))
            or detector['python'] != platform.python_version()
            or any(importlib.metadata.version(name) != version
                   for name, version in detector['dependencies'].items())
            or detector['ffmpeg'] != djmix.run(['ffmpeg', '-version']).stdout.decode().splitlines()[0]):
        raise ValueError('Cached model, inference runner or runtime has changed.')


def write_new(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('--madmom', type=Path, required=True)
    parser.add_argument('--reuse', type=Path, required=True)
    parser.add_argument('--research-root', type=Path, default=Path('outputs/openkeyscan-research'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Choose a new output path; existing reports are preserved.')
    expected = {row['id']: row for row in annotations(args.baseline.parent / 'annotations.zip')
                if row['split'] == 'calibration'}
    baseline, baseline_rows = checked_baseline(args.baseline, expected)
    madmom, madmom_rows = checked_baseline(args.madmom, expected)
    if baseline['status'] != 'finished' or madmom['status'] != 'finished':
        parser.error('Both full development references must be finished.')
    cached = json.loads(args.reuse.read_text())
    detector = cached['detector']
    verify_runtime(detector, args.research_root)
    rows = checked_comparison(cached, detector, baseline_rows, madmom_rows)
    rows = {key: row for key, row in rows.items() if row['status'] != 'failed'}
    pending = [key for key in expected if key not in rows]
    report = {
        'status': 'running', 'split': 'calibration', 'selected': len(expected),
        'baseline': str(args.baseline.resolve()), 'baseline_sha256': sha256(args.baseline),
        'madmom': str(args.madmom.resolve()), 'madmom_sha256': sha256(args.madmom),
        'detector': detector, 'input': cached['input'],
        'extension_runner_sha256': sha256(Path(__file__)),
        'limitations': [value.replace('Development pilot only', 'Development partition only')
                        for value in cached['limitations']],
        'reused_count': len(rows), 'reused_report': str(args.reuse.resolve()),
        'reused_report_sha256': sha256(args.reuse), 'parts': [], 'results': [],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)

    def save():
        report['results'] = [rows[key] for key in expected if key in rows]
        report['summary'] = summarize(report['results'])
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        temporary.replace(args.out)

    save()
    try:
        if pending:
            input_path = args.baseline.parent / f'{args.out.stem}-remaining-baseline.json'
            madmom_path = args.baseline.parent / f'{args.out.stem}-remaining-madmom.json'
            output_path = args.out.parent / f'{args.out.stem}-remaining-predictions.json'
            for original, path, inputs in ((baseline, input_path, baseline_rows),
                                          (madmom, madmom_path, madmom_rows)):
                snapshot = copy.deepcopy(original)
                snapshot.update(selected=len(pending), results=[inputs[key] for key in pending],
                                snapshot_of=report['baseline' if path == input_path else 'madmom'])
                snapshot['summary'] = baseline_summary(snapshot['results']) if path == input_path else None
                write_new(path, snapshot)
            print(f'Reusing {len(rows)} unchanged predictions; analyzing {len(pending)} new tracks.', flush=True)
            process = subprocess.run([
                sys.executable, str(Path(__file__).with_name('prototype_openkeyscan_key.py')),
                str(input_path), '--madmom', str(madmom_path), '--research-root', str(args.research_root),
                '--out', str(output_path)])
            chunk = json.loads(output_path.read_text())
            predictions = checked_comparison(chunk, detector, baseline_rows, madmom_rows)
            if (set(predictions) != set(pending) or process.returncode not in (0, 1)
                    or chunk['baseline_sha256'] != sha256(input_path)
                    or chunk['madmom_sha256'] != sha256(madmom_path)
                    or chunk['input'] != cached['input']):
                raise ValueError('New predictions differ from the requested inputs or processing.')
            rows.update(predictions)
            report['parts'].append({'path': str(output_path.resolve()), 'sha256': sha256(output_path),
                                    'count': len(predictions)})
        if set(rows) != set(expected):
            raise ValueError('Comparison does not cover the complete development partition.')
        report['status'] = 'finished'
        save()
        print(json.dumps(report['summary']), flush=True)
        return int(report['summary']['failed'] > 0)
    except Exception as error:
        report.update(status='incomplete', error=f'{type(error).__name__}: {error}')
        save()
        raise


if __name__ == '__main__':
    raise SystemExit(main())
