"""Source-key observations, cache reuse and explicit ownership of editable labels."""
from __future__ import annotations
import copy
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

import djmix
from studio_cache import source_signature
from studio_key_model import MODEL_SHA256, SOURCE_REVISION, RUNTIME_PACKAGES, verify_model
from studio_process import check_cancelled, _stop
from studio_storage import managed_cache, require_space

VERSION = 1
ROOT = Path(__file__).resolve().parent
MODEL_ID = hashlib.sha256(f'{VERSION}:{MODEL_SHA256}:{SOURCE_REVISION}:first60-mono44100-f32'.encode()).hexdigest()
KEYS = {djmix.key_name((root, mode)) for mode in ('major', 'minor') for root in range(12)}


def valid_result(result):
    try:
        if (result['version'] != VERSION or result['model_id'] != MODEL_ID
                or result['state'] not in ('estimated', 'uncertain')
                or not isinstance(result['reason'], str) or len(result['reason']) > 1200
                or not 0 < result['excerpt_seconds'] <= 60.1
                or not math.isfinite(result['excerpt_seconds'])
                or type(result['frames']) is not int
                or result['frames'] != round(result['excerpt_seconds'] * 44100)):
            return False
        candidates = result['candidates']
        if result['state'] == 'uncertain':
            return candidates == []
        if not isinstance(candidates, list) or len(candidates) != 24:
            return False
        scores = [c['score'] for c in candidates]
        return (set(c['key'] for c in candidates) == KEYS
                and all(type(s) in (int, float) and math.isfinite(s) and 0 <= s <= 1 for s in scores)
                and abs(sum(scores) - 1) <= 1e-5
                and all(a >= b for a, b in zip(scores, scores[1:])))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def current_analysis(track):
    result = getattr(track, 'key_analysis', {})
    try:
        return result if valid_result(result) and result.get('source') == source_signature(track.path) else {}
    except (OSError, ValueError):
        return {}


def remember_analysis(track, result, *, update_automatic=False):
    """A saved legacy label (including an unknown manual origin) stays in control."""
    track.key_analysis = copy.deepcopy(result)
    protected = track.key_origin in ('manual', 'saved')
    if update_automatic and not protected and valid_result(result):
        track.key = result['candidates'][0]['key'] if result['state'] == 'estimated' else ''
        track.key_origin = 'detected'


def set_manual_key(track, key):
    track.key = djmix.key_name(djmix.parse_key(key)) if key else ''
    track.key_origin = 'manual'  # Unknown is an intentional choice too.


def accept_estimate(track):
    result = current_analysis(track)
    if not result or result['state'] != 'estimated':
        raise djmix.MixError('Check this song’s key again before using its estimate.')
    track.key = result['candidates'][0]['key']
    track.key_origin = 'detected'


def guidance(track):
    origin = getattr(track, 'key_origin', 'saved')
    title = {'manual': 'Your key choice is kept', 'saved': 'Saved key is kept',
             'detected': 'Estimated key · check by ear'}.get(origin, 'Key needs checking')
    if not track.key:
        title = 'Unknown · chosen by you' if origin == 'manual' else 'Key unknown'
    result = current_analysis(track)
    if result:
        if result['state'] == 'estimated':
            title += ' · estimate: ' + result['candidates'][0]['key'] if origin != 'detected' else ''
            alternatives = ', '.join(c['key'] for c in result['candidates'][1:3])
            detail = result['reason'] + ' Other possibilities: ' + alternatives + '.'
        else:
            detail = result['reason']
    else:
        detail = 'Detect key offers an estimate without replacing this label. Restore detected accepts it; choose any source key or Unknown yourself. The library menu calls these Check key and Use estimate.'
        if getattr(track, 'key_analysis', {}).get('state') == 'unavailable':
            detail = track.key_analysis.get('reason', detail)
    return title, detail + ' Detection is imperfect. This label does not prove the notes in every overlap fit.'


def _run_worker(source, folder):
    output = folder / 'result.json'
    command = ([sys.executable, '--key-worker'] if getattr(sys, 'frozen', False)
               else [sys.executable, str(ROOT / 'studio_key_worker.py')]) + [str(source), str(output)]
    environment = os.environ.copy()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        environment[name] = '1'
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {'start_new_session': True}
    deadline = time.monotonic() + 180
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment, **options) as process:
        try:
            while True:
                check_cancelled()
                if time.monotonic() > deadline:
                    raise djmix.MixError('Key detection took too long. Try again, or choose a key yourself.')
                try:
                    code = process.wait(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
            check_cancelled()
            if not output.is_file() or output.stat().st_size > 64000:
                raise djmix.MixError('Key detection stopped before finishing. Try again, or set the key yourself.')
            result = json.loads(output.read_text())
            if code or 'error' in result:
                raise djmix.MixError('Key detection could not finish: ' + str(result.get('error', 'worker stopped'))[:1200])
            return result
        finally:
            if process.poll() is None:
                if sys.platform == 'win32':
                    try:
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                else:
                    try: os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
            _stop(process)


@managed_cache
def analyze_key(path, cache_dir=None, progress=lambda message: None):
    check_cancelled()
    before = source_signature(path)
    try:
        runtime = {name: metadata.version(name) for name in RUNTIME_PACKAGES}
    except metadata.PackageNotFoundError as error:
        raise djmix.MixError('Key detection is not installed in this build. You can still set the key yourself.') from error
    runtime['ffmpeg'] = djmix.run([djmix.tool_path('ffmpeg'), '-version']).stdout.decode().splitlines()[0]
    identity = hashlib.sha256(json.dumps([MODEL_ID, before, runtime], sort_keys=True).encode()).hexdigest()
    folder = Path(cache_dir) if cache_dir is not None else None
    cached = folder / (identity + '.key.json') if folder else None
    if cached:
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if cached.stat().st_size <= 64000:
                result = json.loads(cached.read_text())
                if valid_result(result) and result.get('source') == before and result.get('runtime') == runtime:
                    check_cancelled(); cached.touch(); progress('Reusing unchanged key estimate'); return result
        except (OSError, ValueError, TypeError):
            pass
    verify_model()
    require_space(folder or Path(tempfile.gettempdir()), 12 * 1024 * 1024, 'check the song key')
    progress('Estimating the key from the song’s first minute')
    with tempfile.TemporaryDirectory(prefix='key-work-', dir=folder) as temporary:
        result = _run_worker(Path(path).resolve(), Path(temporary))
    check_cancelled()
    if before != source_signature(path):
        raise djmix.MixError('The source changed during key detection. Check it again before accepting an estimate.')
    result.update(version=VERSION, model_id=MODEL_ID, source=before, runtime=runtime)
    if not valid_result(result):
        raise djmix.MixError('Key detection returned an invalid estimate. Keep your current label and try again.')
    if cached:
        pending = cached.with_suffix('.tmp')
        try:
            pending.write_text(json.dumps(result, allow_nan=False), encoding='utf-8')
            check_cancelled(); pending.replace(cached)
        finally:
            pending.unlink(missing_ok=True)
    return result
