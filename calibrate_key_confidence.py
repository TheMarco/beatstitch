#!/usr/bin/env python3
"""Fit source-key temperature scaling on the full development partition only.

The output is an unvalidated research artifact, never an app confidence claim.
See Guo et al., https://proceedings.mlr.press/v70/guo17a.html.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import logsumexp

import djmix
from benchmark_key_confidence import REVISION, annotations, wilson

KEYS = tuple(djmix.key_name((root, mode)) for mode in ('major', 'minor') for root in range(12))


def checked_probabilities(values):
    values = np.asarray(values, dtype=np.float64)
    if (values.ndim != 2 or values.shape[1] != 24 or not len(values)
            or not np.isfinite(values).all() or np.any(values < 0)
            or not np.allclose(values.sum(axis=1), 1., atol=1e-5, rtol=0)):
        raise ValueError('Expected finite, normalized distributions over all 24 keys.')
    # Float32 softmax may underflow. A fixed floor keeps fitting well defined.
    values = np.maximum(values, 1e-12)
    return values / values.sum(axis=1, keepdims=True)


def scaled_probabilities(values, temperature):
    values = checked_probabilities(values)
    if not np.isfinite(temperature) or temperature <= 0:
        raise ValueError('Temperature must be finite and positive.')
    logits = np.log(values) / temperature
    return np.exp(logits - logsumexp(logits, axis=1, keepdims=True))


def checked_targets(targets, count):
    targets = np.asarray(targets)
    if (targets.shape != (count,) or not np.issubdtype(targets.dtype, np.integer)
            or np.any(targets < 0) or np.any(targets >= 24)):
        raise ValueError('Expected one integer reference key per recording.')
    return targets


def metrics(values, targets):
    values = checked_probabilities(values)
    targets = checked_targets(targets, len(values))
    prediction = values.argmax(axis=1)
    correct = prediction == targets
    confidence = values.max(axis=1)
    one_hot = np.eye(24)[targets]
    bins = []
    for index in range(10):
        low, high = index / 10., (index + 1) / 10.
        mask = (confidence >= low) & ((confidence < high) if index < 9 else (confidence <= high))
        count, hits = int(mask.sum()), int(correct[mask].sum())
        bins.append({'minimum': low, 'maximum': high, 'count': count, 'exact': hits,
                     'mean_score': float(confidence[mask].mean()) if count else None,
                     'observed_accuracy': hits / count if count else None,
                     'wilson_95': wilson(hits, count)})
    return {'count': len(values), 'exact': int(correct.sum()), 'accuracy': float(correct.mean()),
            'negative_log_likelihood': float(-np.log(values[np.arange(len(values)), targets]).mean()),
            'multiclass_brier': float(np.sum((values - one_hot) ** 2, axis=1).mean()),
            'top_label_brier': float(np.mean((confidence - correct) ** 2)),
            'mean_top_score': float(confidence.mean()),
            'expected_calibration_error_10_bins': sum(
                b['count'] / len(values) * abs(b['mean_score'] - b['observed_accuracy'])
                for b in bins if b['count']), 'bins': bins}


def fit_temperature(values, targets):
    values = checked_probabilities(values)
    targets = checked_targets(targets, len(values))
    logs = np.log(values)

    def objective(log_temperature):
        adjusted = logs / np.exp(log_temperature)
        return float((logsumexp(adjusted, axis=1) - adjusted[np.arange(len(values)), targets]).mean())

    lower, upper = np.log(.25), np.log(8.)
    result = minimize_scalar(objective, bounds=(lower, upper), method='bounded',
                             options={'xatol': 1e-8})
    if not result.success or not np.isfinite(result.fun):
        raise ValueError('Temperature fit did not converge.')
    temperature = float(np.exp(result.x))
    if min(abs(result.x - lower), abs(result.x - upper)) < 1e-4:
        raise ValueError('Fit reached its prespecified bounds; review this model and data before proceeding.')
    return temperature


def load_development_report(path, archive):
    data = json.loads(Path(path).read_text())
    expected = {row['id']: row for row in annotations(archive) if row['split'] == 'calibration'}
    rows = data.get('results', [])
    if data.get('status') != 'finished' or data.get('split') != 'calibration':
        raise ValueError('Fitting requires a completed calibration-partition report, never validation/test data.')
    if len(rows) != len(expected) or data.get('selected') != len(expected):
        raise ValueError('Fit the full pinned calibration partition, not a selected pilot.')
    identities = [row['record']['id'] for row in rows]
    if len(set(identities)) != len(identities) or set(identities) != set(expected):
        raise ValueError('Recording IDs are duplicated or do not cover the full calibration partition.')
    values, targets = [], []
    for row in rows:
        record = row['record']
        if record != expected[record['id']] or row.get('status') != 'estimated':
            raise ValueError('Every reference must match the pinned annotations and have a successful estimate.')
        candidates = row['candidates']
        by_key = {entry['key']: entry['softmax'] for entry in candidates}
        if len(candidates) != 24 or set(by_key) != set(KEYS):
            raise ValueError('Each prediction must contain exactly the 24 distinct key scores.')
        values.append([by_key[key] for key in KEYS])
        targets.append(KEYS.index(record['key']))
    return data, checked_probabilities(values), np.array(targets, dtype=int)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--annotations', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Choose a new artifact path; existing results are preserved.')
    data, values, targets = load_development_report(args.report, args.annotations)
    temperature = fit_temperature(values, targets)
    scaled = scaled_probabilities(values, temperature)
    if not np.array_equal(values.argmax(axis=1), scaled.argmax(axis=1)):
        raise ValueError('Calibration unexpectedly changed key rankings.')
    artifact = {'version': 1, 'status': 'fitted_not_validated', 'method': 'temperature_scaling',
                'temperature': temperature, 'fit_bounds': [.25, 8.], 'probability_floor': 1e-12,
                'scope': 'First-minute source-key estimates on GiantSteps EDM previews; not overlap harmony.',
                'dataset_revision': REVISION,
                'annotation_sha256': hashlib.sha256(args.annotations.read_bytes()).hexdigest(),
                'input_report': str(args.report.resolve()),
                'input_sha256': hashlib.sha256(args.report.read_bytes()).hexdigest(),
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'detector': data['detector'], 'keys': list(KEYS),
                'fit_records': [row['record'] for row in data['results']],
                'development_before': metrics(values, targets),
                'development_after': metrics(scaled, targets),
                'limitations': [
                    'Fit-set improvement is not evidence of held-out calibration or accuracy.',
                    'Validate the frozen detector and transform on disjoint data before app use.',
                    'Scores do not establish timing, local-overlap harmony, vocals or blend quality.',
                    'Generated music and distribution shifts remain unvalidated.']}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as handle:
        json.dump(artifact, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'temperature': temperature, 'status': artifact['status'],
                      'recordings': len(values), 'artifact': str(args.out)}))


if __name__ == '__main__':
    main()
