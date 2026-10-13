"""An isolated, resumable development collection; predictions are never references.

prepare freezes source identities and deterministic listening tasks before inference.
analyze calls the unchanged production detectors, recording failures and abstentions.
Human references and prediction exposure are saved separately by the native reviewer.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import signal
import tempfile
import threading

import numpy as np
import soundfile as sf

import djmix
from studio_cache import digest, source_signature
from studio_process import cancel_on, check_cancelled

VERSION = 1
RATE = 48000
PEAK_RATE = 100
CHOICES = ('not_reviewed', 'yes', 'no', 'unclear')
KEY_CHOICES = ('not_reviewed', 'unclear', 'no_key', 'not_qualified') + tuple(
    djmix.key_name((root, mode)) for mode in ('major', 'minor') for root in range(12))
TIMING_CHOICES = ('not_reviewed', 'annotated', 'beatless', 'unclear')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            check_cancelled(); h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.pending-', suffix='.json', delete=False) as stream:
        pending = Path(stream.name)
        try:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
            stream.close(); os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)


def child(pilot_file, relative):
    root = Path(pilot_file).resolve().parent
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise ValueError('Pilot artifact must stay inside its output folder.')
    return result


def detector_identity():
    from studio_key import MODEL_ID
    from studio_structure import MODEL_SHA256 as BEAT_HASH
    from studio_content import MODEL_SHA256 as CONTENT_HASH
    names = ('studio_key.py', 'studio_key_worker.py', 'studio_key_model.py',
             'studio_structure.py', 'studio_structure_features.py', 'studio_beat_tempo.py',
             'studio_content.py', 'studio_content_model.py', 'studio_overlap_harmony.py',
             'analyze_audio.py', 'detector_pilot.py')
    root = Path(__file__).resolve().parent
    versions = {}
    for name in ('torch', 'librosa', 'numpy', 'scipy', 'soundfile', 'beat_this'):
        try: versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError: versions[name] = None
    return {'key_model_id': MODEL_ID, 'beat_model_sha256': BEAT_HASH,
            'content_model_sha256': CONTENT_HASH,
            'source_sha256': {name: file_hash(root / name) for name in names},
            'runtime': versions,
            'ffmpeg': djmix.run([djmix.tool_path('ffmpeg'), '-version']).stdout.decode().splitlines()[0]}


def listening_tasks(record):
    duration = record['duration']
    # Whole-window voice observations are on a five-second grid. Leave 150 ms
    # for MP3 container/decoded-length differences; no predictions select tasks.
    last = max(0., math.floor((duration - 10.15) / 5) * 5.)
    middle = math.floor(last / 10) * 5.
    key_end = min(60., duration - .15)
    result = [{'id': record['id'] + '-source-key', 'record_id': record['id'],
               'kind': 'source_key', 'name': 'First-minute key', 'start': 0., 'end': key_end}]
    for name, start in (('Beginning', 0.), ('Middle', middle), ('Near ending', last)):
        result.append({'id': record['id'] + '-' + name.lower().replace(' ', '-'),
                       'record_id': record['id'], 'kind': 'passage', 'name': name,
                       'start': start, 'end': start + 10.})
    return result


def prepare(dataset, output):
    dataset, output = Path(dataset).expanduser().resolve(), Path(output).resolve()
    if not dataset.is_dir(): raise ValueError('Choose an existing audio folder.')
    pilot_file = output / 'pilot.json'
    if pilot_file.exists():
        pilot = load_pilot(pilot_file)
        if pilot['dataset'] != str(dataset): raise ValueError('This pilot belongs to another folder.')
        expected = {r['source'][0] for r in pilot['records']}
        actual = {str(p.resolve()) for p in dataset.rglob('*')
                  if p.is_file() and p.suffix.lower() in ('.mp3', '.wav', '.flac', '.m4a', '.aiff', '.ogg')}
        if expected != actual: raise ValueError('The collection changed; use a new output folder.')
        for r in pilot['records']: verify_source(r, full=True)
        return pilot_file
    paths = sorted(p.resolve() for p in dataset.rglob('*') if p.is_file()
                   and p.suffix.lower() in ('.mp3', '.wav', '.flac', '.m4a', '.aiff', '.ogg'))
    if not paths: raise ValueError('No supported audio files found.')
    records = []
    for path in paths:
        signature = source_signature(path); info = djmix.probe(path)
        if info['duration'] < 20:
            raise ValueError(f'{path.name} is too short for this full-song pilot.')
        checksum = file_hash(path)
        if source_signature(path) != signature: raise ValueError('A source changed while preparing the pilot.')
        identity = digest([str(path), checksum])[:16]
        records.append({'id': identity, 'name': path.stem.replace('mixkit-', '').replace('-', ' '),
                        'source': signature, 'source_sha256': checksum, **{k: info[k] for k in ('duration', 'sample_rate', 'channels')},
                        'audio': f'audio/{identity}.wav', 'waveform': f'waveforms/{identity}.npz',
                        'result': f'results/{identity}.json',
                        'creator_group': 'unknown', 'generated_music': 'unknown'})
    tasks = [task for r in records for task in listening_tasks(r)]
    plan = {'version': VERSION, 'dataset': str(dataset), 'records': records, 'tasks': tasks,
            'detectors': detector_identity(),
            'purpose': 'development_pilot_only', 'ui_promotion_allowed': False,
            'reference_status': 'human_labels_required',
            'protocol': {'selection': 'All supplied audio; fixed positions chosen before inference.',
                         'voice_target': 'Audible singing, speech or vocal samples anywhere in the entire ten-second window.',
                         'key_target': 'First minute for source key; both equal five-second halves for local key.',
                         'timing_target': 'Human-marked audible beats and bar starts, with completeness declared separately.',
                         'limitations': 'No independent held-out split, creator independence, generated-EDM coverage or actual blend-quality validation is established.'}}
    atomic_json(pilot_file, {**plan, 'created_utc': utc_now(), 'plan_sha256': digest(plan)})
    return pilot_file


def load_pilot(pilot_file):
    pilot = json.loads(Path(pilot_file).read_text(encoding='utf-8'))
    plan = {k: v for k, v in pilot.items() if k not in ('created_utc', 'plan_sha256')}
    if pilot.get('version') != VERSION or digest(plan) != pilot.get('plan_sha256'):
        raise ValueError('Pilot plan is damaged or changed. References cannot be mixed with another plan.')
    return pilot


def verify_source(record, full=False):
    path = Path(record['source'][0])
    if source_signature(path) != record['source'] or (full and file_hash(path) != record['source_sha256']):
        raise ValueError(f'{path.name} changed since the pilot was prepared. Use a new pilot folder.')


def task_for_id(pilot, task_id):
    return next(t for t in pilot['tasks'] if t['id'] == task_id)


def record_for_id(pilot, record_id):
    return next(r for r in pilot['records'] if r['id'] == record_id)


def empty_reference():
    return {'voice': 'not_reviewed', 'pitched': 'not_reviewed', 'drums_only': 'not_reviewed',
            'key': 'not_reviewed', 'timing_status': 'not_reviewed', 'timing_complete': False,
            'beats': [], 'notes': ''}


def validate_reference(task, reference):
    if not isinstance(reference, dict) or set(reference) != set(empty_reference()):
        raise ValueError('Invalid reference fields.')
    if any(reference[k] not in CHOICES for k in ('voice', 'pitched', 'drums_only')):
        raise ValueError('Use reviewed, absent, present or uncertain labels.')
    if reference['key'] not in KEY_CHOICES or reference['timing_status'] not in TIMING_CHOICES:
        raise ValueError('Invalid key or timing label.')
    if type(reference['timing_complete']) is not bool or not isinstance(reference['notes'], str) or len(reference['notes']) > 10000:
        raise ValueError('Invalid completeness declaration or notes.')
    beats = reference['beats']
    if not isinstance(beats, list) or len(beats) > 1500: raise ValueError('Invalid beat markers.')
    previous = -1
    for beat in beats:
        if not isinstance(beat, dict) or set(beat) != {'time', 'downbeat'}: raise ValueError('Invalid beat marker.')
        t = beat['time']
        if (type(t) not in (int, float) or not math.isfinite(t) or not task['start'] <= t < task['end']
                or t <= previous or type(beat['downbeat']) is not bool):
            raise ValueError('Beat markers must be ordered and inside this listening task.')
        previous = t
    if beats and reference['timing_status'] != 'annotated':
        raise ValueError('Choose Marked beats when saving beat markers.')
    if reference['timing_status'] == 'annotated' and not beats:
        raise ValueError('Add beat markers, or choose an uncertain/unreviewed rhythm.')
    if reference['timing_complete'] and reference['timing_status'] not in ('annotated', 'beatless'):
        raise ValueError('Incomplete or uncertain timing cannot be treated as a complete reference.')
    if reference['drums_only'] == 'yes' and (reference['voice'] == 'yes' or reference['pitched'] == 'yes'):
        raise ValueError('A drums-only passage cannot also contain audible voices or pitched material.')


def load_reviews(pilot_file):
    pilot = load_pilot(pilot_file); path = Path(pilot_file).parent / 'references.json'
    if not path.exists():
        return {'version': VERSION, 'plan_sha256': pilot['plan_sha256'], 'entries': [], 'exposures': []}
    saved = json.loads(path.read_text(encoding='utf-8'))
    if saved.get('version') != VERSION or saved.get('plan_sha256') != pilot['plan_sha256']:
        raise ValueError('These references belong to another or damaged pilot.')
    if not isinstance(saved.get('entries'), list) or not isinstance(saved.get('exposures'), list):
        raise ValueError('Invalid reference history.')
    for entry in saved['entries']:
        validate_reference(task_for_id(pilot, entry['task_id']), entry['reference'])
    return saved


def latest_reference(pilot_file, task_id, reviewer):
    entries = load_reviews(pilot_file)['entries']
    return next((e for e in reversed(entries) if e['task_id'] == task_id and e['reviewer'] == reviewer), None)


def _reviewer(name):
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError('Enter a short reviewer name or alias.')
    return name.strip()


def mark_predictions_exposed(pilot_file, task_id, reviewer):
    reviewer = _reviewer(reviewer); pilot = load_pilot(pilot_file)
    task = task_for_id(pilot, task_id); saved = load_reviews(pilot_file)
    if not latest_reference(pilot_file, task_id, reviewer):
        raise ValueError('Save your reference answer before revealing predictions.')
    if not any(e['record_id'] == task['record_id'] and e['reviewer'] == reviewer for e in saved['exposures']):
        saved['exposures'].append({'record_id': task['record_id'], 'reviewer': reviewer, 'utc': utc_now()})
        atomic_json(Path(pilot_file).parent / 'references.json', saved)


def save_reference(pilot_file, task_id, reviewer, reference):
    reviewer = _reviewer(reviewer); pilot = load_pilot(pilot_file)
    task = task_for_id(pilot, task_id); record = record_for_id(pilot, task['record_id'])
    verify_source(record, full=True); validate_reference(task, reference)
    saved = load_reviews(pilot_file)
    exposed = any(e['record_id'] == record['id'] and e['reviewer'] == reviewer for e in saved['exposures'])
    entry = {'task_id': task_id, 'reviewer': reviewer, 'reference': reference,
             'source_sha256': record['source_sha256'], 'utc': utc_now(),
             'prediction_exposed_in_tool': exposed,
             'scope': 'provisional_human_review_not_adjudicated_ground_truth'}
    saved['entries'].append(entry)
    atomic_json(Path(pilot_file).parent / 'references.json', saved)
    return entry


def load_prediction(pilot_file, record_id):
    pilot = load_pilot(pilot_file); record = record_for_id(pilot, record_id)
    path = child(pilot_file, record['result'])
    if not path.exists(): return {}
    result = json.loads(path.read_text(encoding='utf-8'))
    if result.get('plan_sha256') != pilot['plan_sha256'] or result.get('source_sha256') != record['source_sha256']:
        raise ValueError('Predictions belong to a different source or pilot.')
    return result


def load_waveform(pilot_file, record, task):
    with np.load(child(pilot_file, record['waveform']), allow_pickle=False) as saved:
        if str(saved['source_sha256'].item()) != record['source_sha256']:
            raise ValueError('Waveform belongs to another source.')
        peaks = saved['peaks']
        return peaks[math.floor(task['start'] * PEAK_RATE):math.ceil(task['end'] * PEAK_RATE)].copy()


def _decode(pilot_file, record, progress):
    from studio_storage import require_space
    audio = child(pilot_file, record['audio']); audio.parent.mkdir(parents=True, exist_ok=True)
    if audio.exists():
        info = sf.info(audio)
        if info.samplerate == RATE and info.channels == 2 and abs(info.duration - record['duration']) < .15:
            progress('Reusing decoded source audio'); return info
        raise ValueError('Cached source audio is invalid. Preserve references and repair the pilot cache.')
    require_space(audio.parent, math.ceil(record['duration'] * RATE * 4) + 65536, 'prepare pilot listening audio')
    with tempfile.TemporaryDirectory(dir=audio.parent, prefix='.pending-audio-') as temporary:
        pending = Path(temporary) / 'source.wav'
        progress('Preparing original audio for native listening and precise markers')
        djmix.ffmpeg('-i', record['source'][0], '-map', '0:a:0', '-ar', str(RATE), '-ac', '2',
                     '-c:a', 'pcm_s16le', str(pending))
        info = sf.info(pending)
        if abs(info.duration - record['duration']) > .15 or any(t['end'] > info.duration for t in listening_tasks(record)):
            raise ValueError('Decoded audio does not cover the frozen listening tasks.')
        verify_source(record); os.replace(pending, audio)
        return info


def _build_waveform(pilot_file, record, progress):
    destination = child(pilot_file, record['waveform'])
    if destination.exists():
        for task in listening_tasks(record): load_waveform(pilot_file, record, task)
        progress('Reusing real source waveform'); return
    destination.parent.mkdir(parents=True, exist_ok=True)
    step = RATE // PEAK_RATE; pieces = []
    with sf.SoundFile(child(pilot_file, record['audio'])) as stream:
        while True:
            check_cancelled(); block = stream.read(RATE * 10, dtype='float32', always_2d=True)
            if not len(block): break
            mono = np.max(np.abs(block), axis=1)
            mono = np.pad(mono, (0, (-len(mono)) % step))
            pieces.append(mono.reshape(-1, step).max(axis=1))
    peaks = np.concatenate(pieces)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.pending-peaks-', suffix='.npz', delete=False) as stream:
        pending = Path(stream.name)
        try:
            np.savez_compressed(stream, peaks=peaks, source_sha256=record['source_sha256'])
            stream.close(); os.replace(pending, destination)
        finally: pending.unlink(missing_ok=True)


def _beat_predictions(record, cache, progress):
    from studio_structure import analyze_tempo, analyze_song, MODEL_SHA256
    tempo = analyze_tempo(Path(record['source'][0]), record['duration'], cache, progress)
    raw_path = cache / (digest([1, MODEL_SHA256, record['source']]) + '.npz')
    with np.load(raw_path, allow_pickle=False) as saved:
        raw = {name: saved[name].tolist() for name in ('beats', 'downbeats', 'beat_strength', 'downbeat_strength')}
    grid = analyze_song(Path(record['source'][0]), tempo['bpm'], record['duration'], cache, progress) if tempo['bpm'] else {}
    return {'tempo': tempo, 'raw_events': raw, 'grid': grid,
            'score_meaning': 'Raw peak strengths and coherence checks are not correctness probabilities.'}


def _local_keys(pilot_file, record, progress):
    import librosa
    from studio_overlap_harmony import tonal_evidence, summarize_side, RATE as KEY_RATE
    result = {}
    with sf.SoundFile(child(pilot_file, record['audio'])) as stream:
        for task in listening_tasks(record):
            if task['kind'] != 'passage': continue
            progress('Checking local notes: ' + task['name']); parts = []
            boundaries = np.rint(np.linspace(task['start'] * RATE, task['end'] * RATE, 3)).astype(int)
            for a, b in zip(boundaries, boundaries[1:]):
                check_cancelled(); stream.seek(int(a))
                audio = stream.read(int(b - a), dtype='float32', always_2d=True)
                audio = librosa.resample(audio.T, orig_sr=RATE, target_sr=KEY_RATE).T
                part = tonal_evidence(audio)
                parts.append({**part, 'start': float(a / RATE - task['start']), 'end': float(b / RATE - task['start'])})
            result[task['id']] = summarize_side(parts)
    return result


def analyze(pilot_file, limit=None, progress=print):
    from studio_key import analyze_key
    from studio_content import analyze_content
    pilot_file = Path(pilot_file).resolve(); pilot = load_pilot(pilot_file)
    if detector_identity() != pilot['detectors']:
        raise ValueError('Detector code/runtime changed after plan freeze. Use a new pilot output folder.')
    records = pilot['records'][:limit] if limit is not None else pilot['records']
    root = pilot_file.parent; completed = 0
    for number, record in enumerate(records, 1):
        check_cancelled(); verify_source(record, full=True)
        log = lambda message: progress(f'[{number}/{len(records)}] {record["name"]}: {message}')
        result = load_prediction(pilot_file, record['id']) or {
            'version': VERSION, 'plan_sha256': pilot['plan_sha256'],
            'source_sha256': record['source_sha256'], 'source': record['source'], 'stages': {}}
        jobs = (
            ('listening_audio', lambda: {'duration': _decode(pilot_file, record, log).duration}),
            ('waveform', lambda: (_build_waveform(pilot_file, record, log), {'rate': PEAK_RATE})[1]),
            ('source_key', lambda: analyze_key(Path(record['source'][0]), root / 'cache/keys', log)),
            ('beats', lambda: _beat_predictions(record, root / 'cache/beats', log)),
            ('content', lambda: analyze_content(record['source'][0], record['duration'], root / 'cache/content', log)),
            ('local_keys', lambda: _local_keys(pilot_file, record, log)))
        for stage, job in jobs:
            check_cancelled()
            if result['stages'].get(stage, {}).get('status') == 'completed':
                log('Reusing unchanged ' + stage.replace('_', ' ')); continue
            try:
                value = job(); verify_source(record)
                result['stages'][stage] = {'status': 'completed', 'result': value}
            except Exception as error:
                if str(error) == 'Operation cancelled.': raise
                result['stages'][stage] = {'status': 'failed', 'error': str(error)[:1800]}
                log('FAILED ' + stage + ': ' + str(error)[:300])
            result['updated_utc'] = utc_now()
            atomic_json(child(pilot_file, record['result']), result)
        completed += int(all(result['stages'].get(s, {}).get('status') == 'completed' for s, _ in jobs))
        atomic_json(root / 'summary.json', summarize(pilot_file))
    return completed


def summarize(pilot_file):
    pilot = load_pilot(pilot_file); reviews = load_reviews(pilot_file)
    results = [load_prediction(pilot_file, r['id']) for r in pilot['records']]
    done = [r for r in results if len(r.get('stages', {})) == 6 and all(s['status'] == 'completed' for s in r['stages'].values())]
    failures = [{'record_id': record['id'], 'name': record['name'], 'stage': stage, 'error': value['error']}
                for record, result in zip(pilot['records'], results)
                for stage, value in result.get('stages', {}).items() if value['status'] == 'failed']
    latest = {(e['reviewer'], e['task_id']): e for e in reviews['entries']}
    reviewed = [e for e in latest.values() if any(e['reference'][f] != 'not_reviewed' for f in ('voice', 'pitched', 'drums_only', 'key', 'timing_status'))]
    return {'status': 'predictions_ready_human_references_pending' if len(done) == len(results) else 'partial',
            'tracks': len(results), 'complete_predictions': len(done), 'failed_stages': failures,
            'listening_tasks': len(pilot['tasks']), 'reviewed_answers': len(reviewed),
            'distinct_reviewers': len({e['reviewer'] for e in reviewed}),
            'distinct_audio_files': len({r['source_sha256'] for r in pilot['records']}),
            'creator_groups': 'unknown', 'generated_edm_coverage': 'unknown',
            'source_minutes': sum(r['duration'] for r in pilot['records']) / 60,
            'source_bytes': sum(r['source'][1] for r in pilot['records']),
            'pilot_only': True, 'ui_promotion_allowed': False, 'accuracy': None,
            'note': 'Model agreement, missing labels and incomplete beat annotations are not ground truth. No detector or confidence map is installed.'}


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare_parser = sub.add_parser('prepare'); prepare_parser.add_argument('dataset', type=Path)
    prepare_parser.add_argument('--out', type=Path, required=True)
    analyze_parser = sub.add_parser('analyze'); analyze_parser.add_argument('pilot', type=Path)
    analyze_parser.add_argument('--limit', type=int)
    summary_parser = sub.add_parser('summary'); summary_parser.add_argument('pilot', type=Path)
    args = parser.parse_args(arguments); cancelled = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: cancelled.set())
    try:
        with cancel_on(cancelled):
            if args.command == 'prepare': print(prepare(args.dataset, args.out), flush=True)
            elif args.command == 'summary': print(json.dumps(summarize(args.pilot), indent=2), flush=True)
            else:
                if args.limit is not None and args.limit < 1: parser.error('--limit must be positive')
                analyze(args.pilot, args.limit, lambda m: print(m, flush=True))
                summary = summarize(args.pilot); print(json.dumps(summary, indent=2), flush=True)
                return 1 if summary['failed_stages'] else 0
        return 0
    except Exception as error:
        print(str(error), flush=True); return 130 if str(error) == 'Operation cancelled.' else 1


if __name__ == '__main__':
    raise SystemExit(main())
