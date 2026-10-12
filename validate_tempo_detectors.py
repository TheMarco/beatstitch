#!/usr/bin/env python3
"""Compare unchanged BPM detectors on the fixed validation partition only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from benchmark_beat_tempo_incremental import validate_baseline, validate_resume, validate_rows
from benchmark_key_confidence import AUDIO_URL, download
from benchmark_tempo import (annotations, detector_identity, evaluate as evaluate_baseline,
                             sha256, summarize)
from prototype_beat_this_tempo import compare, evaluate as evaluate_candidate, identity


def read_planned_report(path):
    report = json.loads(path.read_text())
    plan_path = Path(report['plan'])
    if sha256(plan_path) != report['plan_sha256']:
        raise ValueError('Report plan changed after the experiment was frozen.')
    return report, json.loads(plan_path.read_text())


def check_development(baseline, baseline_plan, candidate, candidate_plan, records,
                      baseline_detector, candidate_detector):
    if (baseline_plan['records'] != records or candidate_plan['records'] != records
            or baseline['status'] != 'finished' or candidate['status'] != 'finished'
            or len(candidate['results']) != len(records)
            or baseline_plan['detector'] != baseline_detector
            or candidate_plan['detector'] != candidate_detector
            or candidate_plan['baseline_plan_sha256'] != baseline['plan_sha256']):
        raise ValueError('Finish the full development comparison with unchanged detectors first.')
    validate_baseline(baseline, baseline_plan, baseline['plan_sha256'])
    validate_resume(candidate, candidate_plan, candidate['plan_sha256'], baseline)
    validate_rows(baseline['results'], records, baseline_detector)


def check_resume(report, plan, plan_sha):
    if (report.get('plan_sha256') != plan_sha or report.get('split') != 'validation'
            or report.get('selected') != len(plan['records'])
            or report.get('status') not in ('running', 'finished')):
        raise ValueError('Validation report no longer matches its frozen plan.')
    before, after = report['baseline_results'], report['results']
    if len(before) != len(after):
        raise ValueError('Every attempted recording needs both detector outcomes.')
    validate_rows(before, plan['records'], plan['baseline_detector'])
    validate_rows(after, plan['records'], plan['candidate_detector'])
    if report['status'] == 'finished' and len(after) != len(plan['records']):
        raise ValueError('Finished validation must account for the complete partition.')


def prepare_audio(record, root, reuse_audio, allow_download):
    path = root / 'audio' / (record['id'] + '.mp3')
    prior = reuse_audio / path.name
    if not path.exists() and prior.exists():
        if hashlib.md5(prior.read_bytes()).hexdigest() != record['md5']:
            raise ValueError('Existing audio differs from the published checksum.')
        path.parent.mkdir(parents=True, exist_ok=True)
        os.link(prior, path)
    if allow_download:
        download(AUDIO_URL + path.name, path, maximum=16 * 1024 * 1024, md5=record['md5'])
    if hashlib.md5(path.read_bytes()).hexdigest() != record['md5']:
        raise ValueError('Validation audio differs from the published checksum.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--development-baseline', type=Path, required=True)
    parser.add_argument('--development-candidate', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--reuse-audio', type=Path, default=Path('outputs/key-benchmark/audio'))
    args = parser.parse_args()
    catalog = annotations(args.root / 'annotations.zip')
    development = [r for r in catalog if r['bpm'] and r['split'] == 'calibration']
    records = [r for r in catalog if r['bpm'] and r['split'] == 'validation']
    baseline_detector, candidate_detector = detector_identity(), identity()
    baseline, baseline_plan = read_planned_report(args.development_baseline)
    candidate, candidate_plan = read_planned_report(args.development_candidate)
    check_development(baseline, baseline_plan, candidate, candidate_plan, development,
                      baseline_detector, candidate_detector)
    plan = {'status': 'frozen_before_inference', 'split': 'validation', 'records': records,
            'baseline_detector': baseline_detector, 'candidate_detector': candidate_detector,
            'runner_sha256': sha256(__file__),
            'guard_sha256': sha256(Path(__file__).with_name('benchmark_beat_tempo_incremental.py')),
            'download_helper_sha256': sha256(Path(__file__).with_name('benchmark_key_confidence.py')),
            'development_baseline_sha256': sha256(args.development_baseline),
            'development_candidate_sha256': sha256(args.development_candidate),
            'purpose': 'Fixed detector comparison; no confidence fitting or final-test access.'}
    plan_path = args.out.with_suffix('.plan.json')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if plan_path.exists():
        if json.loads(plan_path.read_text()) != plan:
            parser.error('The frozen experiment changed. Choose a new report path.')
    else:
        if args.out.exists():
            parser.error('An existing report has no plan; it will not be overwritten.')
        with plan_path.open('x') as handle:
            json.dump(plan, handle, indent=2, allow_nan=False)
    plan_sha = sha256(plan_path)
    if args.out.exists():
        report = json.loads(args.out.read_text())
        check_resume(report, plan, plan_sha)
    else:
        report = {'status': 'running', 'split': 'validation', 'selected': len(records),
                  'plan': str(plan_path.resolve()), 'plan_sha256': plan_sha,
                  'baseline_results': [], 'results': [],
                  'reused': {'baseline': 0, 'candidate': 0},
                  'limitations': [
                      'Global BPM labels do not validate exact beat placement or drift correction.',
                      'Accuracy2 accepts tempo multiples that can still mislead a DJ arrangement.',
                      'Failures and abstentions remain in both accuracy denominators.',
                      'Beat This training-recording overlap is unaudited; no unseen-data claim.',
                      'Validation can inform selection; it is not a final test or calibrated confidence.',
                      'The app review button uses the initial beat estimate; refinement is reported separately.']}

    def save():
        report['baseline_summary'] = summarize(report['baseline_results'])
        report['summary'] = summarize(report['results'])
        report['comparison'] = compare(report['baseline_results'], report['results'])
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        temporary.replace(args.out)

    save()
    for record in records[len(report['results']):]:
        input_error = None
        try:
            prepare_audio(record, args.root, args.reuse_audio, args.download)
        except Exception as error:
            input_error = f'{type(error).__name__}: {error}'
        for name, evaluate, detector, field in (
                ('baseline', evaluate_baseline, baseline_detector, 'baseline_results'),
                ('candidate', evaluate_candidate, candidate_detector, 'results')):
            try:
                if input_error:
                    raise ValueError(input_error)
                row, reused = evaluate(record, args.root, detector)
            except Exception as error:
                row, reused = {'record': record, 'detector': detector, 'status': 'failed',
                               'error': f'{type(error).__name__}: {error}'}, False
                if input_error:
                    row['failure_stage'] = 'audio_input'
            report[field].append(row)
            report['reused'][name] += int(reused)
        save()
        print(f'{len(report["results"])}/{len(records)} {record["id"]}: '
              f'baseline={report["baseline_results"][-1]["status"]} '
              f'candidate={report["results"][-1]["status"]}', flush=True)
    report['status'] = 'finished'
    save()
    print(json.dumps({'status': report['status'], 'selected': report['selected'],
                      'baseline_summary': report['baseline_summary'], 'summary': report['summary']}))
    return int(report['summary']['failed'] + report['baseline_summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
