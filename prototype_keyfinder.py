#!/usr/bin/env python3
"""Compare libKeyFinder against a completed key benchmark on the same excerpts."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

import djmix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('--helper', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Choose a new output filename; existing results are preserved.')
    baseline = json.loads(args.baseline.read_text())
    if baseline.get('status') != 'finished' or baseline.get('split') != 'calibration':
        parser.error('Prototype comparison requires a completed calibration-partition report.')
    helper = args.helper.resolve()
    report = {'status': 'running', 'baseline': str(args.baseline.resolve()),
              'baseline_sha256': hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
              'library': 'libKeyFinder 2.2.8', 'revision': 'b33b5a88e04a5182dd19c38c57762925631118fd',
              'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(),
              'helper_source_sha256': hashlib.sha256(Path(__file__).with_suffix('.cpp').read_bytes()).hexdigest(),
              'input': 'Same first-minute excerpts; FFmpeg mono 44.1 kHz float32',
              'limitations': baseline['limitations'], 'results': []}

    def save():
        rows = report['results']
        report['summary'] = {'attempted': len(rows), 'exact': sum(r.get('exact', False) for r in rows),
                             'failed': sum(r['status'] == 'failed' for r in rows),
                             'baseline_exact': sum(r['baseline_exact'] for r in rows)}
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(args.out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    save()
    for old in baseline['results']:
        row = {'record': old['record'], 'baseline_exact': old.get('exact', False), 'status': 'failed'}
        started = time.monotonic()
        try:
            source = args.baseline.parent / 'audio' / (row['record']['id'] + '.mp3')
            if hashlib.md5(source.read_bytes()).hexdigest() != row['record']['md5']:
                raise ValueError('Source does not match the benchmark checksum.')
            duration = old.get('duration', min(60., djmix.probe(source)['duration']))
            audio = djmix.ffmpeg('-i', str(source), '-t', str(duration), '-map', '0:a:0',
                                '-ar', '44100', '-ac', '1', '-f', 'f32le', '-').stdout
            result = subprocess.run([str(helper)], input=audio, capture_output=True, timeout=60)
            if result.returncode:
                raise ValueError(result.stderr.decode(errors='replace')[-1000:])
            estimate = json.loads(result.stdout)
            expected = len(audio) // 4
            if estimate['frames'] != expected:
                raise ValueError('Helper did not consume the complete excerpt.')
            key = djmix.key_name(djmix.parse_key(estimate['key'])) if estimate['key'] else ''
            row.update(status='estimated' if key else 'abstained', key=key,
                       exact=key == row['record']['key'], duration=duration)
        except Exception as error:
            row['error'] = f'{type(error).__name__}: {error}'
        row['seconds'] = round(time.monotonic() - started, 3)
        report['results'].append(row)
        save()
        print(f'{len(report["results"])}/{len(baseline["results"])}: {row["record"]["id"]} {row["status"]}', flush=True)
    report['status'] = 'finished'
    save()
    print(json.dumps(report['summary']))
    return int(report['summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
