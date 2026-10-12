#!/usr/bin/env python3
"""Compare frozen OpenKeyScan with prior madmom results on both remaining partitions."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
              'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_name] = '1'

import djmix
from benchmark_key_confidence import annotations, wilson
from benchmark_openkeyscan_incremental import verify_runtime, write_new
from prototype_openkeyscan_key import MODEL, sha256, summarize


def checked_rows(report, split, all_records):
    expected = {r['id']: r for r in all_records if r['split'] == split}
    rows = report.get('results', [])
    actual = {r['record']['id']: r for r in rows}
    if (report.get('status') != 'finished' or report.get('split') != split
            or report.get('selected') != len(expected) or not expected
            or len(rows) != len(actual) or set(actual) != set(expected)):
        raise ValueError(f'Require every pinned {split} recording exactly once in a finished report.')
    for key, row in actual.items():
        if row['record'] != expected[key] or row['status'] not in ('estimated', 'abstained', 'failed'):
            raise ValueError('Reference annotation or status differs from the pinned partition.')
        if row['status'] == 'estimated':
            candidates = row.get('candidates', [])
            scores = [c['softmax'] for c in candidates]
            if (len(candidates) != 24 or len({c['key'] for c in candidates}) != 24
                    or any(not math.isfinite(v) or not 0 <= v <= 1 for v in scores)
                    or not math.isclose(sum(scores), 1., abs_tol=1e-5)
                    or scores[0] != max(scores) or row['key'] != candidates[0]['key']
                    or row['exact'] != (row['key'] == row['record']['key'])
                    or not 0 < row['duration'] <= 60
                    or row['frames'] != round(row['duration'] * 44100)):
                raise ValueError('Reference scores, correctness or excerpt are inconsistent.')
        elif row.get('exact', False):
            raise ValueError('A failed or abstained prediction cannot count as correct.')
    return actual


def check_disjoint(groups):
    seen = set()
    for records in groups:
        hashes = {r['record']['md5'] for r in records}
        if hashes & seen:
            raise ValueError('Audio overlaps the development, validation or test partitions.')
        seen |= hashes


def load_processor(root):
    import sys
    import torch
    sys.path.insert(0, str((root / 'source').resolve()))
    from openkeyscan_analyzer_server import preprocess_audio, load_model
    from dataset import CAMELOT_MAPPING
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    model = load_model(root / 'source' / MODEL, torch.device('cpu'))
    labels = []
    for index in range(24):
        spellings = {djmix.key_name(djmix.parse_key(k)) for k, v in CAMELOT_MAPPING.items() if v == index}
        if len(spellings) != 1 or spellings == {'Unknown'}:
            raise ValueError('Official class mapping cannot be resolved.')
        labels.append(spellings.pop())
    if len(set(labels)) != 24:
        raise ValueError('Official class mapping is not 24 distinct keys.')

    def process(wav):
        spec = preprocess_audio(wav).unsqueeze(0)
        with torch.inference_mode():
            prediction = torch.softmax(model(spec), dim=1).cpu().numpy()
        return prediction, list(spec.shape)

    return process, labels


def predict(reference, audio_root, temporary_root, processor, labels):
    """Same decoding, float WAV and official preprocessing/model as the frozen prototype."""
    import numpy as np
    import soundfile as sf
    record = reference['record']
    source = audio_root / (record['id'] + '.mp3')
    if hashlib.md5(source.read_bytes()).hexdigest() != record['md5']:
        raise ValueError('Audio differs from the benchmark checksum.')
    duration = float(reference['duration'])
    decoded = djmix.ffmpeg('-i', str(source), '-t', str(duration), '-map', '0:a:0',
                           '-ar', '44100', '-ac', '1', '-f', 'f32le', '-').stdout
    audio = np.frombuffer(decoded, dtype='<f4').copy()
    if not len(audio) or not np.isfinite(audio).all() or len(audio) != reference['frames']:
        raise ValueError('Invalid audio or mismatched excerpt length.')
    row = {'record': record, 'duration': duration, 'frames': len(audio),
           'decoded_sha256': hashlib.sha256(decoded).hexdigest()}
    if np.max(np.abs(audio)) < 1e-5:
        return {**row, 'status': 'abstained', 'reason': 'Effectively silent excerpt.'}
    with tempfile.TemporaryDirectory(prefix='openkeyscan-evaluation-', dir=temporary_root) as tmp:
        wav = Path(tmp) / 'excerpt.wav'
        sf.write(wav, audio, 44100, subtype='FLOAT')
        reread, rate = sf.read(wav, dtype='float32')
        if rate != 44100 or not np.array_equal(audio, reread):
            raise ValueError('WAV round-trip changed the audio.')
        prediction, shape = processor(wav)
    if (prediction.shape != (1, 24) or not np.isfinite(prediction).all()
            or not np.isclose(prediction.sum(), 1., atol=1e-5)):
        raise ValueError('Invalid key model output.')
    scores = prediction[0]
    candidates = [{'key': labels[i], 'softmax': float(scores[i])} for i in np.argsort(-scores)]
    return {**row, 'status': 'estimated', 'key': candidates[0]['key'], 'candidates': candidates,
            'exact': candidates[0]['key'] == record['key'],
            'top_three': any(c['key'] == record['key'] for c in candidates[:3]),
            'float_wav_roundtrip': 'passed', 'spectrogram_shape': shape}


def check_parity(old, new):
    for field in ('record', 'status', 'duration', 'frames', 'decoded_sha256', 'key',
                  'exact', 'top_three', 'candidates', 'float_wav_roundtrip', 'spectrogram_shape'):
        if old.get(field) != new.get(field):
            raise ValueError(f'Evaluation differs from the frozen development prediction: {field}')
    return {'id': old['record']['id'], 'all_24_scores_exact': True, 'decoded_audio_exact': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--development', type=Path, required=True)
    parser.add_argument('--madmom-validation', type=Path, required=True)
    parser.add_argument('--madmom-test', type=Path, required=True)
    parser.add_argument('--research-root', type=Path, default=Path('outputs/openkeyscan-research'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    plan_path = args.out.with_suffix('.plan.json')
    if args.out.exists() or plan_path.exists():
        parser.error('Choose fresh output paths; preserve earlier evidence.')
    inputs = {'development': args.development, 'validation': args.madmom_validation,
              'test': args.madmom_test, 'annotations': args.root / 'annotations.zip'}
    all_records = annotations(inputs['annotations'])
    development = json.loads(args.development.read_text())
    developed = checked_rows(development, 'calibration', all_records)
    references = {split: checked_rows(json.loads(inputs[split].read_text()), split, all_records)
                  for split in ('validation', 'test')}
    check_disjoint([list(developed.values()), *[list(r.values()) for r in references.values()]])
    detector = development['detector']
    verify_runtime(detector, args.research_root)
    processor, labels = load_processor(args.research_root)
    originals = sorted(developed.values(), key=lambda row: row['record']['id'])
    parity = []
    for index in (0, len(originals) // 2, len(originals) - 1):
        old = originals[index]
        parity.append(check_parity(old, predict(old, args.root / 'audio', args.research_root,
                                               processor, labels)))
    plan = {
        'status': 'frozen_before_validation_and_test_inference',
        'inputs': {name: {'path': str(path.resolve()), 'sha256': sha256(path)} for name, path in inputs.items()},
        'detector': detector, 'evaluation_script_sha256': sha256(Path(__file__)),
        'reference_parity': parity,
        'records': {split: [r['record'] for r in rows.values()] for split, rows in references.items()},
        'checks': [
            'Use the unchanged official default checkpoint and development preprocessing.',
            'Evaluate every validation and test recording; retain failures in the denominator.',
            'No fitting, checkpoint selection, confidence threshold tuning or excerpt selection.',
            'Both partitions have already been evaluated with madmom; do not call this a new unseen test.',
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_new(plan_path, plan)
    print('Exact parity on three development recordings passed. Both evaluation partitions frozen.', flush=True)
    report = {
        'status': 'running', 'plan': str(plan_path.resolve()), 'plan_sha256': sha256(plan_path),
        'detector': detector, 'evaluation_script_sha256': sha256(Path(__file__)),
        'selected': sum(len(rows) for rows in references.values()),
        'limitations': [
            'The validation and test partitions have already informed prior madmom evaluation.',
            'Official OpenKeyScan development uses GiantSteps; complete checkpoint training and '
            'model-selection recording provenance has not been independently audited.',
            'Different recordings/remixes can be related despite byte-disjoint partitions.',
            'Whole-excerpt key labels do not validate short overlaps or user-generated music.',
            'Softmax scores are not calibrated correctness probabilities.',
            'Runtime is indicative; prior madmom runs were measured separately.',
        ],
        'results': [],
    }

    def save():
        report['summary'] = {split: summarize([r for r in report['results'] if r['record']['split'] == split])
                             for split in references}
        for values in report['summary'].values():
            if values['attempted']:
                values['wilson_95'] = wilson(values['exact'], values['attempted'])
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        temporary.replace(args.out)

    save()
    for split, rows in references.items():
        for old in rows.values():
            started = time.monotonic()
            try:
                row = predict(old, args.root / 'audio', args.research_root, processor, labels)
            except Exception as error:
                row = {'record': old['record'], 'status': 'failed', 'error': f'{type(error).__name__}: {error}'}
            row.update(madmom_key=old.get('key'), madmom_exact=old.get('exact', False),
                       seconds=round(time.monotonic() - started, 3))
            report['results'].append(row)
            save()
            print(f'{len(report["results"])}/{report["selected"]}: {split} {old["record"]["id"]} '
                  f'{row["status"]} {row.get("key", row.get("error", ""))}', flush=True)
    for split, rows in references.items():
        checked_rows({'status': 'finished', 'split': split, 'selected': len(rows),
                      'results': [r for r in report['results'] if r['record']['split'] == split]},
                     split, all_records)
    report['status'] = 'finished'
    save()
    print(json.dumps(report['summary']), flush=True)
    return int(any(v['failed'] for v in report['summary'].values()))


if __name__ == '__main__':
    raise SystemExit(main())
