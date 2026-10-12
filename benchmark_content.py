#!/usr/bin/env python3
"""Measure existing voice prompts on Jamendo's training partition, without tuning.

The reserved validation/test songs are never inferred. Results concern human
voice-presence labels, not competing vocals, melody clashes or mix quality.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import time
import zipfile

ARCHIVE_BYTES = 327156782
ARCHIVE_MD5 = '4d6b907b224c6df3a5ed3ffc3e98b8b1'
SOURCE = 'https://zenodo.org/records/2585988'


def file_hash(path, algorithm='sha256'):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, algorithm).hexdigest()


def parse_labels(text):
    """Keep annotation gaps unknown; reject overlapping or invalid intervals."""
    intervals = []
    previous = 0.
    for line in text.splitlines():
        if not line.strip():
            continue
        start, end, label = line.split()
        a, b = float(start), float(end)
        if (not all(math.isfinite(v) for v in (a, b)) or not previous <= a < b
                or label not in ('sing', 'nosing')):
            raise ValueError('Invalid or overlapping voice annotation.')
        intervals.append((a, b, label == 'sing'))
        previous = b
    if not intervals:
        raise ValueError('No voice annotations.')
    return intervals


def catalog(archive):
    names = archive.namelist()
    if len(set(names)) != len(names):
        raise ValueError('Duplicate archive members.')
    splits = {}
    for split, count in [('train', 61), ('valid', 16), ('test', 16)]:
        songs = archive.read('jamendo/filelists/' + split).decode('utf-8').splitlines()
        if len(songs) != count or len(set(songs)) != count:
            raise ValueError('Unexpected official partition.')
        for song in songs:
            if (PurePosixPath(song).name != song or '\\' in song
                    or Path(song).suffix not in ('.ogg', '.mp3')):
                raise ValueError('Unsafe or unknown audio member.')
        splits[split] = songs
    if len(set(sum(splits.values(), []))) != 93:
        raise ValueError('The official partitions overlap.')
    records = []
    for song in splits['train']:
        audio = 'jamendo/audio/' + song
        labels = 'jamendo/labels/' + str(PurePosixPath(song).with_suffix('.lab'))
        info = archive.getinfo(audio)
        if not 0 < info.file_size <= 64 * 1024 ** 2:
            raise ValueError('Oversized or empty audio member.')
        raw = archive.read(labels)
        parse_labels(raw.decode('utf-8'))
        records.append({'name': song, 'audio': audio, 'labels': labels,
                        'audio_bytes': info.file_size,
                        'labels_sha256': hashlib.sha256(raw).hexdigest()})
    return records


def intersection(a, b, intervals):
    return sum(max(0., min(b, end) - max(a, start)) for start, end in intervals)


def metrics(cells):
    tp, fp, fn, tn = (cells[k] for k in ('tp', 'fp', 'fn', 'tn'))
    ratio = lambda a, b: a / b if b else None
    return {**cells, 'precision': ratio(tp, tp + fp), 'recall': ratio(tp, tp + fn),
            'f1': ratio(2 * tp, 2 * tp + fp + fn),
            'false_prompt_rate': ratio(fp, fp + tn), 'miss_rate': ratio(fn, tp + fn)}


def score(analysis, labels):
    from studio_content import valid_result, voice_regions
    duration = analysis['duration']
    if not valid_result(analysis, duration):
        raise ValueError('Invalid app observations.')
    if labels[-1][1] > duration + .25:
        raise ValueError('Annotations extend beyond the decoded recording.')
    labels = [(a, min(b, duration), voice) for a, b, voice in labels if a < duration]
    # Preserve the original baseline even after the app's prompt policy changes.
    regions = [(r['start'], r['end']) for r in voice_regions(analysis, threshold=.2)]
    cells = dict.fromkeys(('tp', 'fp', 'fn', 'tn'), 0.)
    for a, b, voice in labels:
        prompted = min(b - a, intersection(a, b, regions))
        cells['tp' if voice else 'fp'] += prompted
        cells['fn' if voice else 'tn'] += b - a - prompted
    windows = []
    window_cells = dict.fromkeys(cells, 0)
    skipped = 0
    for window in analysis['windows']:
        a, b = window['start'], window['end']
        known = intersection(a, b, [(x, y) for x, y, _ in labels])
        voiced = intersection(a, b, [(x, y) for x, y, v in labels if v])
        # Count only fully annotated windows. Gaps are not negative labels.
        eligible = abs(known - (b - a)) < .001
        prompted, present = window['state'] == 'voice', voiced > 0
        if eligible:
            window_cells['tp' if present and prompted else 'fn' if present else 'fp' if prompted else 'tn'] += 1
        else:
            skipped += 1
        windows.append({'start': a, 'end': b, 'voice_seconds': voiced,
                        'annotated_seconds': known, 'eligible': eligible})
    return {'seconds': metrics(cells), 'windows': metrics(window_cells),
            'excluded_windows': skipped, 'annotated_seconds': sum(cells.values()),
            'unannotated_seconds': max(0., duration - sum(cells.values())),
            'window_labels': windows}


def summarize(rows, selected):
    successful = [r for r in rows if r['status'] == 'measured']
    summary = {'selected': selected, 'measured': len(successful),
               'failed': len(rows) - len(successful), 'pending': selected - len(rows)}
    for unit in ('seconds', 'windows'):
        cells = {key: sum(r['score'][unit][key] for r in successful)
                 for key in ('tp', 'fp', 'fn', 'tn')}
        summary[unit] = metrics(cells)
    summary['unannotated_seconds'] = sum(r['score']['unannotated_seconds'] for r in successful)
    summary['excluded_windows'] = sum(r['score']['excluded_windows'] for r in successful)
    return summary


def detector_identity():
    import djmix
    from studio_content import GROUPS, MODEL_SHA256, RATE, STEP, VERSION, WINDOW, verify_model
    verify_model()
    root = Path(__file__).resolve().parent
    return {'version': VERSION, 'model_sha256': MODEL_SHA256, 'rate': RATE,
            'window': WINDOW, 'step': STEP, 'groups': {k: list(v) for k, v in GROUPS.items()},
            'sources': {name: file_hash(root / name) for name in
                        ('studio_content.py', 'studio_content_model.py', 'djmix.py')},
            'runtime': {name: importlib.metadata.version(name) for name in ('torch', 'numpy', 'soundfile')},
            'python': sys.version.split()[0],
            'ffmpeg': djmix.ffmpeg('-version').stdout.decode().splitlines()[0]}


def validate_resume(report, plan, plan_hash):
    rows = report['results']
    if (report['plan_sha256'] != plan_hash or len(rows) > len(plan['records'])
            or [r['record'] for r in rows] != plan['records'][:len(rows)]):
        raise ValueError('Saved results do not match the frozen development selection.')
    for row in rows:
        if row['status'] not in ('measured', 'failed'):
            raise ValueError('Unknown saved result.')
        if row['status'] == 'measured':
            if hashlib.sha256(row['annotation_text'].encode()).hexdigest() != row['record']['labels_sha256']:
                raise ValueError('Saved labels changed.')
            if row['score'] != score(row['analysis'], parse_labels(row['annotation_text'])):
                raise ValueError('Saved measurements changed.')


def save_report(path, report):
    pending = path.with_suffix('.tmp')
    pending.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    pending.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--max-new', type=int, help='Stop after this many additional songs; the selection stays frozen.')
    args = parser.parse_args()
    if args.max_new is not None and args.max_new < 1:
        parser.error('--max-new must be positive.')
    if args.archive.stat().st_size != ARCHIVE_BYTES or file_hash(args.archive, 'md5') != ARCHIVE_MD5:
        parser.error('Archive differs from the published Jamendo release.')
    with zipfile.ZipFile(args.archive) as archive:
        records = catalog(archive)
        plan = {'version': 1, 'source': SOURCE, 'archive_sha256': file_hash(args.archive),
                'split': 'train', 'records': records, 'detector': detector_identity(),
                'runner_sha256': file_hash(__file__),
                'protocol': 'Unchanged app thresholds. Duration scoring of merged voice prompts; '
                            'window scoring against any labeled voice. Gaps are unknown. No fitting.',
                'limitations': ['Development partition only; validation and test remain reserved.',
                    'Voice-presence labels do not assess competing vocals or musical quality.',
                    'Ten-second windows deliberately produce coarse listening ranges.',
                    'AudioSet training overlap is unaudited; this is not proof of unseen-data accuracy.',
                    'This corpus is not representative of AI-generated EDM; no calibrated app confidence.']}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        plan_path = args.out.with_suffix('.plan.json')
        if plan_path.exists():
            if json.loads(plan_path.read_text()) != plan:
                parser.error('The frozen experiment changed; use a new output path.')
        else:
            if args.out.exists():
                parser.error('Existing report has no frozen plan.')
            with plan_path.open('x') as handle:
                json.dump(plan, handle, indent=2, allow_nan=False)
        plan_hash = file_hash(plan_path)
        report = (json.loads(args.out.read_text()) if args.out.exists() else
                  {'plan': str(plan_path.resolve()), 'plan_sha256': plan_hash,
                   'limitations': plan['limitations'], 'results': []})
        validate_resume(report, plan, plan_hash)
        report['status'] = 'running'
        report['summary'] = summarize(report['results'], len(records))
        save_report(args.out, report)
        remaining = records[len(report['results']):]
        if args.max_new is not None:
            remaining = remaining[:args.max_new]
        import djmix
        from studio_content import analyze_content
        for record in remaining:
            started = time.monotonic()
            row = {'record': record}
            try:
                with tempfile.TemporaryDirectory(prefix='.content-measure-', dir=args.out.parent) as temporary:
                    work = Path(temporary)
                    audio = work / record['name']
                    with archive.open(record['audio']) as source, audio.open('wb') as target:
                        shutil.copyfileobj(source, target)
                    # App inference receives no annotation or label-derived duration.
                    duration = djmix.probe(audio)['duration']
                    analysis = analyze_content(audio, duration, work / 'cache')
                    analysis.pop('source', None)
                    annotations = archive.read(record['labels']).decode('utf-8')
                    row.update(status='measured', audio_sha256=file_hash(audio), analysis=analysis,
                               annotation_text=annotations, score=score(analysis, parse_labels(annotations)))
            except Exception as error:
                row.update(status='failed', error=f'{type(error).__name__}: {error}')
            row['seconds'] = round(time.monotonic() - started, 3)
            report['results'].append(row)
            report['summary'] = summarize(report['results'], len(records))
            save_report(args.out, report)
            print(f'{len(report["results"])}/{len(records)} {record["name"]}: {row["status"]} '
                  f'{row.get("error", "")} ({row["seconds"]}s)', flush=True)
        report['status'] = ('partial' if report['summary']['pending'] else
                            'finished_with_failures' if report['summary']['failed'] else 'finished')
        save_report(args.out, report)
        print(json.dumps({'status': report['status'], **report['summary']}))
        return int(report['summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
