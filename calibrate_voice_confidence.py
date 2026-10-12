#!/usr/bin/env python3
"""Explore window-level voice confidence from saved, human-labelled observations.

No inference, downloads, threshold changes or app promotion. Fit one monotone
logit map on the official training songs, freeze it before reading held-out
scores, and report both previously inspected partitions without retuning.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import zipfile

import numpy as np

from benchmark_content import (ARCHIVE_BYTES, ARCHIVE_MD5, catalog, file_hash,
                               intersection, parse_labels, save_report, validate_resume)
from repair_voice_reference import corrected_tail
from select_key_confidence import apply_map, fit_map
from studio_content import GROUPS, MODEL_SHA256, RATE, STEP, VERSION, WINDOW, valid_result
from validate_voice_threshold import load_report, reserved_catalog, validate_rows

BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED = 4000, 2919
PROTOCOL = {
    'outcome': 'Any annotated singing inside a fully annotated source window.',
    'features': 'Only the saved maximum voice-class score; silent windows retain score zero.',
    'fit': 'One monotone logit map, fitted by window-weighted binary log loss on train only.',
    'selection': 'No candidate selection, threshold search or refitting on validation/test.',
    'evaluation': 'Window-weighted metrics; resample whole songs together with all their overlapping windows.',
    'comparison_rule': 'On each partition, beat raw scores and the frozen training prevalence in both Brier and log loss, with paired song-bootstrap Brier upper bounds below zero.',
    'bootstrap_samples': BOOTSTRAP_SAMPLES, 'bootstrap_seed': BOOTSTRAP_SEED,
}
LIMITATIONS = [
    'Validation/test recordings were previously inspected in the voice-threshold experiment; this is exploratory reuse, not a new unseen test.',
    'The target is any annotated singing in a ten-second window, not competing vocals, speech coverage, exact boundaries or mix quality.',
    'A probability for one source window is not a probability that two songs will clash.',
    'Jamendo is not representative of AI-generated EDM; AudioSet training overlap and related artists have not been audited.',
    'Only 16 songs in each held-out partition. Song-bootstrap intervals remain uncertain and do not include model-fitting uncertainty.',
    'Sparse or pure reliability bins can have narrow empirical intervals without establishing precise future reliability.',
    'The documented correction only truncates one missing final non-vocal annotation tail. Original evidence is preserved.',
    'No numeric confidence, model, prompt cutoff or other setting is installed in the application.',
]


def window_examples(analysis, annotation_text):
    """Labels never enter features; annotation gaps remain unknown, not negative."""
    if not valid_result(analysis, analysis['duration']):
        raise ValueError('Invalid saved passage observations.')
    labels = parse_labels(annotation_text)
    duration = analysis['duration']
    if labels[-1][1] > duration + .25:
        raise ValueError('Annotations extend beyond the recording.')
    labels = [(a, min(b, duration), voice) for a, b, voice in labels if a < duration]
    scores, outcomes, excluded = [], [], 0
    known = [(a, b) for a, b, _ in labels]
    singing = [(a, b) for a, b, voice in labels if voice]
    for window in analysis['windows']:
        a, b = window['start'], window['end']
        if abs(intersection(a, b, known) - (b - a)) >= .001:
            excluded += 1
            continue
        scores.append(window['scores']['voice'] if window['rms_db'] >= -65 else 0.)
        outcomes.append(int(intersection(a, b, singing) > 0))
    if not scores:
        raise ValueError('Every song must have at least one fully annotated window.')
    return {'scores': scores, 'outcomes': outcomes, 'excluded_windows': excluded}


def load_partition(path, archive, phase, archive_sha256, detector=None, previous=()):
    """Validate full official selections, saved measurements, labels and audio hashes."""
    if phase not in ('train', 'validation', 'test'):
        raise ValueError('Unknown partition.')
    report, plan = load_report(Path(path))
    expected = catalog(archive) if phase == 'train' else reserved_catalog(archive, phase)
    if (report.get('status') != 'finished' or plan.get('archive_sha256') != archive_sha256
            or plan.get('split' if phase == 'train' else 'phase') != phase
            or len(report.get('results', [])) != len(expected)
            or any(r.get('status') != 'measured' for r in report['results'])):
        raise ValueError('Require the complete, successful official partition and unchanged archive.')
    identity = plan.get('detector', {})
    expected_detector = {'version': VERSION, 'model_sha256': MODEL_SHA256, 'rate': RATE,
                         'window': WINDOW, 'step': STEP, 'groups': {k: list(v) for k, v in GROUPS.items()}}
    if any(identity.get(k) != v for k, v in expected_detector.items()) or (detector is not None and identity != detector):
        raise ValueError('All partitions must use the same saved detector and preprocessing.')
    correction = plan.get('reference_correction')
    if correction:
        if phase != 'test' or report.get('reference_correction') != correction:
            raise ValueError('Unexpected reference correction.')
        matches = [i for i, r in enumerate(expected) if r['name'] == correction.get('source')]
        if len(matches) != 1:
            raise ValueError('Correction must identify exactly one official test recording.')
        index = matches[0]; row = report['results'][index]
        raw = archive.read(expected[index]['labels']).decode()
        revised, detail = corrected_tail(raw, row['analysis']['duration'])
        if (row.get('original_annotation_text') != raw or row.get('annotation_text') != revised
                or any(correction.get(k) != v for k, v in detail.items())
                or correction.get('original_labels_sha256') != expected[index]['labels_sha256']
                or correction.get('revised_labels_sha256') != hashlib.sha256(revised.encode()).hexdigest()
                or correction.get('audio_sha256') != row['audio_sha256']):
            raise ValueError('The reference amendment must preserve every earlier and vocal annotation.')
        expected[index] = {**expected[index], 'labels_sha256': correction['revised_labels_sha256']}
    if plan.get('records') != expected or [r['record'] for r in report['results']] != expected:
        raise ValueError('Missing, duplicate, reordered or altered official records.')
    if phase == 'train': validate_resume(report, plan, report['plan_sha256'])
    else: validate_rows(report, plan)
    names, hashes = {s['name'] for s in previous}, {s['audio_sha256'] for s in previous}
    songs = []
    for row in report['results']:
        record = row['record']
        with archive.open(record['audio']) as audio:
            audio_hash = hashlib.file_digest(audio, 'sha256').hexdigest()
        if (audio_hash != row['audio_sha256'] or audio_hash in hashes or record['name'] in names):
            raise ValueError('Changed audio or overlapping recordings across partitions.')
        names.add(record['name']); hashes.add(audio_hash)
        songs.append({'name': record['name'], 'audio_sha256': audio_hash,
                      **window_examples(row['analysis'], row['annotation_text'])})
    return songs, identity


def frozen_model(songs):
    scores = np.asarray([v for s in songs for v in s['scores']], dtype=float)
    outcomes = np.asarray([v for s in songs for v in s['outcomes']], dtype=float)
    return {'model': fit_map(scores, outcomes, 'logit'),
            'training_prevalence': float(outcomes.mean()),
            'training_songs': len(songs), 'training_windows': len(scores)}


def evaluate(songs, frozen):
    scores = np.asarray([v for s in songs for v in s['scores']], dtype=float)
    outcomes = np.asarray([v for s in songs for v in s['outcomes']], dtype=float)
    groups = np.repeat(np.arange(len(songs)), [len(s['scores']) for s in songs])
    counts = np.bincount(groups, minlength=len(songs))
    draw = np.random.default_rng(BOOTSTRAP_SEED).integers(0, len(songs), size=(BOOTSTRAP_SAMPLES, len(songs)))
    denominator = counts[draw].sum(axis=1)
    predictions = {'raw': scores, 'mapped': apply_map(scores, frozen['model']),
                   'training_constant': np.full(len(scores), frozen['training_prevalence'])}
    result = {'songs': len(songs), 'windows': len(scores), 'positive_windows': int(outcomes.sum()),
              'excluded_windows': sum(s['excluded_windows'] for s in songs),
              'observed_prevalence': float(outcomes.mean())}
    losses = {}
    for name, predicted in predictions.items():
        bounded = np.clip(predicted, 1e-12, 1 - 1e-12)
        loss = (predicted - outcomes) ** 2; losses[name] = loss
        bins = []
        for i in range(10):
            low, high = i / 10, (i + 1) / 10
            mask = (predicted >= low) & ((predicted < high) if i < 9 else predicted <= high)
            by_song_count = np.bincount(groups[mask], minlength=len(songs))
            by_song_positive = np.bincount(groups[mask], weights=outcomes[mask], minlength=len(songs))
            sizes = by_song_count[draw].sum(axis=1); valid = sizes > 0
            sampled = by_song_positive[draw].sum(axis=1)[valid] / sizes[valid]
            count = int(mask.sum())
            bins.append({'minimum': low, 'maximum': high, 'windows': count,
                         'contributing_songs': int(np.count_nonzero(by_song_count)),
                         'positive_windows': int(outcomes[mask].sum()),
                         'mean_score': float(predicted[mask].mean()) if count else None,
                         'observed_prevalence': float(outcomes[mask].mean()) if count else None,
                         'song_bootstrap_95': np.quantile(sampled, [.025, .975]).tolist() if len(sampled) else None,
                         'valid_resamples': int(valid.sum())})
        song_loss = np.bincount(groups, weights=loss, minlength=len(songs)) / counts
        result[name] = {'brier': float(loss.mean()),
                        'binary_log_loss': float(-np.mean(outcomes * np.log(bounded) + (1 - outcomes) * np.log1p(-bounded))),
                        'mean_song_brier': float(song_loss.mean()), 'mean_score': float(predicted.mean()),
                        'ece_10_bins_descriptive': sum(b['windows'] / len(scores) * abs(b['mean_score'] - b['observed_prevalence']) for b in bins if b['windows']),
                        'bins': bins}
    result['paired_brier'] = {}
    for name in ('raw', 'training_constant'):
        diff = losses['mapped'] - losses[name]
        totals = np.bincount(groups, weights=diff, minlength=len(songs))
        sampled = totals[draw].sum(axis=1) / denominator
        result['paired_brier'][name] = {'mapped_minus_baseline': float(diff.mean()),
            'song_bootstrap_95': np.quantile(sampled, [.025, .975]).tolist()}
    result['comparison_rule_passed'] = all(
        result['mapped']['brier'] < result[name]['brier']
        and result['mapped']['binary_log_loss'] < result[name]['binary_log_loss']
        and result['paired_brier'][name]['song_bootstrap_95'][1] < 0
        for name in ('raw', 'training_constant'))
    return result


def run(archive_path, development, validation, test, out):
    archive_path, out = Path(archive_path), Path(out)
    plan_path = out.with_suffix('.plan.json')
    if out.exists() or plan_path.exists():
        raise ValueError('Use fresh output paths; do not overwrite earlier evidence.')
    if archive_path.stat().st_size != ARCHIVE_BYTES or file_hash(archive_path, 'md5') != ARCHIVE_MD5:
        raise ValueError('Archive differs from the published release.')
    archive_sha = file_hash(archive_path)
    inputs = {name: {'path': str(Path(path).resolve()), 'sha256': file_hash(path),
                     'plan_sha256': file_hash(Path(path).with_suffix('.plan.json'))}
              for name, path in [('train', development), ('validation', validation), ('test', test)]}
    source_files = [Path(__file__).name, 'benchmark_content.py', 'validate_voice_threshold.py',
                    'repair_voice_reference.py', 'select_key_confidence.py', 'studio_content.py',
                    'calibrate_key_confidence.py', 'benchmark_key_confidence.py']
    sources = {name: file_hash(Path(__file__).parent / name) for name in source_files}
    with zipfile.ZipFile(archive_path) as archive:
        train, detector = load_partition(development, archive, 'train', archive_sha)
        frozen = frozen_model(train)
        plan = {'version': 1, 'protocol': PROTOCOL, 'limitations': LIMITATIONS, 'inputs': inputs,
                'archive_sha256': archive_sha, 'detector': detector, 'sources': sources, **frozen,
                'runtime': {'python': platform.python_version(), **{name: metadata.version(name) for name in ('numpy', 'scipy')}},
                'training_recordings': [{'name': s['name'], 'audio_sha256': s['audio_sha256']} for s in train],
                'ui_promotion_allowed': False}
        if file_hash(development) != inputs['train']['sha256'] or file_hash(Path(development).with_suffix('.plan.json')) != inputs['train']['plan_sha256']:
            raise ValueError('Training evidence changed during fitting.')
        out.parent.mkdir(parents=True, exist_ok=True)
        # Neither held-out score report is loaded until the fitted map is saved.
        with plan_path.open('x') as handle: json.dump(plan, handle, indent=2, allow_nan=False)
        report = {'status': 'running', 'plan_sha256': file_hash(plan_path), 'limitations': LIMITATIONS,
                  'ui_promotion_allowed': False, 'partitions': {'train': evaluate(train, frozen)}}
        save_report(out, report)
        try:
            previous = list(train)
            for phase, path in [('validation', validation), ('test', test)]:
                if file_hash(path) != inputs[phase]['sha256'] or file_hash(Path(path).with_suffix('.plan.json')) != inputs[phase]['plan_sha256']:
                    raise ValueError('Held-out evidence changed after freezing the plan.')
                songs, _ = load_partition(path, archive, phase, archive_sha, detector, previous)
                report['partitions'][phase] = evaluate(songs, frozen)
                previous.extend(songs); save_report(out, report)
            if any(file_hash(Path(__file__).parent / name) != value for name, value in sources.items()):
                raise ValueError('Calibration source changed during evaluation.')
            if any(file_hash(item['path']) != item['sha256'] or file_hash(Path(item['path']).with_suffix('.plan.json')) != item['plan_sha256'] for item in inputs.values()):
                raise ValueError('Input evidence changed during evaluation.')
            report.update(status='exploratory_complete', both_partitions_passed=all(
                report['partitions'][p]['comparison_rule_passed'] for p in ('validation', 'test')))
            save_report(out, report)
        except Exception as error:
            report.update(status='failed', error=f'{type(error).__name__}: {error}')
            save_report(out, report)
            raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    for name in ('development', 'validation', 'test', 'out'): parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    report = run(args.archive, args.development, args.validation, args.test, args.out)
    print(json.dumps({'status': report['status'], 'both_partitions_passed': report['both_partitions_passed'],
                      'out': str(args.out), 'ui_promotion_allowed': False}))


if __name__ == '__main__': main()
