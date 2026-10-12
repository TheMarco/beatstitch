#!/usr/bin/env python3
"""Resume a fixed development comparison over the completed baseline prefix.

This consumes one atomic baseline snapshot and exits when that snapshot is
processed. A partial report is never represented as the complete experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from benchmark_tempo import annotations, score, sha256, summarize
from prototype_beat_this_tempo import compare, evaluate, identity


def validate_rows(rows, records, detector=None):
    if len(rows) > len(records) or [r.get('record') for r in rows] != records[:len(rows)]:
        raise ValueError('Results must be the exact ordered prefix of the frozen selection.')
    for row in rows:
        if row.get('status') not in ('estimated', 'abstained', 'failed'):
            raise ValueError('Invalid recording outcome.')
        if row['status'] == 'estimated':
            for bpm, field in (('initial_bpm', 'initial_score'), ('bpm', 'score')):
                if row.get(field) != score(row[bpm], row['record']['bpm']):
                    raise ValueError('Saved BPM scores do not match the estimates and reference.')
        if detector is not None and row.get('detector') != detector:
            raise ValueError('Detector identity differs from the frozen plan.')


def validate_baseline(report, plan, plan_sha):
    if (report.get('plan_sha256') != plan_sha or report.get('detector') != plan['detector']
            or report.get('selected') != len(plan['records']) or report.get('split') != 'calibration'
            or report.get('status') not in ('running', 'finished')):
        raise ValueError('Baseline no longer matches its frozen development plan.')
    validate_rows(report['results'], plan['records'])
    if report['status'] == 'finished' and len(report['results']) != len(plan['records']):
        raise ValueError('A finished baseline must account for every selected recording.')


def validate_resume(report, plan, plan_sha, baseline):
    if (report.get('plan_sha256') != plan_sha or report.get('detector') != plan['detector']
            or report.get('selected') != len(plan['records'])):
        raise ValueError('Comparison differs from the frozen plan; choose a new experiment.')
    validate_rows(report['results'], plan['records'], plan['detector'])
    count = len(report['results'])
    if count > len(baseline['results']) or report.get('baseline_results') != baseline['results'][:count]:
        raise ValueError('Previously paired baseline results changed or disappeared.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    baseline_bytes = args.baseline.read_bytes()
    baseline = json.loads(baseline_bytes)
    baseline_plan_path = Path(baseline['plan'])
    baseline_plan_sha = sha256(baseline_plan_path)
    baseline_plan = json.loads(baseline_plan_path.read_text())
    catalog = [r for r in annotations(args.root / 'annotations.zip') if r['bpm'] and r['split'] == 'calibration']
    if baseline_plan['records'] != catalog:
        parser.error('Use the full, fixed development selection; validation and test records are excluded.')
    validate_baseline(baseline, baseline_plan, baseline_plan_sha)
    detector = identity()
    plan = {'status': 'frozen_before_inference', 'records': catalog, 'detector': detector,
            'runner_sha256': sha256(__file__), 'baseline_plan_sha256': baseline_plan_sha,
            'baseline': str(args.baseline.resolve()),
            'purpose': 'Development comparison, not final accuracy or calibrated confidence.'}
    plan_path = args.out.with_suffix('.plan.json')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if plan_path.exists():
        if json.loads(plan_path.read_text()) != plan:
            parser.error('The frozen experiment changed. Use a new report path.')
    else:
        if args.out.exists():
            parser.error('An existing report has no plan; do not overwrite it.')
        with plan_path.open('x') as handle:
            json.dump(plan, handle, indent=2, allow_nan=False)
    plan_sha = sha256(plan_path)
    if args.out.exists():
        report = json.loads(args.out.read_text())
        validate_resume(report, plan, plan_sha, baseline)
    else:
        report = {'selected': len(catalog), 'plan': str(plan_path.resolve()), 'plan_sha256': plan_sha,
                  'detector': detector, 'results': [], 'baseline_results': [], 'reused_count': 0,
                  'limitations': baseline['limitations'] + [
                      'Partial results are the completed prefix, not the full development experiment.',
                      'Beat This training-recording overlap has not been audited; no unseen-data claim.',
                      'Model scores, window count, residual and coverage are not calibrated confidence.',
                      'The candidate is a reviewable second opinion; the default import detector is unchanged.']}
    report.update(status='running', baseline_available=len(baseline['results']),
                  baseline_snapshot_sha256=hashlib.sha256(baseline_bytes).hexdigest())

    def save():
        report['summary'] = summarize(report['results'])
        report['paired_baseline_summary'] = summarize(report['baseline_results'])
        report['comparison'] = compare(report['baseline_results'], report['results'])
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        temporary.replace(args.out)

    save()
    for before in baseline['results'][len(report['results']):]:
        record = before['record']
        try:
            row, reused = evaluate(record, args.root, detector)
        except Exception as error:
            row, reused = {'record': record, 'detector': detector, 'status': 'failed',
                           'error': f'{type(error).__name__}: {error}'}, False
        report['baseline_results'].append(before)
        report['results'].append(row)
        report['reused_count'] += int(reused)
        save()
        print(f'{len(report["results"])}/{len(catalog)} {record["id"]}: {row["status"]} '
              f'{row.get("bpm", row.get("error"))} reference={record["bpm"]} reused={reused}', flush=True)
    report['status'] = 'finished' if len(report['results']) == len(catalog) else 'partial'
    save()
    print(json.dumps({'status': report['status'], 'completed': len(report['results']),
                      'selected': len(catalog), 'summary': report['summary'], 'comparison': report['comparison']}))
    return int(report['summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
