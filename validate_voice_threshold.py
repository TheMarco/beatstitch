#!/usr/bin/env python3
"""Evaluate one frozen voice-prompt cutoff, first on validation then reserved test.

This changes no application settings and fits no parameters to reserved labels.
Intervals are resampled by song, not by overlapping ten-second window.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import time
import zipfile

import numpy as np

from benchmark_content import (ARCHIVE_BYTES, ARCHIVE_MD5, catalog, detector_identity,
                               file_hash, intersection, metrics, parse_labels,
                               save_report, validate_resume)

BASELINE, CANDIDATE = .2, .08
CRITERIA = {'minimum_precision': .95, 'minimum_recall': .80,
            'maximum_false_prompt_rate': .05, 'minimum_recall_gain': .25,
            'recall_gain_interval_lower_above': 0., 'bootstrap_samples': 4000,
            'bootstrap_seed': 2918}
LIMITATIONS = [
    'Measures any annotated voice in a window, not competing vocals or musical quality.',
    'Ten-second listening ranges are coarse and may include instrumental gaps.',
    'Jamendo is not a representative AI-generated EDM evaluation.',
    'Possible overlap with AudioSet training recordings has not been audited.',
    'Only 16 songs per reserved partition; bootstrap intervals are descriptive, not guarantees.',
    'No calibrated probabilities or numeric confidence are installed by this experiment.',
]


def measure(analysis, labels, threshold):
    from studio_content import valid_result
    if not 0 < threshold < 1 or not valid_result(analysis, analysis['duration']):
        raise ValueError('Invalid observations or voice threshold.')
    duration = analysis['duration']
    if labels[-1][1] > duration + .25:
        raise ValueError('Annotations extend beyond the recording.')
    labels = [(a, min(b, duration), voice) for a, b, voice in labels if a < duration]
    regions, cells = [], dict.fromkeys(('tp', 'fp', 'fn', 'tn'), 0)
    excluded = 0
    for window in analysis['windows']:
        a, b = window['start'], window['end']
        prompted = window['rms_db'] >= -65 and window['scores']['voice'] >= threshold
        if prompted:
            if regions and a <= regions[-1][1]:
                regions[-1] = (regions[-1][0], max(b, regions[-1][1]))
            else:
                regions.append((a, b))
        known = intersection(a, b, [(x, y) for x, y, _ in labels])
        present = intersection(a, b, [(x, y) for x, y, v in labels if v]) > 0
        if abs(known - (b - a)) >= .001:
            excluded += 1
            continue
        cells['tp' if present and prompted else 'fn' if present else 'fp' if prompted else 'tn'] += 1
    seconds = dict.fromkeys(cells, 0.)
    for a, b, voice in labels:
        prompted = min(b - a, intersection(a, b, regions))
        seconds['tp' if voice else 'fp'] += prompted
        seconds['fn' if voice else 'tn'] += b - a - prompted
    return {'windows': metrics(cells), 'seconds': metrics(seconds),
            'excluded_windows': excluded, 'unannotated_seconds': max(0., duration - sum(seconds.values()))}


def summarize(rows, selected):
    successful = [r for r in rows if r['status'] == 'measured']
    result = {'selected': selected, 'measured': len(successful), 'failed': len(rows) - len(successful),
              'pending': selected - len(rows)}
    for name in ('baseline', 'candidate'):
        result[name] = {unit: metrics({key: sum(r[name][unit][key] for r in successful)
                                     for key in ('tp', 'fp', 'fn', 'tn')}) for unit in ('windows', 'seconds')}
        recalls = [r[name]['windows']['recall'] for r in successful if r[name]['windows']['recall'] is not None]
        result[name]['mean_song_recall'] = float(np.mean(recalls)) if recalls else None
        result[name]['songs_with_false_prompts'] = sum(r[name]['windows']['fp'] > 0 for r in successful)
    gain = None
    interval = None
    before, after = result['baseline']['windows'], result['candidate']['windows']
    if before['recall'] is not None and after['recall'] is not None:
        gain = after['recall'] - before['recall']
        arrays = {name: np.asarray([[r[name]['windows']['tp'], r[name]['windows']['fn']]
                                   for r in successful], dtype=float) for name in ('baseline', 'candidate')}
        draw = np.random.default_rng(CRITERIA['bootstrap_seed']).integers(
            0, len(successful), size=(CRITERIA['bootstrap_samples'], len(successful)))
        sampled = {name: value[draw].sum(axis=1) for name, value in arrays.items()}
        valid = np.logical_and(*(value.sum(axis=1) > 0 for value in sampled.values()))
        delta = (sampled['candidate'][valid, 0] / sampled['candidate'][valid].sum(axis=1)
                 - sampled['baseline'][valid, 0] / sampled['baseline'][valid].sum(axis=1))
        interval = {'unit': 'song', 'percentile_95': np.quantile(delta, [.025, .975]).tolist(),
                    'valid_resamples': len(delta)} if len(delta) else None
    result.update(recall_gain=gain, paired_recall_gain_interval=interval)
    result['acceptance_passed'] = bool(
        len(successful) == selected and not result['failed']
        and after['precision'] is not None and after['precision'] >= CRITERIA['minimum_precision']
        and after['recall'] is not None and after['recall'] >= CRITERIA['minimum_recall']
        and after['false_prompt_rate'] is not None and after['false_prompt_rate'] <= CRITERIA['maximum_false_prompt_rate']
        and gain is not None and gain >= CRITERIA['minimum_recall_gain']
        and interval and interval['percentile_95'][0] > CRITERIA['recall_gain_interval_lower_above'])
    return result


def reserved_catalog(archive, phase):
    if phase not in ('validation', 'test'):
        raise ValueError('Unknown reserved phase.')
    catalog(archive)  # Verify all official partitions are disjoint and correctly sized.
    split = 'valid' if phase == 'validation' else 'test'
    records = []
    for song in archive.read('jamendo/filelists/' + split).decode().splitlines():
        audio = 'jamendo/audio/' + song
        labels = 'jamendo/labels/' + str(PurePosixPath(song).with_suffix('.lab'))
        info = archive.getinfo(audio)
        if not 0 < info.file_size <= 64 * 1024 ** 2:
            raise ValueError('Invalid audio member size.')
        raw = archive.read(labels)
        parse_labels(raw.decode())
        import hashlib
        records.append({'name': song, 'audio': audio, 'labels': labels, 'audio_bytes': info.file_size,
                        'labels_sha256': hashlib.sha256(raw).hexdigest()})
    return records


def load_report(path):
    report = json.loads(path.read_text())
    plan_path = path.with_suffix('.plan.json')
    if report['plan_sha256'] != file_hash(plan_path):
        raise ValueError('Report plan changed.')
    return report, json.loads(plan_path.read_text())


def validate_rows(report, plan):
    rows = report['results']
    if len(rows) > len(plan['records']) or [r['record'] for r in rows] != plan['records'][:len(rows)]:
        raise ValueError('Saved selection changed.')
    import hashlib
    for row in rows:
        if row['status'] not in ('measured', 'failed'):
            raise ValueError('Invalid result state.')
        if row['status'] == 'measured':
            if hashlib.sha256(row['annotation_text'].encode()).hexdigest() != row['record']['labels_sha256']:
                raise ValueError('Saved annotation changed.')
            labels = parse_labels(row['annotation_text'])
            for name, threshold in [('baseline', BASELINE), ('candidate', CANDIDATE)]:
                if row[name] != measure(row['analysis'], labels, threshold):
                    raise ValueError('Saved score changed.')


def approve_test(report, plan, development_sha, detector):
    if (report.get('status') != 'finished' or plan.get('phase') != 'validation'
            or plan.get('thresholds') != {'baseline': BASELINE, 'candidate': CANDIDATE}
            or plan.get('criteria') != CRITERIA or plan.get('development_sha256') != development_sha
            or plan.get('detector') != detector or len(plan.get('records', [])) != 16):
        raise ValueError('Final testing requires the unchanged, completed validation experiment.')
    validate_rows(report, plan)
    if not summarize(report['results'], 16)['acceptance_passed']:
        raise ValueError('Validation did not meet the frozen acceptance criteria; keep test reserved.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--development', type=Path, required=True)
    parser.add_argument('--phase', choices=('validation', 'test'), required=True)
    parser.add_argument('--validation', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.archive.stat().st_size != ARCHIVE_BYTES or file_hash(args.archive, 'md5') != ARCHIVE_MD5:
        parser.error('Archive differs from the published release.')
    development, development_plan = load_report(args.development)
    validate_resume(development, development_plan, development['plan_sha256'])
    detector = detector_identity()
    if (development['status'] != 'finished' or development_plan['split'] != 'train'
            or development_plan['detector'] != detector or len(development['results']) != 61
            or any(r['status'] != 'measured' for r in development['results'])):
        parser.error('Require the complete development baseline with the unchanged detector.')
    development_sha = file_hash(args.development)
    if args.phase == 'test':
        if args.validation is None:
            parser.error('Final test requires --validation.')
        validation, validation_plan = load_report(args.validation)
        approve_test(validation, validation_plan, development_sha, detector)
    elif args.validation is not None:
        parser.error('Do not supply a validation result while measuring validation.')
    with zipfile.ZipFile(args.archive) as archive:
        records = reserved_catalog(archive, args.phase)
        plan = {'version': 1, 'phase': args.phase, 'records': records,
                'archive_sha256': file_hash(args.archive), 'development_sha256': development_sha,
                'validation_sha256': file_hash(args.validation) if args.validation else None,
                'thresholds': {'baseline': BASELINE, 'candidate': CANDIDATE}, 'criteria': CRITERIA,
                'detector': detector, 'runner_sha256': file_hash(__file__),
                'helper_sha256': file_hash(Path(__file__).with_name('benchmark_content.py')),
                'selection_reason': 'Development-only choice: .08 catches 91% of vocal windows '
                                    'with 1.5% false prompts and equals the existing drum-led voice veto.',
                'limitations': LIMITATIONS}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        plan_path = args.out.with_suffix('.plan.json')
        if plan_path.exists():
            if json.loads(plan_path.read_text()) != plan:
                parser.error('Experiment changed; existing plan cannot be overwritten.')
        else:
            if args.out.exists():
                parser.error('Existing report has no plan.')
            with plan_path.open('x') as handle:
                json.dump(plan, handle, indent=2, allow_nan=False)
        report = (json.loads(args.out.read_text()) if args.out.exists() else
                  {'plan_sha256': file_hash(plan_path), 'limitations': LIMITATIONS, 'results': []})
        if report['plan_sha256'] != file_hash(plan_path):
            parser.error('Report is bound to another plan.')
        validate_rows(report, plan)
        report.update(status='running', summary=summarize(report['results'], len(records)))
        save_report(args.out, report)
        import djmix
        from studio_content import analyze_content
        for record in records[len(report['results']):]:
            row = {'record': record}; started = time.monotonic()
            try:
                with tempfile.TemporaryDirectory(prefix='.voice-validation-', dir=args.out.parent) as temporary:
                    work = Path(temporary); audio = work / record['name']
                    with archive.open(record['audio']) as source, audio.open('wb') as target:
                        shutil.copyfileobj(source, target)
                    analysis = analyze_content(audio, djmix.probe(audio)['duration'], work / 'cache')
                    analysis.pop('source', None)
                    annotations = archive.read(record['labels']).decode()
                    labels = parse_labels(annotations)
                    row.update(status='measured', audio_sha256=file_hash(audio), analysis=analysis,
                               annotation_text=annotations, baseline=measure(analysis, labels, BASELINE),
                               candidate=measure(analysis, labels, CANDIDATE))
            except Exception as error:
                row.update(status='failed', error=f'{type(error).__name__}: {error}')
            row['seconds'] = round(time.monotonic() - started, 3)
            report['results'].append(row)
            report['summary'] = summarize(report['results'], len(records))
            save_report(args.out, report)
            print(f'{len(report["results"])}/{len(records)} {record["name"]}: {row["status"]} '
                  f'{row.get("error", "")}', flush=True)
        report['status'] = 'finished_with_failures' if report['summary']['failed'] else 'finished'
        save_report(args.out, report)
        print(json.dumps({'status': report['status'], **report['summary']}))
        return int(report['summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
