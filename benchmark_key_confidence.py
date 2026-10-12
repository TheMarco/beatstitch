#!/usr/bin/env python3
"""Measure the existing key detector on pinned GiantSteps labels; never invent confidence."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import time
import urllib.request
import zipfile

import djmix

REVISION = '6bcd492c825ac9b8597bc650a5f6fd18b6c43d2b'
ARCHIVE_URL = f'https://codeload.github.com/GiantSteps/giantsteps-key-dataset/zip/{REVISION}'
AUDIO_URL = 'https://www.cp.jku.at/datasets/giantsteps/backup/'
VERSION = 1
SPLITS = ('calibration', 'validation', 'test')
ROOT = Path(__file__).resolve().parent


def partition(checksum):
    # Group byte-identical recordings together before looking at predictions.
    bucket = int(hashlib.sha256(('dj-key-v1:' + checksum).encode()).hexdigest()[:8], 16) % 10
    return 'calibration' if bucket < 6 else 'validation' if bucket < 8 else 'test'


def annotations(archive):
    prefix = f'giantsteps-key-dataset-{REVISION}/'
    records = []
    with zipfile.ZipFile(archive) as data:
        for name in sorted(data.namelist()):
            match = re.fullmatch(re.escape(prefix) + r'annotations/key/(\d+\.LOFI)\.key', name)
            if not match:
                continue
            identity = match[1]
            original = data.read(name).decode('utf-8').strip()
            key = djmix.key_name(djmix.parse_key(original))
            checksum = data.read(prefix + 'md5/' + identity + '.md5').decode().strip()
            if not re.fullmatch('[0-9a-f]{32}', checksum) or not key:
                raise ValueError(f'Invalid annotation for {identity}.')
            records.append({'id': identity, 'key': key, 'original_label': original,
                            'md5': checksum, 'split': partition(checksum)})
    if len(records) != 604:
        raise ValueError(f'Expected the pinned 604-recording dataset; found {len(records)}.')
    # A fixed shuffled order makes a bounded pilot repeatable, independent of labels.
    return sorted(records, key=lambda row: hashlib.sha256(('dj-key-order-v1:' + row['id']).encode()).digest())


def download(url, path, *, maximum, md5=None):
    path = Path(path)
    if path.exists():
        if md5 and hashlib.md5(path.read_bytes()).hexdigest() != md5:
            raise ValueError(f'Existing file does not match the published checksum: {path}. Kept unchanged.')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.download')
    created = False
    try:
        with urllib.request.urlopen(url, timeout=30) as response, temporary.open('xb') as handle:
            created = True
            count = 0
            while chunk := response.read(256 * 1024):
                count += len(chunk)
                if count > maximum:
                    raise ValueError(f'Download exceeds the expected size: {path.name}')
                handle.write(chunk)
        if md5 and hashlib.md5(temporary.read_bytes()).hexdigest() != md5:
            raise ValueError(f'Download checksum mismatch: {path.name}')
        temporary.replace(path)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def detector_identity():
    files = ('analyze_audio.py', 'djmix.py', 'benchmark_key_confidence.py')
    return {'version': VERSION, 'excerpt': 'first 60 seconds (or entire shorter preview)',
            'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files},
            'python': platform.python_version(),
            'dependencies': {name: importlib.metadata.version(name) for name in ('librosa', 'numpy', 'numba')},
            'ffmpeg': djmix.run(['ffmpeg', '-version']).stdout.decode().splitlines()[0]}


def evaluate(record, root, detector):
    from analyze_audio import analyze
    root = Path(root)
    source = root / 'audio' / (record['id'] + '.mp3')
    key = hashlib.sha256(json.dumps([record, detector], sort_keys=True).encode()).hexdigest()[:24]
    saved = root / 'results' / (key + '.json')
    if saved.is_file():
        result = json.loads(saved.read_text())
        if result.get('record') == record and result.get('detector') == detector:
            return result
        raise ValueError(f'Invalid cached benchmark record: {saved}')
    result = {'record': record, 'detector': detector, 'status': 'failed'}
    started = time.monotonic()
    try:
        if hashlib.md5(source.read_bytes()).hexdigest() != record['md5']:
            raise ValueError('Audio differs from the published benchmark recording.')
        duration = min(60., djmix.probe(source)['duration'])
        estimate = analyze(source, 0, duration, bars=8 if duration >= 25 else 4)
        candidates = estimate['section_key_candidates']
        result.update(status='estimated' if candidates else 'abstained', duration=duration,
                      candidates=candidates, exact=bool(candidates and candidates[0]['key'] == record['key']),
                      top_three=any(c['key'] == record['key'] for c in candidates))
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    result['seconds'] = round(time.monotonic() - started, 3)
    saved.parent.mkdir(parents=True, exist_ok=True)
    temporary = saved.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(saved)
    return result


def wilson(correct, count):
    if not count:
        return None
    z = 1.959963984540054
    p = correct / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    margin = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return [max(0., center - margin), min(1., center + margin)]


def summarize(results):
    estimated = [row for row in results if row['status'] == 'estimated']
    correct = sum(row['exact'] for row in estimated)
    bins = []
    for low, high in ((-1., .4), (.4, .6), (.6, .8), (.8, 1.000001)):
        rows = [r for r in estimated if low <= r['candidates'][0]['correlation'] < high]
        hits = sum(r['exact'] for r in rows)
        bins.append({'correlation_min': low, 'correlation_max': min(1., high), 'count': len(rows),
                     'exact': hits, 'accuracy': hits / len(rows) if rows else None,
                     'wilson_95': wilson(hits, len(rows))})
    return {'attempted': len(results), 'estimated': len(estimated),
            'abstained': sum(r['status'] == 'abstained' for r in results),
            'failed': sum(r['status'] == 'failed' for r in results), 'exact': correct,
            'accuracy_of_estimates': correct / len(estimated) if estimated else None,
            'correct_fraction_of_attempts': correct / len(results) if results else None,
            'wilson_95': wilson(correct, len(estimated)),
            'top_three': sum(r['top_three'] for r in estimated), 'correlation_bins': bins}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--split', choices=SPLITS, default='calibration')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--download', action='store_true', help='Download selected public benchmark previews after verifying pinned annotations.')
    parser.add_argument('--workers', type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive.')
    root = args.root.resolve(); root.mkdir(parents=True, exist_ok=True)
    archive = root / 'annotations.zip'
    if args.download:
        download(ARCHIVE_URL, archive, maximum=8 * 1024 * 1024)
    records = [r for r in annotations(archive) if r['split'] == args.split]
    total = len(records)
    records = records[:args.limit]
    if args.download:
        for i, row in enumerate(records, 1):
            download(AUDIO_URL + row['id'] + '.mp3', root / 'audio' / (row['id'] + '.mp3'),
                     maximum=4 * 1024 * 1024, md5=row['md5'])
            print(f'Audio {i}/{len(records)} verified: {row["id"]}', flush=True)
    detector = detector_identity()
    digest = hashlib.sha256(json.dumps([records, detector], sort_keys=True).encode()).hexdigest()[:16]
    report_path = root / f'{args.split}-{len(records)}-{digest}.json'
    report = {'dataset': 'GiantSteps key (original 604 annotations)', 'revision': REVISION,
              'annotation_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'dataset_url': 'https://www.cp.jku.at/datasets/giantsteps/', 'split': args.split,
              'split_total': total, 'selected': len(records), 'detector': detector, 'status': 'running',
              'limitations': [
                  'Global preview labels do not establish the correct key of every short overlap.',
                  'This benchmark is not a calibrated probability model and does not measure blend quality.',
                  'A pilot subset is not a full-dataset result; failures remain in the denominator.',
                  'Splits group identical files; unknown remixes or recordings may still be related.',
                  'No claim is made for generated music, timing confidence or voice detection.'],
              'results': []}

    def save():
        report['summary'] = summarize(report['results'])
        temp = report_path.with_suffix('.tmp')
        temp.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        temp.replace(report_path)
    save()
    # Limit native numerical thread pools before starting workers.
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[name] = '1'
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(evaluate, row, str(root), detector) for row in records]
        for job in as_completed(jobs):
            result = job.result()
            report['results'].append(result)
            save()
            print(f'Analyzed {len(report["results"])}/{len(records)}: {result["record"]["id"]} {result["status"]}', flush=True)
    report['results'].sort(key=lambda r: r['record']['id'])
    report['status'] = 'finished'
    save()
    print(json.dumps(report['summary'], indent=2))
    print(f'Report: {report_path}')
    return int(report['summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
