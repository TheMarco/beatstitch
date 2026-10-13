#!/usr/bin/env python3
"""Controlled key-detector experiment on a frozen plan (research only).

Every backend receives the identical complete case WAV. Real-song cases are
scored only for baseline-relative consistency, never for correctness; only
constructed tonal records carry a composition key that can be scored exactly.
Failures, abstentions and unavailable runtimes stay in attempted denominators.
Detector agreement does not establish that any key is correct.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import numpy as np
import soundfile as sf

import djmix
from detector_experiment_common import (
    applicable_cases, atomic_json, case_audio, file_hash, load_plan,
    report_header, resume_report, verify_record,
)
from studio_process import check_cancelled

ROOT = Path(__file__).resolve().parent
BACKENDS = ('openkeyscan', 'essentia_edma', 'essentia_bgate', 'madmom',
            'libkeyfinder', 'pitch_profile')
# Backends whose libraries live only under the research package trees are
# never imported into this process; they run through ``--infer`` subprocesses.
RESEARCH_PACKAGES = {'essentia_edma': 'essentia-key-research',
                     'essentia_bgate': 'essentia-key-research',
                     'madmom': 'madmom-research'}
SUBPROCESS_TIMEOUT = 300
KEYFINDER_TIMEOUT = 120
VALID_STATUSES = ('estimated', 'uncertain', 'abstained', 'failed', 'unavailable')
KEYS = {djmix.key_name((root, mode)) for mode in ('major', 'minor') for root in range(12)}
_ESSENTIA_EXTRACTORS = {}


def _parse(value):
    try:
        return djmix.parse_key(value) if value else None
    except Exception:
        return None


def _research_root(project_root, backend):
    return Path(project_root) / 'outputs' / RESEARCH_PACKAGES[backend]


def _research_python(project_root):
    for candidate in (Path(project_root) / '.venv' / 'bin' / 'python',
                      Path(project_root) / '.venv' / 'Scripts' / 'python.exe'):
        if candidate.is_file():
            return candidate
    return None


def _validate_result(result):
    """Normalize one backend result; reject invalid keys or non-finite scores."""
    if not isinstance(result, dict):
        raise ValueError('Backend returned no result object.')
    status = result.get('status')
    if status not in VALID_STATUSES:
        raise ValueError(f'Backend returned an unknown status: {status!r}.')
    candidates = []
    for entry in result.get('candidates') or []:
        key = entry.get('key') if isinstance(entry, dict) else None
        if key not in KEYS:
            raise ValueError(f'Backend returned an invalid candidate key: {key!r}.')
        score = entry.get('score', entry.get('softmax', entry.get('correlation')))
        if score is not None and (not isinstance(score, (int, float))
                                  or isinstance(score, bool) or not math.isfinite(score)):
            raise ValueError('Backend returned a non-finite candidate score.')
        candidates.append({'key': key, 'score': None if score is None else float(score)})
    key = result.get('key') or (candidates[0]['key'] if candidates else None)
    if status == 'estimated':
        if key not in KEYS or not candidates or candidates[0]['key'] != key:
            raise ValueError('Estimated result lacks a valid top candidate.')
    elif candidates and key is not None and key not in KEYS:
        raise ValueError('Backend returned an invalid key.')
    extra = {name: result[name] for name in ('strength', 'identity', 'helper_sha256',
             'helper_source_sha256', 'source_archives_verified', 'manifest') if name in result}
    if 'strength' in extra and not math.isfinite(extra['strength']):
        raise ValueError('Backend returned a non-finite strength.')
    return {'status': status, 'key': key if status == 'estimated' else key,
            'candidates': candidates,
            'reason': str(result.get('reason', ''))[:1200],
            'error': str(result.get('error', ''))[:1200], **extra}


# --------------------------------------------------------------------------
# Individual backends. Each returns a plain result dict; ``infer_backend``
# is the single dispatch seam used by ``run`` (and patched by tests).
# --------------------------------------------------------------------------

def infer_openkeyscan(audio, cache_dir):
    from studio_key import analyze_key, valid_result
    try:
        result = analyze_key(Path(audio), cache_dir=Path(cache_dir))
    except djmix.MixError as error:
        message = str(error)
        status = ('unavailable' if any(word in message for word in
                    ('not installed', 'missing or damaged', 'incomplete'))
                  else 'failed')
        return {'status': status, 'key': None, 'candidates': [],
                'reason' if status == 'unavailable' else 'error': message}
    if not valid_result(result):
        raise ValueError('Production key detector returned an invalid estimate.')
    status = 'estimated' if result['state'] == 'estimated' else 'uncertain'
    candidates = [{'key': c['key'], 'score': float(c['score'])}
                  for c in result['candidates']]
    return {'status': status, 'key': candidates[0]['key'] if candidates else None,
            'candidates': candidates, 'reason': result.get('reason', '')}


def infer_pitch_profile(audio):
    """HPSS/CQT pitch-profile baseline equivalent to ``analyze_audio``."""
    import librosa
    from analyze_audio import RATE, key_candidates
    decoded = djmix.ffmpeg('-i', str(audio), '-map', '0:a:0', '-ar', str(RATE),
                           '-ac', '1', '-f', 'f32le', '-').stdout
    y = np.frombuffer(decoded, dtype='<f4').copy()
    if not len(y) or not np.isfinite(y).all():
        raise ValueError('Decoded audio is empty or non-finite.')
    if np.max(np.abs(y)) < 1e-5:
        return {'status': 'uncertain', 'key': None, 'candidates': [],
                'reason': 'Effectively silent excerpt; no key estimate.'}
    spectrum = librosa.stft(y, n_fft=2048, hop_length=128)
    harmonic, _percussive = librosa.decompose.hpss(spectrum, margin=(3, 3))
    harmonic_y = librosa.istft(harmonic, hop_length=128, length=len(y))
    chroma = librosa.feature.chroma_cqt(y=harmonic_y, sr=RATE, hop_length=256,
                                        n_octaves=6)
    scored = key_candidates(chroma)
    candidates = []
    for entry in scored:
        correlation = float(entry['correlation'])
        if not math.isfinite(correlation):
            raise ValueError('Non-finite pitch-profile correlation.')
        candidates.append({'key': entry['key'], 'score': correlation})
    if not candidates:
        return {'status': 'uncertain', 'key': None, 'candidates': [],
                'reason': 'Flat chroma; no key estimate.'}
    return {'status': 'estimated', 'key': candidates[0]['key'],
            'candidates': candidates,
            'reason': 'Pitch-profile correlations are not probabilities.'}


def _keyfinder_helper(project_root):
    root = Path(project_root) / 'outputs' / 'keyfinder-research'
    names = ('keyfinder-prototype', 'keyfinder_helper', 'prototype_keyfinder', 'helper')
    helper = next((root / name for name in names if (root / name).is_file()), None)
    if helper is None:
        return None, f'libKeyFinder helper is missing under {root}.'
    identity = {'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}
    source = ROOT / 'prototype_keyfinder.cpp'
    if source.is_file():
        identity['helper_source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    for manifest_name in ('manifest.json', 'source.json', 'sources.json'):
        manifest_path = root / manifest_name
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except ValueError as error:
            return None, f'libKeyFinder manifest is damaged: {error}'
        if isinstance(manifest, list):
            archives={'libkeyfinder':'keyfinder.tar.gz','fftw':'fftw.tar.gz'}
            for item in manifest:
                filename=next((v for k,v in archives.items() if k in item.get('url','')),None)
                if filename is None or file_hash(root/filename)!=item.get('sha256'):
                    return None, 'libKeyFinder source archive differs from its recorded download.'
            identity['source_archives_verified']=True
            identity['manifest']=manifest_name
            break
        for field in ('helper_sha256', 'helper_source_sha256'):
            expected = manifest.get(field)
            if expected is not None and expected != identity.get(field):
                return None, f'libKeyFinder {field} differs from its source manifest.'
        identity['manifest'] = manifest_name
        break
    return (helper, identity), ''


def infer_libkeyfinder(audio, project_root):
    found, problem = _keyfinder_helper(project_root)
    if found is None:
        return {'status': 'unavailable', 'key': None, 'candidates': [], 'reason': problem}
    helper, identity = found
    decoded = djmix.ffmpeg('-i', str(audio), '-map', '0:a:0', '-ar', '44100',
                           '-ac', '1', '-f', 'f32le', '-').stdout
    result = subprocess.run([str(helper)], input=decoded, capture_output=True,
                            timeout=KEYFINDER_TIMEOUT)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors='replace')[-1000:]
                         or 'libKeyFinder helper failed.')
    estimate = json.loads(result.stdout)
    if estimate.get('frames') != len(decoded) // 4:
        raise ValueError('Helper did not consume the complete excerpt.')
    key = djmix.key_name(djmix.parse_key(estimate['key'])) if estimate.get('key') else None
    if key is None:
        return {'status': 'abstained', 'key': None, 'candidates': [],
                'reason': 'libKeyFinder reported no key.', **identity}
    return {'status': 'estimated', 'key': key,
            'candidates': [{'key': key, 'score': None}], **identity}


def infer_essentia(audio, profile, research_root):
    """Runs only inside the --infer subprocess (Essentia on PYTHONPATH)."""
    import importlib.metadata
    research_root = Path(research_root)
    try:
        distribution = json.loads((research_root / 'source.json').read_text())
        wheel = research_root / distribution['wheel']
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != distribution['sha256']:
            raise ValueError('The downloaded Essentia wheel differs from its recorded checksum.')
        import essentia  # noqa: F401
        from essentia.standard import KeyExtractor
        from prototype_essentia_key import VERSION
        if importlib.metadata.version('essentia') != VERSION:
            raise ValueError('Use the pinned Essentia package for this comparison.')
    except (ImportError, FileNotFoundError, KeyError, ValueError) as error:
        return {'status': 'unavailable', 'key': None, 'candidates': [],
                'reason': f'Essentia runtime unavailable: {error}'}
    decoded = djmix.ffmpeg('-i', str(audio), '-map', '0:a:0', '-ar', '44100',
                           '-ac', '1', '-f', 'f32le', '-').stdout
    samples = np.frombuffer(decoded, dtype='<f4').copy()
    if not len(samples) or not np.isfinite(samples).all():
        raise ValueError('Decoded audio is empty or non-finite.')
    if np.max(np.abs(samples)) < 1e-5:
        return {'status': 'abstained', 'key': None, 'candidates': [],
                'reason': 'Effectively silent excerpt.'}
    cache_key = (str(research_root.resolve()), profile)
    if cache_key not in _ESSENTIA_EXTRACTORS:
        _ESSENTIA_EXTRACTORS[cache_key] = KeyExtractor(sampleRate=44100, profileType=profile)
    tonic, mode, strength = _ESSENTIA_EXTRACTORS[cache_key](samples)
    if not math.isfinite(float(strength)):
        raise ValueError('Non-finite key strength.')
    key = djmix.key_name(djmix.parse_key(f'{tonic} {mode}'))
    return {'status': 'estimated', 'key': key, 'strength': float(strength),
            'candidates': [{'key': key, 'score': float(strength)}],
            'identity': {'version': VERSION, 'wheel_sha256': distribution['sha256'],
                         'profile': profile, 'sample_rate': 44100}}


def infer_madmom(audio, research_root):
    """Runs only inside the --infer subprocess (madmom on PYTHONPATH)."""
    research_root = Path(research_root)
    try:
        from prototype_madmom_key import model_identity
        model, _identity = model_identity(research_root.resolve())
        from madmom.audio.signal import Signal
        from madmom.features.key import CNNKeyRecognitionProcessor, KEY_LABELS
    except (ImportError, FileNotFoundError, KeyError, ValueError) as error:
        return {'status': 'unavailable', 'key': None, 'candidates': [],
                'reason': f'madmom runtime unavailable: {error}'}
    decoded = djmix.ffmpeg('-i', str(audio), '-map', '0:a:0', '-ar', '44100',
                           '-ac', '1', '-f', 'f32le', '-').stdout
    samples = np.frombuffer(decoded, dtype='<f4').copy()
    if not len(samples) or not np.isfinite(samples).all():
        raise ValueError('Decoded audio is empty or non-finite.')
    if np.max(np.abs(samples)) < 1e-5:
        return {'status': 'abstained', 'key': None, 'candidates': [],
                'reason': 'Effectively silent excerpt.'}
    processor = CNNKeyRecognitionProcessor(nn_files=[str(model)])
    prediction = np.asarray(processor(Signal(samples, sample_rate=44100)))
    if (prediction.shape != (1, 24) or not np.isfinite(prediction).all()
            or np.min(prediction) < 0 or not np.isclose(prediction.sum(), 1., atol=1e-5)):
        raise ValueError('Invalid key model output.')
    labels = [djmix.key_name(djmix.parse_key(label)) for label in KEY_LABELS]
    scores = prediction[0]
    candidates = [{'key': labels[i], 'score': float(scores[i])}
                  for i in np.argsort(-scores)]
    return {'status': 'estimated', 'key': candidates[0]['key'], 'candidates': candidates,
            'identity': _identity}


def _infer_subprocess(backend, audio, project_root, scratch):
    """Run one isolated backend through this script's --infer mode."""
    python = _research_python(project_root)
    packages = _research_root(project_root, backend) / 'packages'
    if python is None:
        return {'status': 'unavailable', 'key': None, 'candidates': [],
                'reason': f'Research Python is missing: {Path(project_root) / ".venv"}.'}
    if not packages.is_dir():
        return {'status': 'unavailable', 'key': None, 'candidates': [],
                'reason': f'Research packages are missing: {packages}.'}
    scratch = Path(scratch); scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', dir=scratch,
                                     delete=False) as handle:
        output = Path(handle.name)
    # Build a fresh environment; the parent process environment is never mutated.
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(packages)
    environment['DETECTOR_EXPERIMENT_PROJECT_ROOT'] = str(project_root)
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        environment[name] = '1'
    try:
        completed = subprocess.run(
            [str(python), str(Path(__file__).resolve()), '--infer', backend,
             str(audio), str(output)],
            capture_output=True, timeout=SUBPROCESS_TIMEOUT, env=environment)
        if completed.returncode or not output.is_file():
            detail = completed.stderr.decode(errors='replace')[-1000:]
            raise ValueError(detail or f'Isolated {backend} inference failed.')
        return _validate_result(json.loads(output.read_text()))
    finally:
        output.unlink(missing_ok=True)


def infer_backend(backend, audio, *, project_root, cache_dir, scratch=None):
    """Dispatch one backend on one complete excerpt; the run() patch seam."""
    audio = Path(audio)
    if backend == 'openkeyscan':
        return _validate_result(infer_openkeyscan(audio, cache_dir))
    if backend == 'pitch_profile':
        return _validate_result(infer_pitch_profile(audio))
    if backend == 'libkeyfinder':
        return _validate_result(infer_libkeyfinder(audio, project_root))
    if backend in RESEARCH_PACKAGES:
        # This CLI is already a dedicated headless research process. Reusing
        # Essentia's import avoids its costly algorithm-registry initialization
        # on every passage; parameters and audio remain identical.
        if backend.startswith('essentia_') and os.environ.get('DJ_EXPERIMENT_SHARED_ESSENTIA') == '1':
            return _validate_result(infer_essentia(audio, backend.removeprefix('essentia_'),
                                                 _research_root(project_root, backend)))
        return _infer_subprocess(backend, audio, project_root,
                                 scratch or Path(cache_dir).parent / 'scratch')
    raise ValueError(f'Unknown key backend: {backend}')


def _infer_main(backend, audio, output):
    """Body of the --infer subprocess mode; writes one JSON result file."""
    project_root = Path(os.environ.get('DETECTOR_EXPERIMENT_PROJECT_ROOT') or ROOT)
    try:
        if backend in ('essentia_edma', 'essentia_bgate'):
            result = infer_essentia(audio, backend.removeprefix('essentia_'),
                                    _research_root(project_root, backend))
        elif backend == 'madmom':
            result = infer_madmom(audio, _research_root(project_root, backend))
        elif backend == 'libkeyfinder':
            result = infer_libkeyfinder(audio, project_root)
        elif backend == 'pitch_profile':
            result = infer_pitch_profile(audio)
        elif backend == 'openkeyscan':
            result = infer_openkeyscan(audio, Path(tempfile.gettempdir()) / 'key-infer-cache')
        else:
            raise ValueError(f'Unknown key backend: {backend}')
        result = _validate_result(result)
    except Exception as error:  # The parent turns this file into a failed row.
        result = {'status': 'failed', 'key': None, 'candidates': [],
                  'error': f'{type(error).__name__}: {error}'[:1200]}
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, allow_nan=False), encoding='utf-8')
    return 0 if result['status'] != 'failed' else 1


# --------------------------------------------------------------------------
# Scoring. Absolute scores exist only for constructed tonal records;
# baseline-relative consistency is reported separately and proves nothing
# about correctness.
# --------------------------------------------------------------------------

def _composition_key(record):
    return _parse((record.get('expected') or {}).get('key'))


def _expected_tonal_key(record, case):
    base = _composition_key(record)
    if base is None:
        return None
    if case['type'] == 'pitch':
        return djmix.transpose(base, int(case.get('semitones', 0)))
    return base


def _score_row(row, record, case, baseline_row):
    predicted = _parse(row.get('key')) if row['status'] == 'estimated' else None
    row['composition_key'] = (djmix.key_name(_composition_key(record))
                              if _composition_key(record) else None)
    expected = _expected_tonal_key(record, case)
    row['expected_key'] = djmix.key_name(expected) if expected else None
    if expected is None or predicted is None:
        row.update(exact=False if expected is not None else None,
                   composition_exact=False if expected is not None else None,
                   top_three=False if expected is not None else None,
                   root_correct=None, mode_correct=None, same_root_mode_error=False)
    else:
        candidate_keys = [c['key'] for c in row['candidates']]
        row.update(
            exact=predicted == expected,
            composition_exact=predicted == _composition_key(record),
            top_three=djmix.key_name(expected) in candidate_keys[:3],
            root_correct=predicted[0] == expected[0],
            mode_correct=predicted[1] == expected[1],
            same_root_mode_error=(predicted[0] == expected[0]
                                  and predicted[1] != expected[1]))
    # Baseline-relative expectation transposes this backend's own baseline.
    baseline_key = (_parse(baseline_row.get('key'))
                    if baseline_row and baseline_row['status'] == 'estimated' else None)
    if case['type'] == 'baseline':
        row.update(expected_relative_key=row['key'] if baseline_key else None,
                   consistent=None, comparison='baseline', mode_preserved=None)
    elif baseline_key is None:
        row.update(expected_relative_key=None, consistent=None,
                   comparison='unavailable', mode_preserved=None)
    else:
        relative = (djmix.transpose(baseline_key, int(case.get('semitones', 0)))
                    if case['type'] == 'pitch' else baseline_key)
        row.update(expected_relative_key=djmix.key_name(relative),
                   consistent=predicted == relative if predicted else False,
                   comparison=('match' if predicted == relative else 'mismatch')
                   if predicted else 'mismatch',
                   mode_preserved=(predicted[1] == relative[1]) if predicted else None)
    return row


def _summarize(rows, records, cases):
    def counts(selected):
        result = {'attempted': len(selected)}
        for status in VALID_STATUSES:
            result[status] = sum(r['status'] == status for r in selected)
        exact_rows = [r for r in selected if r.get('exact') is not None]
        exact = sum(bool(r['exact']) for r in exact_rows)
        result.update(scored_attempts=len(exact_rows), exact=exact,
                      correct_fraction_of_attempts=(exact / len(exact_rows)
                                                    if exact_rows else None),
                      top_three=sum(bool(r['top_three']) for r in exact_rows),
                      same_root_mode_errors=sum(bool(r['same_root_mode_error'])
                                                for r in exact_rows))
        compared = [r for r in selected if r['comparison'] in ('match', 'mismatch')]
        result.update(consistent=sum(bool(r['consistent']) for r in compared),
                      compared=len(compared),
                      comparison_unavailable=sum(r['comparison'] == 'unavailable'
                                                 for r in selected),
                      mode_preserved=sum(r['mode_preserved'] is True for r in compared),
                      mode_not_preserved=sum(r['mode_preserved'] is False
                                             for r in compared))
        return result

    by_id = {record['id']: record for record in records}
    summary = {
        'note': ('Consistency and agreement do not establish correctness; '
                 'absolute scores cover constructed tonal records only.'),
        'backends': {backend: counts([r for r in rows if r['backend'] == backend])
                     for backend in BACKENDS},
        'per_case': {},
        'tonal_backends': {},
        'paired_regressions': [],
    }
    for case in cases:
        selected = [r for r in rows if r['case_id'] == case['id']]
        summary['per_case'][case['id']] = {
            backend: counts([r for r in selected if r['backend'] == backend])
            for backend in BACKENDS}
    tonal = [r for r in rows if by_id[r['record_id']]['kind'] == 'synthetic_tonal']
    for backend in BACKENDS:
        summary['tonal_backends'][backend] = counts(
            [r for r in tonal if r['backend'] == backend])
    baselines = {(r['record_id'], r['backend']): r for r in rows
                 if r['case_type'] == 'baseline'}
    for row in rows:
        base = baselines.get((row['record_id'], row['backend']))
        if (row['case_type'] == 'baseline' or base is None
                or row.get('exact') is None or base.get('exact') is None):
            continue
        if base['exact'] and not row['exact']:
            summary['paired_regressions'].append(
                {'record_id': row['record_id'], 'case_id': row['case_id'],
                 'backend': row['backend'], 'direction': 'regressed',
                 'baseline_exact': True, 'case_exact': False})
        elif row['exact'] and not base['exact']:
            summary['paired_regressions'].append(
                {'record_id': row['record_id'], 'case_id': row['case_id'],
                 'backend': row['backend'], 'direction': 'improved',
                 'baseline_exact': False, 'case_exact': True})
    return summary


def _validate_saved_rows(report, plan):
    records = {record['id']: record for record in plan['records']}
    cases = {case['id']: case for case in plan['cases']}
    seen = set()
    for row in report.get('rows', []):
        identity = (row.get('record_id'), row.get('case_id'), row.get('backend'))
        record = records.get(row.get('record_id'))
        case = cases.get(row.get('case_id'))
        if (record is None or case is None or row.get('backend') not in BACKENDS
                or 'key' not in case.get('families', [])
                or record['kind'] not in case.get('record_kinds', [])
                or identity in seen):
            raise ValueError('Saved key rows do not match the frozen plan; '
                             'preserve them and use a new output directory.')
        seen.add(identity)
    return seen


@contextmanager
def key_scope(audio, scratch):
    """Give all backends the same first <=60 seconds, including slow cases."""
    info = sf.info(audio)
    if info.frames <= info.samplerate * 60:
        yield audio
        return
    Path(scratch).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='key-scope-', dir=scratch) as folder:
        path = Path(folder) / 'first60.wav'
        samples, rate = sf.read(audio, frames=info.samplerate * 60,
                               dtype='float32', always_2d=True)
        sf.write(path, samples, rate, subtype='FLOAT')
        yield path


def run(plan_path, output_dir, limit=None, progress=print):
    """Run (or resume) the key experiment; returns the completed report."""
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool)
                              or limit < 1):
        raise ValueError('--limit must be a positive integer.')
    plan = load_plan(plan_path)
    project_root = Path(plan['project_root'])
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / 'key-report.json'
    report = resume_report(report_path, plan, 'key', __file__)
    isolation = ('dedicated_headless_process_shared_essentia' if
                 os.environ.get('DJ_EXPERIMENT_SHARED_ESSENTIA') == '1' else 'one_subprocess_per_research_inference')
    if report.get('research_isolation', isolation) != isolation:
        raise ValueError('Research isolation changed; use a new output directory.')
    report['research_isolation'] = isolation
    done = _validate_saved_rows(report, plan)
    report.setdefault('backends', list(BACKENDS))
    report.setdefault('excerpt', 'Identical first <=60 seconds of each case WAV for every backend; '
                                 'each backend applies its own pinned decoding.')
    report.setdefault('limitations', report_header(plan, 'key', __file__)['limitations'])
    records = plan['records'][:limit] if limit is not None else list(plan['records'])
    cases_by_record = {}
    for record in records:
        ordered = applicable_cases(plan, record, 'key')
        cases_by_record[record['id']] = sorted(
            ordered, key=lambda case: case['type'] != 'baseline')
    all_cases = plan['cases']
    scratch = output_dir / 'scratch'
    cache_dir = output_dir / 'cache' / 'keys'

    def save(status='running'):
        report['status'] = status
        report['summary'] = _summarize(report['rows'], plan['records'], all_cases)
        atomic_json(report_path, report)

    save()
    baselines = {(row['record_id'], row['backend']): row for row in report['rows']
                 if row['case_type'] == 'baseline'}
    for record in records:
        check_cancelled()
        try:
            verify_record(record)
        except Exception as error:
            for case in cases_by_record[record['id']]:
                for backend in BACKENDS:
                    identity = (record['id'], case['id'], backend)
                    if identity in done:
                        continue
                    row = {'record_id': record['id'], 'record_kind': record['kind'],
                           'case_id': case['id'], 'case_type': case['type'],
                           'backend': backend, 'status': 'failed', 'key': None,
                           'candidates': [], 'reason': '',
                           'error': f'{type(error).__name__}: {error}'[:1200],
                           'audio_sha256': None, 'duration_seconds': None, 'seconds': 0.0}
                    _score_row(row, record, case, baselines.get((record['id'], backend)))
                    report['rows'].append(row); done.add(identity); save()
                    progress(f"key {record['id']} {case['id']} {backend} failed")
            continue
        for case in cases_by_record[record['id']]:
            pending = [b for b in BACKENDS if (record['id'], case['id'], b) not in done]
            if not pending:
                continue
            with case_audio(record, case, scratch) as transformed, key_scope(transformed, scratch) as audio:
                # Provenance is captured before the temporary transform disappears.
                audio_hash = file_hash(audio)
                info = sf.info(audio)
                for backend in pending:
                    identity = (record['id'], case['id'], backend)
                    check_cancelled()
                    started = time.monotonic()
                    try:
                        result = infer_backend(backend, audio,
                                               project_root=project_root,
                                               cache_dir=cache_dir, scratch=scratch)
                    except subprocess.TimeoutExpired:
                        result = {'status': 'failed', 'key': None, 'candidates': [],
                                  'error': f'TimeoutExpired: exceeded bounded timeout'}
                    except Exception as error:
                        result = {'status': 'failed', 'key': None, 'candidates': [],
                                  'error': f'{type(error).__name__}: {error}'[:1200]}
                    row = {'record_id': record['id'], 'record_kind': record['kind'],
                           'case_id': case['id'], 'case_type': case['type'],
                           'backend': backend,
                           'audio_sha256': audio_hash,
                           'case_duration_seconds': float(sf.info(transformed).duration),
                           'duration_seconds': float(info.duration),
                           'seconds': round(time.monotonic() - started, 3), **result}
                    _score_row(row, record, case, baselines.get((record['id'], backend)))
                    if case['type'] == 'baseline':
                        baselines[(record['id'], backend)] = row
                    report['rows'].append(row); done.add(identity); save()
                    progress(f"key {record['id']} {case['id']} {backend} {row['status']}")
    save(status='finished')
    return report


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path, nargs='?', help='Frozen experiment plan JSON.')
    parser.add_argument('--out', type=Path, help='Output directory for key-report.json.')
    parser.add_argument('--limit', type=int, help='Only the first N plan records.')
    parser.add_argument('--infer', nargs=3, metavar=('BACKEND', 'AUDIO', 'OUTPUT'),
                        help='Isolated single-backend inference (research subprocess).')
    args = parser.parse_args(arguments)
    if args.infer:
        backend, audio, output = args.infer
        return _infer_main(backend, Path(audio), Path(output))
    if args.plan is None or args.out is None:
        parser.error('plan and --out are required unless --infer is used.')
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive.')
    report = run(args.plan, args.out, limit=args.limit)
    print(json.dumps({'report': str(args.out / 'key-report.json'),
                      'status': report['status'], 'summary': report['summary']},
                     allow_nan=False))
    return int(any(counts['failed'] for counts in report['summary']['backends'].values()))


if __name__ == '__main__':
    raise SystemExit(main())
