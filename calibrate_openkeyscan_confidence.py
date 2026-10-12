#!/usr/bin/env python3
"""Exploratory correctness calibration for cached OpenKeyScan source-key results.

All partitions were already inspected for detector selection. Reusing them can
describe a candidate; it cannot establish fresh validation or UI confidence.
No audio inference, network access, detector changes or application writes.
"""
from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import platform

import numpy as np

from benchmark_key_confidence import REVISION, annotations
from calibrate_key_confidence import KEYS, checked_probabilities
from evaluate_openkeyscan import checked_rows
from select_key_confidence import (apply_map, binary_metrics, fit_map, paired_brier,
                                   save_new, sha256)
from studio_key_model import MODEL_SHA256, SOURCE_REVISION

ARCHIVE_SHA256 = '91697797f4b22ea1cb49ec00324397de8c3acde830357d866b7a9b8ba46d088b'
RULE = ('On both reused evaluation partitions, mapped Brier and binary log loss '
        'must be lower than raw scores and the frozen development-accuracy '
        'constant; both paired Brier 95% interval upper bounds must be below zero. '
        'Passing is exploratory evidence only, never authorization for UI promotion.')
SCOPE = ('Exact agreement of the first-minute source-key estimate with the '
         'GiantSteps key label. Not local overlap harmony, a good-sounding blend, '
         'the key throughout a full song, or accuracy on AI-generated music.')


def checked_partition(data, records, split, detector):
    if data.get('detector') != detector:
        raise ValueError('The OpenKeyScan detector identity changed between partitions.')
    rows = checked_rows(data, split, records)
    ordered = [rows[identity] for identity in sorted(rows)]
    scores, outcomes = [], []
    for row in ordered:
        if row['status'] != 'estimated':
            raise ValueError('A complete confidence measurement requires every prediction; do not discard failures.')
        candidates = row['candidates']
        by_key = {c['key']: c['softmax'] for c in candidates}
        if set(by_key) != set(KEYS):
            raise ValueError('Each prediction must use the 24 canonical major/minor keys.')
        values = checked_probabilities([[by_key[key] for key in KEYS]])[0]
        scores.append(float(values.max()))
        outcomes.append(bool(row['exact']))
    return np.asarray(scores), np.asarray(outcomes), ordered


def checked_evaluation(data, records, detector):
    expected = [r for r in records if r['split'] in ('validation', 'test')]
    rows = data.get('results', [])
    if (data.get('status') != 'finished' or data.get('selected') != len(expected)
            or len(rows) != len(expected)
            or {r['record']['id'] for r in rows} != {r['id'] for r in expected}):
        raise ValueError('Require the complete unchanged validation/test report, without extra or missing rows.')
    result = {}
    for split in ('validation', 'test'):
        selected = [r for r in rows if r['record']['split'] == split]
        part = {**data, 'split': split, 'selected': len(selected), 'results': selected}
        result[split] = checked_partition(part, records, split, detector)
    return result


def measure(scores, outcomes, model, constant):
    adjusted = apply_map(scores, model)
    baselines = {'raw': scores, 'constant': np.full(len(scores), constant)}
    metrics = {name: binary_metrics(values, outcomes) for name, values in
               {'mapped': adjusted, **baselines}.items()}
    differences = {name: paired_brier(adjusted, values, outcomes) for name, values in baselines.items()}
    for difference in differences.values():
        difference['interpretation'] = ('Negative favors the map. Resampling units are distinct audio files; '
                                        'prior use of these partitions makes this exploratory, not fresh validation.')
    passed = all(metrics['mapped'][metric] < metrics[name][metric]
                 for metric in ('brier', 'binary_log_loss') for name in baselines)
    passed = passed and all(d['paired_bootstrap_95'][1] < 0 for d in differences.values())
    return {'metrics': metrics, 'paired_brier': differences, 'exploratory_rule_passed': bool(passed)}


def run(development, evaluation, archive, output):
    development, evaluation, archive, output = map(Path, (development, evaluation, archive, output))
    plan_path = output.with_suffix('.plan.json')
    if output.exists() or plan_path.exists():
        raise ValueError('Choose fresh output paths; preserve existing evidence and frozen plans.')
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError('Use the pinned GiantSteps key annotation archive.')
    records = annotations(archive)
    if len({r['md5'] for r in records}) != len(records):
        raise ValueError('Repeated audio requires grouped calibration and resampling; do not treat it as independent.')
    dev = json.loads(development.read_text())
    detector = dev.get('detector', {})
    if (detector.get('library'), detector.get('source_revision'), detector.get('model_sha256')) != (
            'OpenKeyScan', SOURCE_REVISION, MODEL_SHA256):
        raise ValueError('Use cached predictions from the pinned OpenKeyScan model used by the app.')
    scores, outcomes, rows = checked_partition(dev, records, 'calibration', detector)
    model = fit_map(scores, outcomes, 'logit')
    constant = float(outcomes.mean())
    inputs = {name: {'path': str(path.resolve()), 'sha256': sha256(path)} for name, path in
              [('development', development), ('evaluation', evaluation), ('annotations', archive)]}
    source_files = ('calibrate_openkeyscan_confidence.py', 'select_key_confidence.py',
                    'calibrate_key_confidence.py', 'evaluate_openkeyscan.py',
                    'benchmark_key_confidence.py', 'studio_key_model.py', 'djmix.py')
    root = Path(__file__).resolve().parent
    sources = {name: sha256(root / name) for name in source_files}
    plan = {'version': 1, 'status': 'frozen_before_evaluation_scoring',
            'evaluation_independence': 'exploratory_reuse', 'ui_promotion_allowed': False,
            'scope': SCOPE, 'rule': RULE, 'dataset_revision': REVISION,
            'inputs': inputs, 'source_sha256': sources, 'detector': detector,
            'runtime': {'python': platform.python_version(),
                        'dependencies': {name: metadata.version(name) for name in ('numpy', 'scipy')}},
            'method': 'single_monotone_logit_map_no_candidate_selection', 'model': model,
            'constant': constant, 'fit_records': [r['record'] for r in rows],
            'limitations': ['Both evaluation partitions were inspected earlier to compare detectors.',
                            'Near duplicates and overlap with model training are unaudited.',
                            'Small or empty confidence bands do not establish reliable probabilities.',
                            'Fresh independently labeled evidence is needed before product confidence claims.']}
    output.parent.mkdir(parents=True, exist_ok=True)
    save_new(plan_path, plan)
    # Fit and protocol are persisted before reading evaluation outcomes.
    evaluated = checked_evaluation(json.loads(evaluation.read_text()), records, detector)
    measurements = {'calibration': measure(scores, outcomes, model, constant)}
    for split, (values, targets, _) in evaluated.items():
        measurements[split] = measure(values, targets, model, constant)
    if any(sha256(item['path']) != item['sha256'] for item in inputs.values()):
        raise ValueError('An input changed during calibration; preserve the plan and retry with fresh paths.')
    if any(sha256(root / name) != checksum for name, checksum in sources.items()):
        raise ValueError('Calibration source changed during this run.')
    report = {'version': 1, 'status': 'exploratory_complete', 'ui_promotion_allowed': False,
              'evaluation_independence': 'exploratory_reuse', 'scope': SCOPE,
              'plan': str(plan_path.resolve()), 'plan_sha256': sha256(plan_path),
              'measurements': measurements,
              'both_partitions_passed': all(measurements[s]['exploratory_rule_passed']
                                            for s in ('validation', 'test')),
              'fit_changed_key_predictions': False, 'model': model, 'constant': constant,
              'inputs': inputs, 'limitations': plan['limitations']}
    save_new(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development', type=Path, required=True)
    parser.add_argument('--evaluation', type=Path, required=True)
    parser.add_argument('--annotations', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.development, args.evaluation, args.annotations, args.out)
    print(json.dumps({'status': result['status'], 'both_partitions_passed': result['both_partitions_passed'],
                      'ui_promotion_allowed': False, 'report': str(args.out)}, indent=2))


if __name__ == '__main__':
    main()
