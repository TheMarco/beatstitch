"""Shared controlled-audio and scoring primitives for development experiments.

Real-song transformations measure consistency, never absolute correctness.
Synthetic composition and event metadata are explicit construction references.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf

import djmix
from detector_pilot import atomic_json, file_hash, digest
from studio_process import check_cancelled

RATE = 48000
VERSION = 1


def load_plan(path):
    value = json.loads(Path(path).read_text())
    content = {k: v for k, v in value.items() if k not in ('plan_sha256', 'created_utc')}
    if value.get('version') != VERSION or value.get('plan_sha256') != digest(content):
        raise ValueError('The frozen experiment plan changed or is damaged.')
    root = Path(value.get('runtime_root', value.get('project_root', '.')))
    for name, expected in value.get('code_sha256', {}).items():
        if file_hash(root / name) != expected:
            raise ValueError('The frozen detector implementation changed: ' + name)
    return value


def verify_record(record):
    path = Path(record['audio'])
    if file_hash(path) != record['audio_sha256']:
        raise ValueError('The controlled baseline audio changed: ' + record['id'])
    info = sf.info(path)
    if info.samplerate != RATE or info.channels != 2 or info.frames != record['frames']:
        raise ValueError('Controlled audio format or frame count changed.')


def applicable_cases(plan, record, family):
    return [case for case in plan['cases'] if family in case['families']
            and record['kind'] in case['record_kinds']]


@contextmanager
def case_audio(record, case, scratch):
    """Keep one temporary transform, with exact event mapping for simple edits."""
    verify_record(record); check_cancelled()
    if case['type'] == 'baseline':
        yield Path(record['audio']); return
    scratch = Path(scratch); scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='controlled-', dir=scratch) as folder:
        path = Path(folder) / 'case.wav'
        kind = case['type']
        if kind in ('pitch', 'tempo'):
            djmix.transform(Path(record['audio']), path, 120., 120. * case.get('speed', 1.),
                            case.get('semitones', 0), formants=True)
        else:
            audio, rate = sf.read(record['audio'], dtype='float32', always_2d=True)
            if kind == 'gain': audio *= 10 ** (case['gain_db'] / 20)
            elif kind == 'swap': audio = audio[:, ::-1]
            elif kind == 'polarity': audio[:, 1] *= -1
            elif kind == 'prefix': audio = np.pad(audio, ((round(case['offset'] * rate), 0), (0, 0)))
            elif kind == 'crop': audio = audio[round(case['offset'] * rate):]
            else: raise ValueError('Unknown controlled transformation: ' + kind)
            if not np.isfinite(audio).all(): raise ValueError('Transformation produced invalid audio.')
            sf.write(path, audio, rate, subtype='FLOAT')
        info = sf.info(path)
        expected_seconds = record['frames'] / RATE / case.get('speed', 1.)
        if kind == 'prefix': expected_seconds += case['offset']
        if kind == 'crop': expected_seconds -= case['offset']
        if abs(info.duration - expected_seconds) > .03:
            raise ValueError('Controlled transformation has an unexpected duration.')
        check_cancelled(); yield path


def map_events(events, case, output_duration=None):
    values = np.asarray(events, dtype=float) / case.get('speed', 1.)
    if case['type'] == 'prefix': values += case['offset']
    if case['type'] == 'crop': values -= case['offset']
    values = values[values >= 0]
    if output_duration is not None: values = values[values < output_duration]
    return values.tolist()


def event_score(reference, predicted, tolerance):
    """Ordered one-to-one event matching; failures/omissions do not vanish."""
    a, b = np.asarray(reference, float), np.asarray(predicted, float)
    if (not math.isfinite(tolerance) or tolerance <= 0 or a.ndim != 1 or b.ndim != 1
            or not np.isfinite(a).all() or not np.isfinite(b).all()
            or np.any(a < 0) or np.any(b < 0) or np.any(np.diff(a) <= 0) or np.any(np.diff(b) <= 0)):
        raise ValueError('Expected finite, ordered event times and positive tolerance.')
    i = j = 0; differences = []
    while i < len(a) and j < len(b):
        if b[j] < a[i] - tolerance: j += 1
        elif a[i] < b[j] - tolerance: i += 1
        else:
            differences.append(float(b[j] - a[i])); i += 1; j += 1
    n = len(differences)
    precision = n / len(b) if len(b) else None
    recall = n / len(a) if len(a) else None
    return {'reference_events': len(a), 'predicted_events': len(b), 'matched': n,
            'missed': len(a) - n, 'extra': len(b) - n,
            'precision': precision, 'recall': recall,
            'f1': 2 * n / (len(a) + len(b)) if len(a) + len(b) else None,
            'offset_median_seconds': float(np.median(differences)) if n else None,
            'offset_p95_abs_seconds': float(np.percentile(np.abs(differences), 95)) if n else None}


def report_header(plan, family, runner):
    return {'version': VERSION, 'family': family, 'plan_sha256': plan['plan_sha256'],
            'runner_sha256': file_hash(runner), 'status': 'running', 'rows': [],
            'ui_promotion_allowed': False, 'limitations': [
                'This is development evidence; the supplied music has no independently verified labels.',
                'Transformation consistency does not establish the baseline key, beat or content label is correct.',
                'Constructed examples do not establish accuracy on real or AI-generated EDM.',
                'Raw scores are not calibrated confidence percentages.']}


def resume_report(path, plan, family, runner):
    path = Path(path)
    if not path.exists(): return report_header(plan, family, runner)
    saved = json.loads(path.read_text())
    if (saved.get('plan_sha256') != plan['plan_sha256'] or saved.get('family') != family
            or saved.get('runner_sha256') != file_hash(runner)):
        raise ValueError('The experiment changed. Preserve this result and use a new output directory.')
    return saved
