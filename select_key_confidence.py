#!/usr/bin/env python3
"""Select source-key correctness calibration; no audio inference or test-set access.

Fit monotone logit and beta maps on development predictions only. Compare on
validation, then freeze a choice for a separate final test. These maps estimate
whether the selected key is correct, not a distribution over all 24 keys.
Beta calibration: https://proceedings.mlr.press/v54/kull17a.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

from benchmark_key_confidence import REVISION, annotations, wilson
from calibrate_key_confidence import (KEYS, checked_probabilities,
                                      load_development_report, scaled_probabilities)

FLOOR = 1e-12
METHODS = ('logit', 'beta')
SELECTION_RULE = ('Lowest validation Brier among temperature, logit and beta maps that '
                  'beat both raw confidence and the development accuracy constant in '
                  'Brier and binary log loss; ties prefer temperature, then logit. '
                  'Otherwise select no adjustment. No fitting on validation or test.')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_scores(scores):
    scores = np.asarray(scores, dtype=float)
    if (scores.ndim != 1 or not len(scores) or not np.isfinite(scores).all()
            or np.any(scores < 0) or np.any(scores > 1)):
        raise ValueError('Expected a nonempty vector of finite probabilities in [0, 1].')
    return scores


def checked_outcomes(outcomes, count):
    outcomes = np.asarray(outcomes)
    if outcomes.shape != (count,) or not np.isin(outcomes, [0, 1]).all():
        raise ValueError('Expected one binary correctness outcome per score.')
    return outcomes.astype(float)


def features(scores, method):
    scores = np.clip(checked_scores(scores), FLOOR, 1 - FLOOR)
    if method == 'logit':
        return np.column_stack((logit(scores), np.ones(len(scores))))
    if method == 'beta':
        return np.column_stack((np.log(scores), -np.log1p(-scores), np.ones(len(scores))))
    raise ValueError('Unknown calibration method.')


def apply_map(scores, model):
    matrix = features(scores, model['method'])
    coefficients = np.asarray(model['coefficients'], dtype=float)
    if (coefficients.shape != (matrix.shape[1],) or not np.isfinite(coefficients).all()
            or np.any(coefficients[:-1] < 0)):
        raise ValueError('Invalid monotone calibration coefficients.')
    return expit(matrix @ coefficients)


def fit_map(scores, outcomes, method):
    matrix = features(scores, method)
    outcomes = checked_outcomes(outcomes, len(matrix))
    if not 0 < outcomes.mean() < 1:
        raise ValueError('Fit requires both correct and incorrect development predictions.')

    def objective(coefficients):
        logits = matrix @ coefficients
        loss = np.mean(np.logaddexp(0, logits) - outcomes * logits)
        gradient = matrix.T @ (expit(logits) - outcomes) / len(outcomes)
        return float(loss), gradient

    initial = np.zeros(matrix.shape[1])
    initial[-1] = logit(outcomes.mean())
    result = minimize(objective, initial, jac=True, method='L-BFGS-B',
                      bounds=[(0, None)] * (len(initial) - 1) + [(None, None)],
                      options={'ftol': 1e-13, 'gtol': 1e-9, 'maxiter': 2000})
    if not result.success or not np.isfinite(result.fun) or not np.isfinite(result.x).all():
        raise ValueError(f'Calibration fit failed: {result.message}')
    # An optimizer success flag alone is insufficient (e.g. premature ftol stop).
    gradient = objective(result.x)[1]
    gradient[:-1] = np.where(result.x[:-1] <= 1e-10, np.minimum(gradient[:-1], 0), gradient[:-1])
    if np.max(np.abs(gradient)) > 1e-5:
        raise ValueError('Calibration fit has not reached a constrained stationary point.')
    return {'method': method, 'coefficients': result.x.tolist(),
            'objective': 'binary_log_loss', 'nonnegative_slopes': True,
            'development_log_loss': float(result.fun)}


def binary_metrics(scores, outcomes):
    scores = checked_scores(scores)
    outcomes = checked_outcomes(outcomes, len(scores))
    bounded = np.clip(scores, FLOOR, 1 - FLOOR)
    bins = []
    for index in range(10):
        low, high = index / 10, (index + 1) / 10
        mask = (scores >= low) & ((scores < high) if index < 9 else (scores <= high))
        count, hits = int(mask.sum()), int(outcomes[mask].sum())
        bins.append({'minimum': low, 'maximum': high, 'count': count, 'exact': hits,
                     'mean_score': float(scores[mask].mean()) if count else None,
                     'observed_accuracy': hits / count if count else None,
                     'wilson_95': wilson(hits, count)})
    return {'count': len(scores), 'exact': int(outcomes.sum()),
            'accuracy': float(outcomes.mean()), 'mean_score': float(scores.mean()),
            'brier': float(np.mean((scores - outcomes) ** 2)),
            'binary_log_loss': float(-np.mean(outcomes * np.log(bounded)
                                             + (1 - outcomes) * np.log1p(-bounded))),
            'expected_calibration_error_10_bins': sum(
                b['count'] / len(scores) * abs(b['mean_score'] - b['observed_accuracy'])
                for b in bins if b['count']), 'bins': bins}


def paired_brier(scores, baseline, outcomes):
    scores, baseline = checked_scores(scores), checked_scores(baseline)
    outcomes = checked_outcomes(outcomes, len(scores))
    if baseline.shape != scores.shape:
        raise ValueError('Paired scores must have the same shape.')
    differences = (scores - outcomes) ** 2 - (baseline - outcomes) ** 2
    rng = np.random.default_rng(20261011)
    resampled = differences[rng.integers(0, len(scores), size=(20000, len(scores)))].mean(axis=1)
    return {'difference': float(differences.mean()),
            'paired_bootstrap_95': np.quantile(resampled, [.025, .975]).tolist(),
            'resamples': 20000, 'seed': 20261011,
            'interpretation': 'Negative favors adjustment. Validation selection makes this descriptive, not final proof.'}


def load_predictions(path, archive, split, detector):
    if split not in ('calibration', 'validation'):
        raise ValueError('Selection cannot read final-test predictions.')
    data = json.loads(Path(path).read_text())
    expected = {r['id']: r for r in annotations(archive) if r['split'] == split}
    rows = data.get('results', [])
    actual = {row['record']['id']: row for row in rows}
    identity = data.get('detector', data.get('reference_detector'))
    if (data.get('status') != 'finished' or data.get('split') != split
            or data.get('selected') != len(expected) or len(actual) != len(rows)
            or set(actual) != set(expected) or identity != detector):
        raise ValueError('Use the full completed partition with the unchanged reference detector.')
    values, outcomes = [], []
    for identity in sorted(actual):
        row = actual[identity]
        if row['record'] != expected[identity] or row.get('status') != 'estimated':
            raise ValueError('Predictions must match the pinned annotations and all succeed.')
        candidates = row['candidates']
        by_key = {c['key']: c['softmax'] for c in candidates}
        if len(candidates) != 24 or set(by_key) != set(KEYS):
            raise ValueError('Exactly 24 distinct key scores are required.')
        value = checked_probabilities([[by_key[k] for k in KEYS]])[0]
        winner = KEYS.index(row['key'])
        if value[winner] != value.max() or row['exact'] != (row['key'] == row['record']['key']):
            raise ValueError('Saved key/correctness disagrees with the scores or reference annotation.')
        if not 0 < row['duration'] <= 60 or row['frames'] != round(row['duration'] * 44100):
            raise ValueError('Unexpected audio excerpt or sample rate.')
        values.append(value)
        outcomes.append(row['exact'])
    return data, np.array(values), np.array(outcomes, dtype=bool), [actual[k] for k in sorted(actual)]


def select_candidate(measurements):
    eligible = [name for name in ('temperature', *METHODS) if all(
        measurements[name][metric] < measurements[base][metric]
        for metric in ('brier', 'binary_log_loss') for base in ('raw', 'constant'))]
    return min(eligible, key=lambda name: measurements[name]['brier']) if eligible else None


def save_new(path, value):
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development', type=Path, required=True)
    parser.add_argument('--validation', type=Path, required=True)
    parser.add_argument('--temperature', type=Path, required=True)
    parser.add_argument('--annotations', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    plan_path = args.out.with_suffix('.plan.json')
    if args.out.exists() or plan_path.exists():
        parser.error('Choose fresh output paths; previous evidence is preserved.')
    development, _, _ = load_development_report(args.development, args.annotations)
    detector = development['detector']
    _, dev_values, dev_correct, dev_rows = load_predictions(
        args.development, args.annotations, 'calibration', detector)
    temperature_fit = json.loads(args.temperature.read_text())
    if (temperature_fit.get('method') != 'temperature_scaling'
            or temperature_fit.get('status') != 'fitted_not_validated'
            or temperature_fit.get('dataset_revision') != REVISION
            or temperature_fit['input_sha256'] != sha256(args.development)
            or temperature_fit['annotation_sha256'] != sha256(args.annotations)
            or temperature_fit['script_sha256'] != sha256(Path(__file__).with_name('calibrate_key_confidence.py'))
            or temperature_fit['detector'] != detector or temperature_fit['keys'] != list(KEYS)
            or temperature_fit['probability_floor'] != FLOOR
            or sorted(temperature_fit['fit_records'], key=lambda r: r['id']) != [r['record'] for r in dev_rows]):
        raise ValueError('Temperature comparison must use the unchanged development-only fit.')
    temperature = temperature_fit['temperature']
    if not np.isfinite(temperature) or not .25 < temperature < 8:
        raise ValueError('Invalid temperature.')
    models = {method: fit_map(dev_values.max(axis=1), dev_correct, method) for method in METHODS}
    plan = {'status': 'fitted_before_validation_comparison', 'dataset_revision': REVISION,
            'scope': 'Correctness of first-minute source-key prediction on GiantSteps EDM previews.',
            'selection_rule': SELECTION_RULE, 'models': models, 'temperature': temperature,
            'constant': float(dev_correct.mean()), 'probability_floor': FLOOR,
            'fit_records': [r['record'] for r in dev_rows], 'reference_detector': detector,
            'inputs': {name: {'path': str(path.resolve()), 'sha256': sha256(path)} for name, path in (
                ('development', args.development), ('validation', args.validation),
                ('temperature', args.temperature), ('annotations', args.annotations))},
            'source_sha256': {name: sha256(Path(__file__).with_name(name)) for name in (
                Path(__file__).name, 'calibrate_key_confidence.py', 'benchmark_key_confidence.py')},
            'limitations': ['Validation selects among a small, fixed candidate family; final test is still required.',
                            'These are correctness scores, not 24-key distributions or overlap compatibility.',
                            'No evidence yet for generated songs, short passages, timing or vocal detection.',
                            'Unknown related mixes may cross splits; published model training overlap is not fully audited.']}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_new(plan_path, plan)  # Models and decision rule frozen before loading validation scores.
    _, val_values, val_correct, val_rows = load_predictions(
        args.validation, args.annotations, 'validation', detector)
    if {r['record']['md5'] for r in dev_rows} & {r['record']['md5'] for r in val_rows}:
        raise ValueError('Development and validation audio overlap.')
    measured, predictions = {}, {}
    for split, values, correct in (('development', dev_values, dev_correct), ('validation', val_values, val_correct)):
        raw = values.max(axis=1)
        scores = {'raw': raw, 'constant': np.full(len(raw), plan['constant']),
                  'temperature': scaled_probabilities(values, temperature).max(axis=1),
                  **{method: apply_map(raw, model) for method, model in models.items()}}
        predictions[split] = scores
        measured[split] = {name: binary_metrics(value, correct) for name, value in scores.items()}
    selected = select_candidate(measured['validation'])
    report = {'status': 'selected_pending_final_test' if selected else 'no_adjustment_selected',
              'selected_method': selected, 'plan': str(plan_path.resolve()), 'plan_sha256': sha256(plan_path),
              'measurements': measured, 'validation_rows': [
                  {'record': row['record'], 'key': row['key'], 'correct': bool(val_correct[i]),
                   'scores': {name: float(scores[i]) for name, scores in predictions['validation'].items()}}
                  for i, row in enumerate(val_rows)], 'limitations': plan['limitations']}
    if selected:
        report['selected_minus_raw_brier'] = paired_brier(
            predictions['validation'][selected], predictions['validation']['raw'], val_correct)
    save_new(args.out, report)
    print(json.dumps({'status': report['status'], 'selected_method': selected,
                      'validation': {k: {m: v[m] for m in ('brier', 'binary_log_loss', 'mean_score')}
                                     for k, v in measured['validation'].items()}, 'out': str(args.out)}))


if __name__ == '__main__':
    main()
