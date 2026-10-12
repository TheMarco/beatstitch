#!/usr/bin/env python3
"""Select broad BPM-correctness estimates from saved development/validation data.

No audio inference, network requests, final-test predictions, or app edits.
The target is Accuracy1 (within 4%), not exact beats, bar starts or drift repair.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

from benchmark_beat_tempo_incremental import validate_rows
from benchmark_tempo import annotations, sha256
from select_key_confidence import binary_metrics, paired_brier
from validate_tempo_detectors import read_planned_report

FEATURES = ('accepted_beat_coverage', 'beat_tempo_consistency', 'opening_agreement')
CANDIDATES = {'coverage': (0,), 'beats': (0, 1), 'agreement': (0, 1, 2)}
PENALTY = .01
SELECTION_RULE = ('Lowest validation Brier among candidates beating the development-accuracy '
                  'constant in both Brier and binary log loss; ties prefer fewer features. '
                  'Fit on development only. Otherwise select no candidate. Final testing is separate.')
LIMITATIONS = [
    'Correctness means global BPM within 4% of the reference; half/double mistakes count as wrong.',
    'This does not calibrate exact beat placement, downbeats, drift correction or blend quality.',
    'Scores apply only to the unchanged beat-based detector, including its timing refinement.',
    'Abstentions/failures remain reported; opening-only fallback and user BPM overrides are not calibrated.',
    'GiantSteps contains short EDM previews; full songs and AI-generated music are not validated here.',
    'Beat This training-recording overlap is unaudited; no unseen-training-data claim.',
    'Validation has already informed detector choice. Selection scores and intervals are descriptive.',
    'No confidence score is installed in the app by this experiment.',
]


def feature_row(beat, opening):
    """Only detector observations enter features; no reference label or correctness."""
    summary = beat['beat_tempo']
    bpm, initial, coverage = beat['bpm'], summary['bpm'], summary['coverage']
    low, high = summary['window_bpm_p10_p90']
    if (not all(type(x) in (int, float) and math.isfinite(x)
                for x in (bpm, initial, coverage, low, high))
            or not 20 <= bpm <= 300 or not 20 <= initial <= 300
            or not 0 <= coverage <= 1 or not 0 < low <= high):
        raise ValueError('Invalid beat-tempo features.')
    opening_bpm = opening.get('initial_bpm') if opening.get('status') == 'estimated' else None
    if opening_bpm is not None and (type(opening_bpm) not in (int, float)
                                   or not math.isfinite(opening_bpm) or opening_bpm <= 0):
        raise ValueError('Invalid opening estimate.')
    # One-octave disagreement maps to zero; same-tempo agreement maps to one.
    # Do not fold octaves: half/double interpretation is precisely a target error.
    consistency = 1 - min(1., math.log2(high / low))
    agreement = 0. if opening_bpm is None else 1 - min(1., abs(math.log2(bpm / opening_bpm)))
    return [coverage, consistency, agreement]


def load_partition(candidate_path, baseline_path, archive, split, expected_detector=None):
    if split not in ('calibration', 'validation'):
        raise ValueError('Confidence selection cannot read the final-test partition.')
    candidate, plan = read_planned_report(Path(candidate_path))
    records = [r for r in annotations(archive) if r['bpm'] and r['split'] == split]
    detector = plan.get('detector') if split == 'calibration' else plan.get('candidate_detector')
    if (candidate.get('status') != 'finished' or candidate.get('selected') != len(records)
            or plan.get('records') != records or len(candidate.get('results', [])) != len(records)
            or not detector or (expected_detector is not None and detector != expected_detector)):
        raise ValueError('Require the complete fixed partition and unchanged detector.')
    if split == 'calibration':
        baseline, baseline_plan = read_planned_report(Path(baseline_path))
        if (baseline.get('status') != 'finished' or baseline.get('split') != split
                or baseline_plan.get('records') != records
                or len(baseline.get('results', [])) != len(records)
                or plan.get('baseline_plan_sha256') != baseline['plan_sha256']
                or candidate.get('baseline_results') != baseline['results']):
            raise ValueError('Development opening estimates do not match their frozen plan.')
        openings, opening_detector = baseline['results'], baseline_plan['detector']
    else:
        if candidate.get('split') != split or plan.get('split') != split:
            raise ValueError('Validation partition identity differs.')
        openings, opening_detector = candidate.get('baseline_results', []), plan['baseline_detector']
        if len(openings) != len(records):
            raise ValueError('Every validation prediction needs its paired opening estimate.')
    validate_rows(candidate['results'], records, detector)
    validate_rows(openings, records, opening_detector)
    features, outcomes, ids = [], [], []
    for beat, opening in zip(candidate['results'], openings):
        if beat['status'] == 'estimated':
            if beat['initial_bpm'] != beat['beat_tempo']['bpm']:
                raise ValueError('Stored initial BPM differs from its beat observations.')
            features.append(feature_row(beat, opening))
            outcomes.append(int(beat['score']['accuracy1_4pct']))
            ids.append(beat['record']['id'])
    counts = {name: sum(r['status'] == name for r in candidate['results'])
              for name in ('estimated', 'abstained', 'failed')}
    counts['attempted'] = len(records)
    return np.asarray(features), np.asarray(outcomes), ids, counts, detector, opening_detector


def checked_matrix(values):
    values = np.asarray(values, dtype=float)
    if (values.ndim != 2 or values.shape[1] != len(FEATURES) or len(values) == 0
            or not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1)):
        raise ValueError('Expected nonempty finite feature rows in [0, 1].')
    return values


def matrix(values, name):
    if name not in CANDIDATES:
        raise ValueError('Unknown tempo-confidence candidate.')
    values = checked_matrix(values)
    return np.column_stack((values[:, CANDIDATES[name]], np.ones(len(values))))


def predict(values, model):
    design = matrix(values, model['name'])
    coefficients = np.asarray(model['coefficients'], dtype=float)
    if (coefficients.shape != (design.shape[1],) or not np.isfinite(coefficients).all()
            or np.any(coefficients[:-1] < 0)):
        raise ValueError('Invalid monotone confidence model.')
    return expit(design @ coefficients)


def fit(values, outcomes, name):
    design = matrix(values, name)
    outcomes = np.asarray(outcomes)
    if (outcomes.shape != (len(design),) or not np.isin(outcomes, [0, 1]).all()
            or not 0 < outcomes.mean() < 1):
        raise ValueError('Fit requires matching binary outcomes of both classes.')

    def objective(coefficients):
        logits = design @ coefficients
        loss = np.mean(np.logaddexp(0, logits) - outcomes * logits)
        loss += PENALTY * np.dot(coefficients[:-1], coefficients[:-1]) / 2
        gradient = design.T @ (expit(logits) - outcomes) / len(outcomes)
        gradient[:-1] += PENALTY * coefficients[:-1]
        return float(loss), gradient

    initial = np.zeros(design.shape[1]); initial[-1] = logit(outcomes.mean())
    result = minimize(objective, initial, jac=True, method='L-BFGS-B',
                      bounds=[(0, None)] * (len(initial) - 1) + [(None, None)],
                      options={'ftol': 1e-13, 'gtol': 1e-9, 'maxiter': 2000})
    gradient = objective(result.x)[1]
    gradient[:-1] = np.where(result.x[:-1] <= 1e-10, np.minimum(gradient[:-1], 0), gradient[:-1])
    if (not result.success or not np.isfinite(result.fun) or not np.isfinite(result.x).all()
            or np.max(np.abs(gradient)) > 1e-5):
        raise ValueError('Confidence fitting failed to converge.')
    return {'name': name, 'coefficients': result.x.tolist(), 'penalty': PENALTY,
            'features': [FEATURES[i] for i in CANDIDATES[name]], 'nonnegative_slopes': True}


def select(measured):
    baseline = measured['constant']
    eligible = [name for name in CANDIDATES if all(measured[name][metric] < baseline[metric]
                                                for metric in ('brier', 'binary_log_loss'))]
    return min(eligible, key=lambda name: (measured[name]['brier'], len(CANDIDATES[name]))) if eligible else None


def run(development, validation):
    x, y, _, _, _, _ = development
    vx, vy, ids, _, _, _ = validation
    models = {name: fit(x, y, name) for name in CANDIDATES}
    constant = float(y.mean())
    predictions = {'constant': np.full(len(vy), constant),
                   **{name: predict(vx, model) for name, model in models.items()}}
    measured = {name: binary_metrics(scores, vy) for name, scores in predictions.items()}
    selected = select(measured)
    return {'selected': selected, 'development_constant': constant, 'models': models,
            'validation_metrics': measured,
            'validation_brier_differences': {name: paired_brier(scores, predictions['constant'], vy)
                                             for name, scores in predictions.items() if name != 'constant'},
            'validation_predictions': [{'id': identity, 'correct_within_4pct': bool(vy[i]),
                                        'scores': {name: float(scores[i]) for name, scores in predictions.items()}}
                                       for i, identity in enumerate(ids)],
            'decision': ('Freeze selected candidate for separate final testing; not ready for app confidence.'
                         if selected else 'No candidate beats the constant on both metrics; do not ship a confidence score.')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--validation', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    plan_path = args.out.with_suffix('.plan.json')
    if args.out.exists() or plan_path.exists():
        parser.error('Use new report and plan paths; existing experiments are never overwritten.')
    development = load_partition(args.development, args.baseline, args.archive, 'calibration')
    validation = load_partition(args.validation, None, args.archive, 'validation', development[4])
    _, validation_plan = read_planned_report(args.validation)
    if (validation_plan.get('development_candidate_sha256') != sha256(args.development)
            or validation_plan.get('development_baseline_sha256') != sha256(args.baseline)):
        parser.error('Validation was not paired with these frozen development reports.')
    if development[5] != validation[5] or set(development[2]) & set(validation[2]):
        parser.error('Opening detector changed or recording partitions overlap.')
    plan = {'status': 'frozen_before_fitting', 'features': FEATURES, 'candidates': CANDIDATES,
            'penalty': PENALTY, 'selection_rule': SELECTION_RULE,
            'input_sha256': {str(p.resolve()): sha256(p) for p in
                             (args.development, args.baseline, args.validation, args.archive)},
            'source_sha256': {name: sha256(Path(__file__).with_name(name)) for name in
                              ('select_tempo_confidence.py', 'select_key_confidence.py',
                               'benchmark_tempo.py', 'benchmark_beat_tempo_incremental.py',
                               'validate_tempo_detectors.py')},
            'beat_detector': development[4], 'opening_detector': development[5],
            'target': 'Accuracy1 within 4%, conditional on a beat-based estimate',
            'development_counts': development[3], 'validation_counts': validation[3],
            'limitations': LIMITATIONS}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with plan_path.open('x') as handle:
        json.dump(plan, handle, indent=2, allow_nan=False)
    result = run(development, validation)
    report = {'status': 'finished', 'plan': str(plan_path.resolve()), 'plan_sha256': sha256(plan_path), **result}
    with args.out.open('x') as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
    print(json.dumps({key: report[key] for key in ('selected', 'decision', 'validation_brier_differences')}))


if __name__ == '__main__':
    main()
