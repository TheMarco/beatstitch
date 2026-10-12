#!/usr/bin/env python3
"""One frozen final test of a selected broad-BPM confidence candidate.

Use the unchanged detector source snapshot with the matching runtime. Resume an
interrupted ordered run without changing its model, acceptance rule or inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sys

ACCEPTANCE = ('At least 100 estimated recordings, no failed recordings, lower binary log loss '
              'than the frozen development-accuracy constant, and an upper paired-bootstrap '
              '95% Brier-difference bound below zero. Passing supports further full-song/AI '
              'validation; it does not establish exact timing confidence or authorize an app label.')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_resume(report, plan, plan_sha):
    from benchmark_beat_tempo_incremental import validate_rows
    if (report.get('plan_sha256') != plan_sha or report.get('split') != 'test'
            or report.get('selected') != len(plan['records'])
            or report.get('status') not in ('running', 'partial', 'finished')):
        raise ValueError('Final-test report differs from the frozen plan.')
    before, after = report['baseline_results'], report['results']
    if len(before) != len(after):
        raise ValueError('Final test needs paired outcomes for every attempted recording.')
    validate_rows(before, plan['records'], plan['baseline_detector'])
    validate_rows(after, plan['records'], plan['candidate_detector'])
    if report['status'] == 'finished' and len(after) != len(plan['records']):
        raise ValueError('A finished final test must account for the entire reserved partition.')


def confidence_result(before, after, model, constant, *, complete):
    import numpy as np
    from benchmark_key_confidence import wilson
    from select_tempo_confidence import binary_metrics, feature_row, paired_brier, predict
    rows = [(beat, opening) for opening, beat in zip(before, after) if beat['status'] == 'estimated']
    if not rows:
        return {'estimated': 0, 'decision': 'No estimates to evaluate.'}
    values = [feature_row(beat, opening) for beat, opening in rows]
    scores = predict(values, model)
    outcomes = np.asarray([int(beat['score']['accuracy1_4pct']) for beat, _ in rows])
    baseline = np.full(len(rows), constant)
    metrics = binary_metrics(scores, outcomes)
    base_metrics = binary_metrics(baseline, outcomes)
    difference = paired_brier(scores, baseline, outcomes)
    difference['interpretation'] = 'Negative favors the frozen candidate over the development constant on reserved recordings.'
    failed = sum(row['status'] == 'failed' for row in before + after)
    passed = (complete and len(rows) >= 100 and failed == 0
              and metrics['binary_log_loss'] < base_metrics['binary_log_loss']
              and difference['paired_bootstrap_95'][1] < 0)
    bands = []
    for low, high in ((0., .5), (.5, .75), (.75, .9), (.9, 1.)):
        selected = (scores >= low) & ((scores < high) if high < 1 else (scores <= high))
        count = int(selected.sum()); hits = int(outcomes[selected].sum())
        bands.append({'minimum': low, 'maximum': high, 'count': count, 'correct': hits,
                      'mean_score': float(scores[selected].mean()) if count else None,
                      'observed_accuracy': hits/count if count else None, 'wilson_95': wilson(hits, count)})
    return {'estimated': len(rows), 'candidate': metrics, 'constant': base_metrics,
            'brier_difference': difference, 'bands': bands, 'acceptance_passed': passed,
            'decision': ('Provisional benefit supported; validate full songs and AI music before app use.' if passed else
                         'Do not ship confidence: frozen acceptance rule not met.' if complete else
                         'Incomplete final test; no acceptance decision.'),
            'predictions': [{'id': beat['record']['id'], 'score': float(scores[i]),
                             'correct_within_4pct': bool(outcomes[i])} for i, (beat, _) in enumerate(rows)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--reuse-audio', type=Path, default=Path('outputs/key-benchmark/audio'))
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--max-new', type=int)
    args = parser.parse_args()
    if args.max_new is not None and args.max_new < 1:
        parser.error('max-new must be positive.')
    # The app import path changed after detector selection; use the pinned old
    # source to reproduce that experiment rather than silently changing methods.
    sys.path.insert(0, str(args.snapshot.resolve()))
    from benchmark_tempo import annotations, detector_identity, evaluate as opening_evaluate, summarize
    from prototype_beat_this_tempo import evaluate as beat_evaluate, identity
    from validate_tempo_detectors import prepare_audio, read_planned_report
    selection, selection_plan = read_planned_report(args.selection)
    if selection.get('status') != 'finished' or selection.get('selected') not in selection.get('models', {}):
        parser.error('Require a completed selection with a frozen candidate.')
    for path, expected in selection_plan['input_sha256'].items():
        if sha256(path) != expected:
            parser.error('A confidence-selection input changed.')
    for name, expected in selection_plan['source_sha256'].items():
        module = importlib.import_module(Path(name).stem)
        if sha256(module.__file__) != expected:
            parser.error('A confidence-selection implementation changed: ' + name)
    baseline_detector, candidate_detector = detector_identity(), identity()
    if (baseline_detector != selection_plan['opening_detector']
            or candidate_detector != selection_plan['beat_detector']):
        parser.error('Detector sources, models or runtime differ from the frozen selection.')
    records = [r for r in annotations(args.root/'annotations.zip') if r['bpm'] and r['split'] == 'test']
    model = selection['models'][selection['selected']]
    constant = selection['development_constant']
    plan = {'status': 'frozen_before_test_inference', 'split': 'test', 'records': records,
            'selection_sha256': sha256(args.selection), 'selection_plan_sha256': selection['plan_sha256'],
            'runner_sha256': sha256(__file__), 'candidate_detector': candidate_detector,
            'baseline_detector': baseline_detector, 'model': model, 'constant': constant,
            'acceptance_rule': ACCEPTANCE, 'audio_and_cache_root': str(args.root.resolve()),
            'limitations': selection_plan['limitations']}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    plan_path = args.out.with_suffix('.plan.json')
    if plan_path.exists():
        if json.loads(plan_path.read_text()) != plan:
            parser.error('The final test is frozen. Do not change the experiment on resume.')
    else:
        if args.out.exists():
            parser.error('Existing final-test report has no plan.')
        with plan_path.open('x') as handle:
            json.dump(plan, handle, indent=2, allow_nan=False)
    plan_sha = sha256(plan_path)
    if args.out.exists():
        report = json.loads(args.out.read_text())
        check_resume(report, plan, plan_sha)
    else:
        report = {'status': 'running', 'split': 'test', 'selected': len(records),
                  'plan': str(plan_path.resolve()), 'plan_sha256': plan_sha,
                  'baseline_results': [], 'results': [], 'reused': {'baseline': 0, 'candidate': 0}}

    def save():
        report['baseline_summary'] = summarize(report['baseline_results'])
        report['summary'] = summarize(report['results'])
        # Fit nothing and make the inferential decision only after all attempts.
        if report['status'] == 'finished':
            report['confidence'] = confidence_result(report['baseline_results'], report['results'],
                                                      model, constant, complete=True)
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        temporary.replace(args.out)

    if report['status'] == 'finished':
        print('Final test already finished; no inference or data changes.')
        return 0
    report['status'] = 'running'; save()
    start = len(report['results'])
    stop = min(len(records), start + args.max_new) if args.max_new else len(records)
    for record in records[start:stop]:
        if shutil.disk_usage(args.root).free < 4 * 1024**3:
            report['status'] = 'partial'; save()
            raise RuntimeError('Final test paused before the next recording: less than 4 GiB free.')
        input_error = None
        try:
            prepare_audio(record, args.root, args.reuse_audio, args.download)
        except Exception as error:
            input_error = f'{type(error).__name__}: {error}'
        for name, evaluate, detector, field in (
                ('baseline', opening_evaluate, baseline_detector, 'baseline_results'),
                ('candidate', beat_evaluate, candidate_detector, 'results')):
            try:
                if input_error:
                    raise ValueError(input_error)
                row, reused = evaluate(record, args.root, detector)
            except Exception as error:
                row, reused = {'record': record, 'detector': detector, 'status': 'failed',
                               'error': f'{type(error).__name__}: {error}'}, False
            report[field].append(row); report['reused'][name] += int(reused)
        save()
        print(f'{len(report["results"])}/{len(records)} {record["id"]}: '
              f'opening={report["baseline_results"][-1]["status"]} '
              f'beat={report["results"][-1]["status"]}', flush=True)
    report['status'] = 'finished' if len(report['results']) == len(records) else 'partial'
    save()
    print(json.dumps({'status': report['status'], 'completed': len(report['results']),
                      'decision': report.get('confidence', {}).get('decision')}))
    return int(report['summary']['failed'] + report['baseline_summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
