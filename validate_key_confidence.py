#!/usr/bin/env python3
"""Validate the frozen key CNN and temperature on the complete unused validation split."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

for _variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[_variable] = '1'

import numpy as np

import djmix
from benchmark_key_confidence import REVISION, annotations, detector_identity, wilson
from calibrate_key_confidence import KEYS, metrics, scaled_probabilities
from prototype_madmom_key import model_identity


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_partition(fit_records, records, all_records):
    expected_fit = {r['id']: r for r in all_records if r['split'] == 'calibration'}
    expected_validation = {r['id']: r for r in all_records if r['split'] == 'validation'}
    fitted = {r['id']: r for r in fit_records}
    actual = {r['id']: r for r in records}
    if len(fitted) != len(fit_records) or fitted != expected_fit:
        raise ValueError('Fitted records do not match the complete pinned development partition.')
    if len(actual) != len(records) or actual != expected_validation:
        raise ValueError('Evaluate every pinned validation record exactly once.')
    if {r['md5'] for r in fit_records} & {r['md5'] for r in records}:
        raise ValueError('Development and validation contain overlapping audio.')


def predict(record, source, processor, labels):
    """Same FFmpeg/Signal/CNN pipeline as the unchanged development prototype."""
    if hashlib.md5(source.read_bytes()).hexdigest() != record['md5']:
        raise ValueError('Audio differs from its published checksum.')
    duration = min(60., djmix.probe(source)['duration'])
    decoded = djmix.ffmpeg('-i', str(source), '-t', str(duration), '-map', '0:a:0',
                           '-ar', '44100', '-ac', '1', '-f', 'f32le', '-').stdout
    audio = np.frombuffer(decoded, dtype='<f4').copy()
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError('Decoded audio is empty or non-finite.')
    result = {'record': record, 'duration': duration, 'frames': len(audio)}
    if np.max(np.abs(audio)) < 1e-5:
        return {**result, 'status': 'abstained', 'reason': 'Effectively silent excerpt.'}
    from madmom.audio.signal import Signal
    prediction = np.asarray(processor(Signal(audio, sample_rate=44100)))
    if (prediction.shape != (1, 24) or not np.isfinite(prediction).all()
            or np.min(prediction) < 0 or not np.isclose(prediction.sum(), 1., atol=1e-5)):
        raise ValueError('Invalid key model output.')
    scores = prediction[0]
    candidates = [{'key': labels[index], 'softmax': float(scores[index])} for index in np.argsort(-scores)]
    return {**result, 'status': 'estimated', 'key': candidates[0]['key'], 'candidates': candidates,
            'exact': candidates[0]['key'] == record['key'],
            'top_three': any(c['key'] == record['key'] for c in candidates[:3])}


def check_reference(old, new):
    for field in ('record', 'status', 'duration', 'frames', 'key', 'exact', 'top_three'):
        if old.get(field) != new.get(field):
            raise ValueError(f'Validation wrapper differs from the development reference: {field}.')
    previous = {c['key']: c['softmax'] for c in old['candidates']}
    current = {c['key']: c['softmax'] for c in new['candidates']}
    if set(previous) != set(KEYS) or set(current) != set(KEYS):
        raise ValueError('Reference class labels differ.')
    a, b = np.array([previous[k] for k in KEYS]), np.array([current[k] for k in KEYS])
    if not np.allclose(a, b, atol=1e-7, rtol=1e-6):
        raise ValueError('Validation wrapper does not reproduce the saved development scores.')
    return float(np.max(np.abs(a - b)))


def finish_report(report, baseline, expected, temperature):
    rows = report['results']
    known = {r['record']['id']: r for r in baseline['results']}
    actual = {r['record']['id']: r for r in rows}
    if (baseline.get('status') != 'finished' or baseline.get('split') != 'validation'
            or baseline.get('selected') != len(expected) or len(known) != len(baseline['results'])
            or len(actual) != len(rows) or set(known) != set(expected) or set(actual) != set(expected)):
        raise ValueError('Both finished predictions and baseline must cover the entire validation partition.')
    for key, row in actual.items():
        old = known[key]
        if row['record'] != expected[key] or old['record'] != expected[key]:
            raise ValueError('Validation annotations changed.')
        if row['status'] == 'estimated' and old['status'] == 'estimated':
            if row['duration'] != old['duration'] or row['frames'] != round(old['duration'] * 44100):
                raise ValueError('The two detectors analyzed different excerpts.')
        row['baseline_exact'] = old.get('exact', False)
    correct = sum(r.get('exact', False) for r in rows)
    report['summary'] = {
        'attempted': len(rows), 'estimated': sum(r['status'] == 'estimated' for r in rows),
        'failed': sum(r['status'] == 'failed' for r in rows),
        'abstained': sum(r['status'] == 'abstained' for r in rows),
        'exact': correct, 'accuracy_of_attempts': correct / len(rows), 'wilson_95': wilson(correct, len(rows)),
        'baseline_exact': sum(r['baseline_exact'] for r in rows),
        'baseline_failed': sum(r['status'] == 'failed' for r in known.values()),
        'improved': sum(r.get('exact', False) and not r['baseline_exact'] for r in rows),
        'regressed': sum(not r.get('exact', False) and r['baseline_exact'] for r in rows),
        'top_three': sum(r.get('top_three', False) for r in rows)}
    report['confidence_metrics'] = None
    if all(r['status'] == 'estimated' for r in rows):
        values = np.array([[next(c['softmax'] for c in row['candidates'] if c['key'] == key)
                            for key in KEYS] for row in rows])
        targets = np.array([KEYS.index(row['record']['key']) for row in rows])
        adjusted = scaled_probabilities(values, temperature)
        before, after = metrics(values, targets), metrics(adjusted, targets)
        if before['exact'] != correct or not np.array_equal(values.argmax(axis=1), adjusted.argmax(axis=1)):
            raise ValueError('Key ordering changed; inspect tied predictions before interpreting this evaluation.')
        report['confidence_metrics'] = {'raw': before, 'temperature_scaled': after,
            'rankings_unchanged': True,
            'nll_improved': after['negative_log_likelihood'] < before['negative_log_likelihood'],
            'top_label_brier_improved': after['top_label_brier'] < before['top_label_brier']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--fit', type=Path, required=True)
    parser.add_argument('--development-report', type=Path, required=True)
    parser.add_argument('--research-root', type=Path, default=Path('outputs/madmom-research'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    plan_path = args.out.with_suffix('.plan.json')
    if args.out.exists() or plan_path.exists():
        parser.error('Choose new output and plan paths; existing evidence is preserved.')
    archive = args.root / 'annotations.zip'
    all_records = annotations(archive)
    records = [row for row in all_records if row['split'] == 'validation']
    fit = json.loads(args.fit.read_text())
    if (fit.get('status') != 'fitted_not_validated' or fit.get('method') != 'temperature_scaling'
            or fit['dataset_revision'] != REVISION or fit['annotation_sha256'] != sha256(archive)
            or fit['input_sha256'] != sha256(args.development_report)
            or fit['script_sha256'] != sha256(Path(__file__).with_name('calibrate_key_confidence.py'))
            or fit['probability_floor'] != 1e-12 or fit['keys'] != list(KEYS)):
        raise ValueError('Use the unchanged fitted artifact, reference predictions and scaling implementation.')
    temperature = float(fit['temperature'])
    if not np.isfinite(temperature) or not .25 < temperature < 8.:
        raise ValueError('Invalid fitted temperature.')
    check_partition(fit['fit_records'], records, all_records)
    model, reference_identity = model_identity(args.research_root.resolve())
    if reference_identity != fit['detector']:
        raise ValueError('Reference detector or dependencies changed since fitting.')
    from madmom.features.key import CNNKeyRecognitionProcessor, KEY_LABELS
    processor = CNNKeyRecognitionProcessor(nn_files=[str(model)])
    labels = [djmix.key_name(djmix.parse_key(label)) for label in KEY_LABELS]
    development = json.loads(args.development_report.read_text())
    original = sorted(development['results'], key=lambda row: row['record']['id'])
    parity = []
    for index in (0, len(original) // 2, len(original) - 1):
        old = original[index]
        new = predict(old['record'], args.root / 'audio' / (old['record']['id'] + '.mp3'), processor, labels)
        parity.append({'id': old['record']['id'], 'maximum_score_error': check_reference(old, new)})
    plan = {'status': 'frozen_before_validation_inference', 'split': 'validation',
            'selected': len(records), 'records': records, 'temperature': temperature,
            'fit_sha256': sha256(args.fit), 'reference_detector': reference_identity,
            'baseline_detector': detector_identity(), 'reference_parity': parity,
            'evaluation_script_sha256': sha256(__file__),
            'scope': fit['scope'], 'checks': [
                'Compare both detectors on every validation recording, retaining failures.',
                'Compare raw and frozen-temperature NLL, Brier scores and reliability bins.',
                'Do not refit, change the model, or select excerpts using validation results.',
                'Do not inspect the final test partition.']}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with plan_path.open('x', encoding='utf-8') as handle:
        json.dump(plan, handle, indent=2, allow_nan=False)
    print('Reference parity passed on three fixed development recordings. Validation plan frozen.', flush=True)
    report = {'status': 'running', 'split': 'validation', 'selected': len(records),
              'plan': str(plan_path.resolve()), 'plan_sha256': sha256(plan_path),
              'reference_detector': reference_identity,
              'evaluation_script_sha256': sha256(__file__), 'temperature': temperature,
              'scope': fit['scope'], 'results': [], 'limitations': fit['limitations'] + [
                  'Validation results describe this EDM dataset and first-minute protocol only.',
                  'Unknown related recordings and remixes may remain across partitions.']}

    def save():
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
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
            print(f'Validation {len(report["results"])}/{len(records)}: {record["id"]} {row["status"]}', flush=True)
        report['status'] = 'predictions_finished_awaiting_baseline'
        save()
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            if args.baseline.exists():
                baseline_bytes = args.baseline.read_bytes()
                baseline = json.loads(baseline_bytes)
                if baseline['detector'] != plan['baseline_detector']:
                    raise ValueError('Baseline detector differs from the frozen validation plan.')
                if baseline.get('status') == 'finished':
                    finish_report(report, baseline, {r['id']: r for r in records}, temperature)
                    report['baseline_sha256'] = hashlib.sha256(baseline_bytes).hexdigest()
                    report['baseline'] = str(args.baseline.resolve())
                    report['status'] = 'finished'
                    save()
                    print(json.dumps(report['summary']), flush=True)
                    return int(report['summary']['failed'] > 0 or report['summary']['baseline_failed'] > 0)
            time.sleep(10)
        raise TimeoutError('Validation predictions are saved; baseline has not finished after 30 minutes.')
    except Exception as error:
        report.update(status='incomplete', error=f'{type(error).__name__}: {error}')
        save()
        raise


if __name__ == '__main__':
    raise SystemExit(main())
