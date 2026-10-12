#!/usr/bin/env python3
"""Final, full test of the frozen source-key detector and validation-selected map."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from validate_key_confidence import predict  # Also pins numerical thread limits before NumPy.
import numpy as np

import djmix
from benchmark_key_confidence import annotations, detector_identity, wilson
from calibrate_key_confidence import KEYS, checked_probabilities, scaled_probabilities
from prototype_madmom_key import model_identity
from select_key_confidence import (apply_map, binary_metrics, paired_brier, save_new,
                                    select_candidate, sha256)


def check_test_partition(records, all_records, fit_records):
    expected = {r['id']: r for r in all_records if r['split'] == 'test'}
    actual = {r['id']: r for r in records}
    development = {r['id']: r for r in all_records if r['split'] == 'calibration'}
    if len(actual) != len(records) or actual != expected:
        raise ValueError('The final test must include every pinned test recording exactly once.')
    fitted = {r['id']: r for r in fit_records}
    if len(fitted) != len(fit_records) or fitted != development:
        raise ValueError('Calibration must still use the complete development partition only.')
    earlier_hashes = {r['md5'] for r in all_records if r['split'] != 'test'}
    if earlier_hashes & {r['md5'] for r in records}:
        raise ValueError('Test audio overlaps development or validation.')


def checked_rows(data, expected):
    rows = data.get('results', [])
    actual = {r['record']['id']: r for r in rows}
    if (data.get('split') != 'test' or data.get('selected') != len(expected)
            or len(rows) != len(actual) or set(actual) != set(expected)):
        raise ValueError('Both detectors must cover the full test partition exactly once.')
    for identity, row in actual.items():
        if row['record'] != expected[identity] or row['status'] not in ('estimated', 'failed', 'abstained'):
            raise ValueError('Invalid test annotation or outcome.')
        if row['status'] != 'estimated' and row.get('exact', False):
            raise ValueError('Failed or abstained predictions cannot be counted as correct.')
    return actual


def adjusted_scores(values, plan):
    method = plan['selected_method']
    if method == 'temperature':
        return scaled_probabilities(values, plan['temperature']).max(axis=1)
    return apply_map(values.max(axis=1), plan['model'])


def finish_report(report, baseline, records, plan):
    expected = {r['id']: r for r in records}
    current, reference = checked_rows(report, expected), checked_rows(baseline, expected)
    if baseline.get('status') != 'finished' or baseline.get('detector') != plan['baseline_detector']:
        raise ValueError('The baseline is incomplete or differs from the frozen detector.')
    values, correct = [], []
    for identity in sorted(current):
        row, old = current[identity], reference[identity]
        if old['status'] == 'estimated':
            if not old.get('candidates') or old['exact'] != (old['candidates'][0]['key'] == old['record']['key']):
                raise ValueError('Baseline correctness differs from its reference label.')
        row['baseline_exact'] = bool(old.get('exact', False))
        if row['status'] != 'estimated':
            continue
        candidates = row['candidates']
        by_key = {c['key']: c['softmax'] for c in candidates}
        if len(candidates) != 24 or set(by_key) != set(KEYS):
            raise ValueError('Expected all 24 distinct model scores.')
        value = checked_probabilities([[by_key[k] for k in KEYS]])[0]
        if value[KEYS.index(row['key'])] != value.max() or row['exact'] != (row['key'] == row['record']['key']):
            raise ValueError('Model key/correctness differs from its scores or reference label.')
        if not 0 < row['duration'] <= 60 or row['frames'] != round(row['duration'] * 44100):
            raise ValueError('Unexpected model excerpt.')
        if old['status'] == 'estimated' and row['duration'] != old['duration']:
            raise ValueError('The two detectors used different excerpts.')
        values.append(value)
        correct.append(row['exact'])
    rows = list(current.values())
    hits = sum(bool(r.get('exact', False)) for r in rows)
    report['summary'] = {
        'attempted': len(rows), 'estimated': len(values),
        'failed': sum(r['status'] == 'failed' for r in rows),
        'abstained': sum(r['status'] == 'abstained' for r in rows),
        'exact': hits, 'accuracy_of_attempts': hits / len(rows), 'wilson_95': wilson(hits, len(rows)),
        'baseline_exact': sum(r['baseline_exact'] for r in rows),
        'baseline_failed': sum(r['status'] == 'failed' for r in reference.values()),
        'improved': sum(bool(r.get('exact', False)) and not r['baseline_exact'] for r in rows),
        'regressed': sum(not r.get('exact', False) and r['baseline_exact'] for r in rows),
        'top_three': sum(bool(r.get('top_three', False)) for r in rows)}
    report['confidence_metrics'] = None
    if len(values) == len(rows):
        values, correct = np.array(values), np.array(correct)
        raw, adjusted = values.max(axis=1), adjusted_scores(values, plan)
        constant = np.full(len(rows), plan['constant'])
        measured = {name: binary_metrics(scores, correct) for name, scores in (
            ('raw', raw), ('selected', adjusted), ('constant', constant))}
        comparisons = {name: paired_brier(adjusted, scores, correct)
                       for name, scores in (('raw', raw), ('constant', constant))}
        for value in comparisons.values():
            value['interpretation'] = ('Negative favors the frozen adjustment. Percentile interval '
                                       'assumes independent recordings; unknown related mixes may violate that.')
        report['confidence_metrics'] = {
            'selected_method': plan['selected_method'], 'measurements': measured,
            'selected_minus_baseline_brier': comparisons,
            'improves_both_metrics_over_both_baselines': all(
                measured['selected'][metric] < measured[base][metric]
                for metric in ('brier', 'binary_log_loss') for base in ('raw', 'constant')),
            'key_decisions_unchanged': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--research-root', type=Path, default=Path('outputs/madmom-research'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    plan_path = args.out.with_suffix('.plan.json')
    if args.out.exists() or plan_path.exists():
        parser.error('Choose fresh evidence paths; a repeat is not a new unseen test.')
    selection = json.loads(args.selection.read_text())
    selection_plan_path = Path(selection['plan'])
    selected_plan = json.loads(selection_plan_path.read_text())
    if (selection.get('status') != 'selected_pending_final_test'
            or sha256(selection_plan_path) != selection['plan_sha256']
            or select_candidate(selection['measurements']['validation']) != selection['selected_method']):
        raise ValueError('Use the unchanged validation-selected calibration artifact.')
    for name, digest in selected_plan['source_sha256'].items():
        if sha256(Path(__file__).with_name(name)) != digest:
            raise ValueError(f'Selection source changed: {name}')
    for name, item in selected_plan['inputs'].items():
        if sha256(item['path']) != item['sha256']:
            raise ValueError(f'Selection evidence changed: {name}')
    validation = json.loads(Path(selected_plan['inputs']['validation']['path']).read_text())
    if validation['evaluation_script_sha256'] != sha256(Path(__file__).with_name('validate_key_confidence.py')):
        raise ValueError('Inference wrapper changed since the reference parity check.')
    validation_plan = json.loads(Path(validation['plan']).read_text())
    if (sha256(validation['plan']) != validation['plan_sha256']
            or validation_plan['baseline_detector'] != detector_identity()):
        raise ValueError('Shared audio helpers, baseline or dependencies changed since validation.')
    archive = args.root / 'annotations.zip'
    if sha256(archive) != selected_plan['inputs']['annotations']['sha256']:
        raise ValueError('Annotation archive changed.')
    all_records = annotations(archive)
    records = [r for r in all_records if r['split'] == 'test']
    check_test_partition(records, all_records, selected_plan['fit_records'])
    model_path, identity = model_identity(args.research_root.resolve())
    if identity != selected_plan['reference_detector']:
        raise ValueError('Detector or dependencies changed since development/validation.')
    method = selection['selected_method']
    plan = {'status': 'frozen_before_final_test_inference', 'split': 'test', 'records': records,
            'selected_method': method, 'model': selected_plan['models'].get(method),
            'temperature': selected_plan['temperature'], 'constant': selected_plan['constant'],
            'selection_sha256': sha256(args.selection), 'selection_plan_sha256': sha256(selection_plan_path),
            'reference_detector': identity, 'baseline_detector': detector_identity(),
            'evaluation_script_sha256': sha256(__file__),
            'reference_wrapper_sha256': validation['evaluation_script_sha256'],
            'scope': selected_plan['scope'], 'limitations': selected_plan['limitations'],
            'protocol': ['No refitting, alternate calibration selection, or excerpt selection on test results.',
                         'Retain every attempted recording and failures in accuracy denominators.',
                         'Compare fixed adjustment with raw score and development accuracy constant.',
                         'Report binary Brier/log loss and paired uncertainty; no automatic production promotion.']}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_new(plan_path, plan)
    from madmom.features.key import CNNKeyRecognitionProcessor, KEY_LABELS
    processor = CNNKeyRecognitionProcessor(nn_files=[str(model_path)])
    labels = [djmix.key_name(djmix.parse_key(label)) for label in KEY_LABELS]
    report = {'status': 'running', 'split': 'test', 'selected': len(records), 'plan': str(plan_path.resolve()),
              'plan_sha256': sha256(plan_path), 'scope': plan['scope'], 'results': []}

    def save():
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        temporary.replace(args.out)

    save()
    try:
        for record in records:
            started = time.monotonic()
            try:
                row = predict(record, args.root / 'audio' / (record['id'] + '.mp3'), processor, labels)
            except Exception as error:
                row = {'record': record, 'status': 'failed', 'error': f'{type(error).__name__}: {error}'}
            row['seconds'] = round(time.monotonic() - started, 3)
            report['results'].append(row)
            save()
            print(f'Final test {len(report["results"])}/{len(records)}: {record["id"]} {row["status"]}', flush=True)
        report['status'] = 'predictions_finished_awaiting_baseline'
        save()
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            if args.baseline.exists():
                baseline_bytes = args.baseline.read_bytes()
                baseline = json.loads(baseline_bytes)
                if baseline.get('status') == 'finished':
                    finish_report(report, baseline, records, plan)
                    report.update(status='finished', baseline=str(args.baseline.resolve()),
                                  baseline_sha256=hashlib.sha256(baseline_bytes).hexdigest())
                    save()
                    print(json.dumps(report['summary']), flush=True)
                    return int(report['summary']['failed'] > 0 or report['summary']['baseline_failed'] > 0)
            time.sleep(10)
        raise TimeoutError('Predictions saved; the baseline did not finish within 30 minutes.')
    except Exception as error:
        report.update(status='incomplete', error=f'{type(error).__name__}: {error}')
        save()
        raise


if __name__ == '__main__':
    raise SystemExit(main())
