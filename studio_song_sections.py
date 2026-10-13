"""Source-bound musical-function observations; independent of BPM and audio edits."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile

import djmix
from studio_cache import source_signature
from studio_process import check_cancelled, _stop
from studio_storage import managed_cache, require_space

VERSION = 1
ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / 'section-model-manifest.json').read_text())
MODEL_ID = hashlib.sha256(json.dumps(MANIFEST, sort_keys=True).encode()).hexdigest()
LABELS = {'intro': 'Intro', 'buildup': 'Build', 'drop': 'Drop',
          'breakdown': 'Breakdown', 'outro': 'Outro', 'silence': 'Quiet section'}
MAX_DURATION = 1800


def model_directory():
    from studio_model_store import directory
    return directory('sections')


def model_available():
    from studio_model_store import available
    return available('sections')


def verify_models(progress=lambda message: None):
    for index, (name, expected) in enumerate(MANIFEST.items(), 1):
        file = model_directory() / name
        if not file.is_file() or file.stat().st_size != expected['size']:
            from studio_model_store import missing_message
            raise djmix.MixError(missing_message('sections'))
        progress(f'Checking section-analysis files ({index}/{len(MANIFEST)})')
        digest = hashlib.sha256()
        with file.open('rb') as handle:
            while chunk := handle.read(4 * 1024 * 1024):
                check_cancelled(); digest.update(chunk)
        if digest.hexdigest() != expected['sha256']:
            from studio_model_store import missing_message
            raise djmix.MixError(missing_message('sections'))


def normalize_regions(sections, duration):
    """Merge only touching equal labels. Never invent a missing tail or gap."""
    if not isinstance(sections, list) or not 1 <= len(sections) <= 2048:
        raise ValueError('Invalid section count.')
    regions = []
    for item in sections:
        if not isinstance(item, dict) or item.get('label') not in LABELS:
            raise ValueError('Invalid musical section.')
        start, end = item.get('start'), item.get('end')
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start < end <= duration + .15
                or (regions and start < regions[-1]['end'] - 1e-6)):
            raise ValueError('Invalid section boundaries.')
        end = min(end, duration)
        if start >= end: raise ValueError('Section is outside the source.')
        if regions and regions[-1]['label'] == item['label'] and abs(start - regions[-1]['end']) < 1e-6:
            regions[-1]['end'] = end
        else: regions.append(dict(label=item['label'], start=start, end=end))
    return regions


def valid_result(result, duration):
    try:
        if (not isinstance(result, dict) or result.get('version') != VERSION
                or result.get('model_id') != MODEL_ID or result.get('duration') != duration
                or not 2 <= duration <= MAX_DURATION): return False
        return normalize_regions(result['regions'], duration) == result['regions']
    except (KeyError, TypeError, ValueError): return False


def current_sections(track):
    result = getattr(track, 'song_sections', {})
    try:
        return result if valid_result(result, track.duration) and result.get('source') == source_signature(track.path) else {}
    except (OSError, ValueError): return {}


def _run_worker(source, work, progress):
    script = ROOT / 'studio_section_worker.py'
    command = ([sys.executable, '--section-worker'] if getattr(sys, 'frozen', False)
               else [sys.executable, str(script)]) + [str(source), str(work)]
    environment = os.environ.copy()
    environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {'start_new_session': True}
    # Worker output is a bounded JSON file, also available in windowed Windows builds
    # where Python's stdout may be None. Cancellation always reaps the child.
    previous = None
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment, **options) as process:
        try:
            while True:
                check_cancelled()
                try:
                    file = work / 'progress.json'
                    if file.stat().st_size <= 4096:
                        value = json.loads(file.read_text())
                        if isinstance(value, str) and value != previous:
                            progress(value); previous = value
                except (OSError, ValueError): pass
                try:
                    code = process.wait(timeout=.1)
                    break
                except subprocess.TimeoutExpired: pass
            check_cancelled()
            file = work / 'result.json'
            if not file.exists() or file.stat().st_size > 1024 * 1024:
                raise djmix.MixError('Section analysis stopped before finishing. Close other memory-heavy apps and try again; your mix has not changed.')
            result = json.loads(file.read_text())
            if code or 'error' in result:
                raise djmix.MixError('Section analysis could not finish: ' + str(result.get('error', 'worker stopped'))[:1200])
            return result
        finally:
            if process.poll() is None:
                # Stop the decoder too if cancellation lands during preparation.
                if sys.platform == 'win32':
                    try:
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
                    except (OSError, subprocess.TimeoutExpired): pass
                else:
                    try: os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
            _stop(process)


@managed_cache
def analyze_sections(path, duration, cache_dir, progress=lambda message: None):
    if not math.isfinite(duration) or not 2 <= duration <= MAX_DURATION:
        raise djmix.MixError('Build/drop analysis supports individual songs from 2 seconds to 30 minutes. Use the other cue tools for longer recordings.')
    before = source_signature(path)
    identity = hashlib.sha256(json.dumps([VERSION, MODEL_ID, before, duration], sort_keys=True).encode()).hexdigest()
    folder = Path(cache_dir); folder.mkdir(parents=True, exist_ok=True)
    file = folder / (identity + '.sections.json')
    try:
        if file.stat().st_size <= 1024 * 1024:
            result = json.loads(file.read_text())
            if valid_result(result, duration) and result.get('source') == before:
                check_cancelled(); file.touch(); progress('Reusing unchanged build/drop observations'); return result
    except (OSError, ValueError, TypeError): pass
    verify_models(progress)
    require_space(folder, math.ceil(duration * 24000 * 4) + 1024 * 1024, 'find musical sections')
    progress('Finding likely builds, drops and other sections…')
    with tempfile.TemporaryDirectory(prefix='section-work-', dir=folder) as temp:
        result = _run_worker(Path(path).resolve(), Path(temp), progress)
    check_cancelled()
    if source_signature(path) != before:
        raise djmix.MixError('The source changed during section analysis. Reopen this song and try again.')
    result = {'version': VERSION, 'model_id': MODEL_ID, 'source': before, 'duration': duration,
              'regions': normalize_regions(result['sections'], duration), 'device': result['device']}
    check_cancelled()
    temporary = file.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(result, allow_nan=False), encoding='utf-8')
        check_cancelled(); temporary.replace(file)
    finally: temporary.unlink(missing_ok=True)
    progress('Section suggestions ready; listen to confirm the labels and boundaries')
    return result


def section_cues(session, index):
    """Intro/outro choices with supported complete bars and validated neighbors.

    Predictions are not drums-only evidence. Preserve key checks and only shorten
    an overlap when the predicted region cannot contain the existing duration.
    """
    from studio_beat_grid import display_grid
    from studio_engine import layout
    from studio_timing import corrected_time
    clip = session.clips[index]; track = session.tracks[clip.track_id]
    result = current_sections(track)
    bars = display_grid(track).get('bars', [])
    candidates = []
    regions = result.get('regions', [])
    for position, region in enumerate(regions):
        kind = {'intro': 'start', 'outro': 'end'}.get(region['label'])
        if kind is None: continue
        # Do not casually discard a later drop because an internal section was
        # labeled outro, or an earlier build because a later part says intro.
        outside = regions[:position] if kind == 'start' else regions[position + 1:]
        allowed = {'intro', 'silence'} if kind == 'start' else {'outro', 'silence'}
        if any(other['label'] not in allowed for other in outside): continue
        within = [b for b in bars if b['start'] >= region['start'] and b['end'] <= region['end']]
        if not within or any(abs(a['end'] - b['start']) > .05 for a, b in zip(within, within[1:])): continue
        cue = within[0]['start'] if kind == 'start' else within[-1]['end']
        if kind == 'start' and cue > min(90, .3 * track.duration): continue
        if kind == 'end' and cue < max(track.duration - 90, .65 * track.duration): continue
        try:
            room = math.floor((corrected_time(track, clip, within[-1]['end']) - corrected_time(track, clip, within[0]['start'])) * track.bpm / 240 + 1e-7)
        except djmix.MixError: continue
        if room < 1: continue
        pair = index if kind == 'start' else index + 1
        draft = copy.deepcopy(session)
        setattr(draft.clips[index], 'cue_in' if kind == 'start' else 'cue_out', cue)
        overlap = 0
        if 0 < pair < len(draft.clips):
            overlap = min(draft.clips[pair].blend_bars, room)
            draft.clips[pair].blend_bars = overlap
        try: layout(draft)
        except djmix.MixError: continue
        if cue == getattr(clip, 'cue_in' if kind == 'start' else 'cue_out') and (not overlap or overlap == session.clips[pair].blend_bars): continue
        candidates.append({**region, 'kind': kind, 'time': cue, 'pair': pair, 'overlap': overlap,
                           'omitted': cue if kind == 'start' else track.duration - cue})
    # Keep the earliest usable opening and latest usable ending. Multiple
    # intro/outro predictions separated by silence must not flood the editor.
    starts = [c for c in candidates if c['kind'] == 'start']
    ends = [c for c in candidates if c['kind'] == 'end']
    return starts[:1] + ends[-1:]
