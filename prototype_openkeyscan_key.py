#!/usr/bin/env python3
"""Compare pinned OpenKeyScan with madmom on the existing development pilot."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

import djmix

REVISION = 'd5cac64bc7195826b6eb10c55b9b0c97fb3287c0'
MODEL = 'checkpoints/openkeyscan3.pt'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(rows):
    times = [row['seconds'] for row in rows]
    exact = sum(row.get('exact', False) for row in rows)
    return {
        'attempted': len(rows),
        'estimated': sum(row['status'] == 'estimated' for row in rows),
        'abstained': sum(row['status'] == 'abstained' for row in rows),
        'failed': sum(row['status'] == 'failed' for row in rows),
        'exact': exact,
        'correct_fraction_of_attempts': exact / len(rows) if rows else None,
        'top_three': sum(row.get('top_three', False) for row in rows),
        'madmom_exact': sum(row['madmom_exact'] for row in rows),
        'improved_over_madmom': sum(row.get('exact', False) and not row['madmom_exact']
                                    for row in rows),
        'regressed_from_madmom': sum(not row.get('exact', False) and row['madmom_exact']
                                      for row in rows),
        'same_key_as_madmom': sum(row.get('key') == row['madmom_key'] for row in rows),
        'processing_seconds_total': sum(times),
        'processing_seconds_median': statistics.median(times) if times else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('--madmom', type=Path, required=True)
    parser.add_argument('--research-root', type=Path, default=Path('outputs/openkeyscan-research'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Choose a new output filename; existing results are preserved.')
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive.')
    baseline = json.loads(args.baseline.read_text())
    madmom = json.loads(args.madmom.read_text())
    for report in (baseline, madmom):
        if report.get('status') != 'finished' or report.get('split') != 'calibration':
            parser.error('Use completed development/calibration reports only.')
    old_rows = baseline['results'][:args.limit]
    reference = madmom['results'][:args.limit]
    if not old_rows or [r['record'] for r in old_rows] != [r['record'] for r in reference]:
        parser.error('Baseline and madmom must contain the same ordered recordings and labels.')
    if any(r['record']['split'] != 'calibration' for r in old_rows):
        parser.error('Every recording must belong to the development partition.')

    root = args.research_root.resolve()
    source = root / 'source'
    manifest = json.loads((root / 'sources.json').read_text())
    if manifest['revision'] != REVISION:
        parser.error('Use the pinned official OpenKeyScan source.')
    for name in ('openkeyscan_analyzer_server.py', 'model.py', 'eval.py', 'dataset.py', MODEL):
        if sha256(source / name) != manifest['files'][name]['sha256']:
            parser.error(f'Source/model differs from its recorded download: {name}')

    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'
    startup = time.monotonic()
    import numpy as np
    import soundfile as sf
    import torch
    sys.path.insert(0, str(source))
    from openkeyscan_analyzer_server import preprocess_audio, camelot_output, load_model
    from dataset import CAMELOT_MAPPING

    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    model = load_model(source / MODEL, torch.device('cpu'))
    # Check every enharmonic spelling resolves to the same canonical app key.
    labels = []
    for index in range(24):
        spellings = [key for key, value in CAMELOT_MAPPING.items() if value == index]
        canonical = {djmix.key_name(djmix.parse_key(key)) for key in spellings}
        if len(canonical) != 1 or canonical == {'Unknown'}:
            raise ValueError(f'Invalid class mapping at index {index}: {spellings}')
        labels.append(canonical.pop())
        expected_camelot = f'{index % 12 + 1}{"A" if index < 12 else "B"}'
        assert camelot_output(index)[0] == expected_camelot
    assert len(set(labels)) == 24

    report = {
        'status': 'running', 'split': 'calibration', 'selected': len(old_rows),
        'baseline': str(args.baseline.resolve()), 'baseline_sha256': sha256(args.baseline),
        'madmom': str(args.madmom.resolve()), 'madmom_sha256': sha256(args.madmom),
        'detector': {
            'library': 'OpenKeyScan', 'source_revision': REVISION,
            'model': MODEL, 'model_sha256': sha256(source / MODEL),
            'source_manifest': manifest, 'python': platform.python_version(),
            'dependencies': {name: importlib.metadata.version(name) for name in
                             ('torch', 'librosa', 'numpy', 'scipy', 'soundfile', 'numba')},
            'device': 'cpu', 'threads': 1, 'runner_sha256': sha256(Path(__file__)),
            'ffmpeg': djmix.run(['ffmpeg', '-version']).stdout.decode().splitlines()[0],
        },
        'input': 'Same first-minute excerpts, FFmpeg mono 44100 Hz float32; lossless float WAV '
                 'passed through unmodified official preprocess_audio and model loader.',
        'startup_seconds': time.monotonic() - startup,
        'checks': {'all_24_key_labels': 'passed'},
        'limitations': baseline['limitations'] + [
            'Development pilot only, not independent evidence of generalization.',
            'Official project uses GiantSteps for evaluation; checkpoint-specific training '
            'and model-selection recording provenance has not been independently audited.',
            'Softmax scores are uncalibrated model outputs, not correctness probabilities.',
            'Uses existing ARM64 Python 3.13/PyTorch 2.11 environment; official dual-architecture '
            'build instructions use Python 3.12/PyTorch 2.2.2. No packaging claim.',
            'No local-overlap, beat-timing or user-generated-music validation.',
            'Per-track times include decode, temporary WAV, preprocessing and inference; '
            'first track may include cold library initialization. Madmom times are from the prior run.',
        ],
        'results': [],
    }

    def save():
        report['summary'] = summarize(report['results'])
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        temporary.replace(args.out)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    save()
    for old, prior in zip(old_rows, reference):
        row = {'record': old['record'], 'status': 'failed',
               'baseline_exact': old.get('exact', False), 'madmom_key': prior.get('key'),
               'madmom_exact': prior.get('exact', False)}
        started = time.monotonic()
        try:
            audio_path = args.baseline.parent / 'audio' / (old['record']['id'] + '.mp3')
            if hashlib.md5(audio_path.read_bytes()).hexdigest() != old['record']['md5']:
                raise ValueError('Audio differs from the benchmark checksum.')
            duration = float(old['duration'])
            if duration != prior['duration'] or not 0 < duration <= 60:
                raise ValueError('Excerpt duration does not match the prior experiment.')
            decoded = djmix.ffmpeg('-i', str(audio_path), '-t', str(duration), '-map', '0:a:0',
                                   '-ar', '44100', '-ac', '1', '-f', 'f32le', '-').stdout
            audio = np.frombuffer(decoded, dtype='<f4').copy()
            if not len(audio) or not np.isfinite(audio).all() or len(audio) != prior['frames']:
                raise ValueError('Invalid audio or mismatched excerpt length.')
            row.update(duration=duration, frames=len(audio),
                       decoded_sha256=hashlib.sha256(decoded).hexdigest())
            if np.max(np.abs(audio)) < 1e-5:
                row.update(status='abstained', reason='Effectively silent excerpt.')
            else:
                with tempfile.TemporaryDirectory(prefix='openkeyscan-', dir=root) as tmp:
                    wav = Path(tmp) / 'excerpt.wav'
                    sf.write(wav, audio, 44100, subtype='FLOAT')
                    reread, rate = sf.read(wav, dtype='float32')
                    if rate != 44100 or not np.array_equal(audio, reread):
                        raise ValueError('WAV round-trip changed the audio.')
                    spec = preprocess_audio(wav).unsqueeze(0)
                    with torch.inference_mode():
                        logits = model(spec)
                        prediction = torch.softmax(logits, dim=1).cpu().numpy()
                if (prediction.shape != (1, 24) or not np.isfinite(prediction).all()
                        or not np.isclose(prediction.sum(), 1., atol=1e-5)):
                    raise ValueError('Invalid key model output.')
                scores = prediction[0]
                order = np.argsort(-scores)
                candidates = [{'key': labels[i], 'softmax': float(scores[i])} for i in order]
                row.update(status='estimated', key=candidates[0]['key'], candidates=candidates,
                           exact=candidates[0]['key'] == old['record']['key'],
                           top_three=any(c['key'] == old['record']['key'] for c in candidates[:3]),
                           float_wav_roundtrip='passed', spectrogram_shape=list(spec.shape))
        except Exception as error:
            row['error'] = f'{type(error).__name__}: {error}'
        row['seconds'] = round(time.monotonic() - started, 3)
        report['results'].append(row)
        save()
        print(f'{len(report["results"])}/{len(old_rows)}: {old["record"]["id"]} '
              f'{row["status"]} {row.get("key", row.get("error", ""))}', flush=True)
    report['status'] = 'finished'
    save()
    print(json.dumps(report['summary']))
    return int(report['summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
