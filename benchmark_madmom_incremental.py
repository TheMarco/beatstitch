#!/usr/bin/env python3
"""Follow a running development baseline without repeating existing CNN predictions."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from benchmark_key_confidence import annotations, summarize as baseline_summary
from prototype_madmom_key import model_identity, summarize


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_baseline(path, expected, identity=None):
    value = json.loads(path.read_text())
    if (value.get('status') not in ('running', 'finished') or value.get('split') != 'calibration'
            or value.get('selected') != len(expected)):
        raise ValueError('Expected the complete calibration baseline, running or finished.')
    if identity is not None and value['detector'] != identity:
        raise ValueError('Baseline detector changed during the comparison.')
    rows = value['results']
    by_id = {row['record']['id']: row for row in rows}
    if len(rows) != len(by_id) or any(row['record'] != expected.get(key) for key, row in by_id.items()):
        raise ValueError('Baseline records do not match unique pinned calibration annotations.')
    if value['status'] == 'finished' and set(by_id) != set(expected):
        raise ValueError('Finished baseline does not cover every development recording.')
    return value, by_id


def checked_predictions(value, detector, baseline_rows):
    if value.get('status') != 'finished' or value.get('split') != 'calibration' or value['detector'] != detector:
        raise ValueError('Cached predictions need the same completed calibration detector and dependencies.')
    result = {}
    for row in value['results']:
        key = row['record']['id']
        old = baseline_rows.get(key)
        if (key in result or old is None or row['record'] != old['record']
                or row.get('baseline_exact') != old.get('exact', False)
                or (row['status'] != 'failed' and 'duration' in old
                    and row.get('duration') != old['duration'])):
            raise ValueError('Cached prediction differs from its baseline recording or excerpt.')
        if row['status'] not in ('estimated', 'abstained', 'failed'):
            raise ValueError('Incomplete cached prediction.')
        result[key] = row
    if len(result) != value.get('selected'):
        raise ValueError('Cached comparison is incomplete.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('--reuse', type=Path, required=True)
    parser.add_argument('--research-root', type=Path, default=Path('outputs/madmom-research'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Choose a new output path; existing results are preserved.')
    expected = {row['id']: row for row in annotations(args.baseline.parent / 'annotations.zip')
                if row['split'] == 'calibration'}
    baseline, baseline_rows = checked_baseline(args.baseline, expected)
    baseline_identity = baseline['detector']
    _, detector = model_identity(args.research_root.resolve())
    cached = json.loads(args.reuse.read_text())
    results = checked_predictions(cached, detector, baseline_rows)
    # A failed earlier attempt must not become a permanently reused failure.
    results = {key: row for key, row in results.items() if row['status'] != 'failed'}
    report = {'status': 'running', 'split': 'calibration', 'selected': len(expected),
              'baseline': str(args.baseline.resolve()), 'detector': detector,
              'input': cached['input'], 'limitations': cached['limitations'],
              'reused_count': len(results), 'reused_report': str(args.reuse.resolve()),
              'reused_report_sha256': sha256(args.reuse), 'parts': [], 'results': []}
    args.out.parent.mkdir(parents=True, exist_ok=True)

    def save():
        report['results'] = [results[key] for key in sorted(results)]
        report['summary'] = summarize(report['results'])
        report['baseline_status'] = baseline['status']
        # Hash this exact snapshot, not a racing reread of the running file.
        report['baseline_snapshot_sha256'] = hashlib.sha256(
            json.dumps(baseline, sort_keys=True).encode()).hexdigest()
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        temporary.replace(args.out)

    save()
    last_progress = time.monotonic()
    observed = len(baseline_rows)
    try:
        while True:
            baseline, baseline_rows = checked_baseline(args.baseline, expected, baseline_identity)
            if len(baseline_rows) != observed:
                observed = len(baseline_rows)
                last_progress = time.monotonic()
            pending = [baseline_rows[key] for key in sorted(baseline_rows) if key not in results][:64]
            if pending:
                number = len(report['parts']) + 1
                # Keep the input next to audio/ for the unchanged prototype runner.
                input_path = args.baseline.parent / f'{args.out.stem}-batch-{number:03d}-baseline.json'
                output_path = args.out.parent / f'{args.out.stem}-batch-{number:03d}-predictions.json'
                snapshot = copy.deepcopy(baseline)
                snapshot.update(status='finished', selected=len(pending), results=pending,
                                summary=baseline_summary(pending),
                                snapshot_of=str(args.baseline.resolve()))
                with input_path.open('x', encoding='utf-8') as handle:
                    json.dump(snapshot, handle, indent=2, allow_nan=False)
                print(f'Batch {number}: {len(pending)} new recordings; {len(results)} already available.', flush=True)
                process = subprocess.run([
                    sys.executable, str(Path(__file__).with_name('prototype_madmom_key.py')),
                    str(input_path), '--research-root', str(args.research_root), '--out', str(output_path)])
                chunk = json.loads(output_path.read_text())
                predictions = checked_predictions(chunk, detector, baseline_rows)
                if set(predictions) != {row['record']['id'] for row in pending}:
                    raise ValueError('The completed batch does not match its requested recordings.')
                if chunk['baseline_sha256'] != sha256(input_path):
                    raise ValueError('The batch input changed during processing.')
                if process.returncode not in (0, 1):
                    raise ValueError(f'CNN batch exited unexpectedly: {process.returncode}')
                results.update(predictions)
                report['parts'].append({'baseline': str(input_path.resolve()),
                                        'predictions': str(output_path.resolve()),
                                        'sha256': sha256(output_path), 'count': len(predictions)})
                last_progress = time.monotonic()
                save()
                print(f'Total predictions: {len(results)}/{len(expected)}.', flush=True)
                continue
            if baseline['status'] == 'finished' and set(results) == set(expected):
                # The producer is finished; now an exact file hash is stable.
                report['baseline_sha256'] = sha256(args.baseline)
                report['status'] = 'finished'
                save()
                print(json.dumps(report['summary']), flush=True)
                return int(report['summary']['failed'] > 0)
            if time.monotonic() - last_progress > 1200:
                raise TimeoutError('No new completed baseline records for 20 minutes; inspect the producer before resuming.')
            time.sleep(10)
    except Exception as error:
        report['status'] = 'incomplete'
        report['error'] = f'{type(error).__name__}: {error}'
        save()
        raise


if __name__ == '__main__':
    raise SystemExit(main())
