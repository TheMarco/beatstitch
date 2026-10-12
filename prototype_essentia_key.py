#!/usr/bin/env python3
"""Compare Essentia's default and EDM profiles on existing calibration excerpts."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import time

import djmix

PROFILES = ('edma', 'bgate')
VERSION = '2.1b6.dev1389'


def summarize(rows, profile):
    estimates = [row.get('profiles', {}).get(profile, {'status': 'failed'}) for row in rows]
    exact = sum(value.get('exact', False) for value in estimates)
    return {'attempted': len(rows), 'estimated': sum(v['status'] == 'estimated' for v in estimates),
            'abstained': sum(v['status'] == 'abstained' for v in estimates),
            'failed': sum(v['status'] == 'failed' for v in estimates), 'exact': exact,
            'correct_fraction_of_attempts': exact / len(rows) if rows else None,
            'baseline_exact': sum(row['baseline_exact'] for row in rows),
            'improved': sum(v.get('exact', False) and not row['baseline_exact']
                            for row, v in zip(rows, estimates)),
            'regressed': sum(not v.get('exact', False) and row['baseline_exact']
                             for row, v in zip(rows, estimates))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('--research-root', type=Path, default=Path('outputs/essentia-key-research'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Choose a new output filename; existing results are preserved.')
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive.')
    baseline = json.loads(args.baseline.read_text())
    if baseline.get('status') != 'finished' or baseline.get('split') != 'calibration':
        parser.error('Prototype comparison requires a completed calibration-partition report.')
    records = baseline['results'][:args.limit]
    if not records or any(row['record']['split'] != 'calibration' for row in records):
        parser.error('Every selected recording must belong to the calibration partition.')
    import numpy as np
    import essentia
    from essentia.standard import KeyExtractor
    if importlib.metadata.version('essentia') != VERSION:
        parser.error('Use the pinned Essentia package for this comparison.')
    distribution = json.loads((args.research_root / 'source.json').read_text())
    wheel = args.research_root / distribution['wheel']
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != distribution['sha256']:
        parser.error('The downloaded wheel differs from its recorded checksum.')
    extractors = {profile: KeyExtractor(sampleRate=44100, profileType=profile) for profile in PROFILES}
    report = {'status': 'running', 'split': 'calibration', 'selected': len(records),
              'baseline': str(args.baseline.resolve()),
              'baseline_sha256': hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
              'detector': {'library': 'Essentia', 'version': VERSION, 'distribution': distribution,
                           'git_sha': getattr(essentia, '__version_git_sha__', None),
                           'python': platform.python_version(),
                           'dependencies': {name: importlib.metadata.version(name)
                                            for name in ('numpy', 'six', 'pyyaml')},
                           'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           'ffmpeg': djmix.run(['ffmpeg', '-version']).stdout.decode().splitlines()[0],
                           'parameters': {profile: {name: ext.paramValue(name) for name in ext.parameterNames()}
                                          for profile, ext in extractors.items()}},
              'input': 'Same first-minute excerpts; FFmpeg mono 44.1 kHz float32',
              'limitations': baseline['limitations'] + [
                  'Essentia strength is a profile correlation, not a calibrated correctness probability.',
                  'Fixed edma and default bgate profiles; no parameter search or fitting on these labels.',
                  'This experiment does not independently audit all recordings used to derive the profiles.'],
              'results': []}

    def save():
        report['summary'] = {profile: summarize(report['results'], profile) for profile in PROFILES}
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        temporary.replace(args.out)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    save()
    for old in records:
        row = {'record': old['record'], 'baseline_exact': old.get('exact', False), 'profiles': {}}
        started = time.monotonic()
        try:
            source = args.baseline.parent / 'audio' / (row['record']['id'] + '.mp3')
            if hashlib.md5(source.read_bytes()).hexdigest() != row['record']['md5']:
                raise ValueError('Source does not match the benchmark checksum.')
            duration = old.get('duration', min(60., djmix.probe(source)['duration']))
            decoded = djmix.ffmpeg('-i', str(source), '-t', str(duration), '-map', '0:a:0',
                                   '-ar', '44100', '-ac', '1', '-f', 'f32le', '-').stdout
            audio = np.frombuffer(decoded, dtype='<f4').copy()
            if not len(audio) or not np.isfinite(audio).all():
                raise ValueError('Decoded audio is empty or non-finite.')
            row.update(duration=duration, frames=len(audio))
            for profile, extractor in extractors.items():
                value = {'status': 'failed'}
                try:
                    if np.max(np.abs(audio)) < 1e-5:
                        value.update(status='abstained', reason='Effectively silent excerpt.')
                    else:
                        tonic, mode, strength = extractor(audio)
                        if not np.isfinite(strength):
                            raise ValueError('Non-finite key strength.')
                        key = djmix.key_name(djmix.parse_key(f'{tonic} {mode}'))
                        value.update(status='estimated', key=key, strength=float(strength),
                                     exact=key == row['record']['key'])
                except Exception as error:
                    value['error'] = f'{type(error).__name__}: {error}'
                row['profiles'][profile] = value
        except Exception as error:
            row['error'] = f'{type(error).__name__}: {error}'
        row['seconds'] = round(time.monotonic() - started, 3)
        report['results'].append(row)
        save()
        print(f'{len(report["results"])}/{len(records)}: {row["record"]["id"]} '
              + ' '.join(f'{p}={v.get("key", v["status"])}' for p, v in row['profiles'].items()), flush=True)
    report['status'] = 'finished'
    save()
    print(json.dumps(report['summary']))
    return int(any(s['failed'] for s in report['summary'].values()))


if __name__ == '__main__':
    raise SystemExit(main())
