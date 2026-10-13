"""Optional, auditioned separation inside one song's existing overlap."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
import soundfile as sf

import djmix
from studio_cache import digest, engine_signature, source_signature
from studio_engine import Track, layout, track_id
from studio_loop_extension import _peaks
from studio_process import check_cancelled, _stop
from studio_separation_model import MODEL_FILE, MODEL_SHA256, MODEL_BYTES, STEMS
from studio_source_identity import fingerprint, rebind_analysis
from studio_storage import managed_cache, require_space
from studio_timing import mapping, corrected_time

VERSION = 1
ROOT = Path(__file__).resolve().parent
MODES = {'instrumental': 'Reduce vocals', 'drums': 'Keep mostly drums'}


def model_directory():
    packaged = ROOT / 'assets' / 'separation'
    if getattr(sys, 'frozen', False) or (packaged / MODEL_FILE).is_file(): return packaged
    return ROOT / 'outputs/spleeter-research/models'


def worker_command():
    if getattr(sys, 'frozen', False): return [sys.executable, '--separation-worker']
    return [sys.executable, str(ROOT / 'studio_separation_worker.py')]


def available():
    try:
        model = model_directory() / MODEL_FILE
        return model.stat().st_size == MODEL_BYTES and Path(worker_command()[0]).is_file()
    except OSError: return False


def plan_separation(session, index, side, mode='instrumental', amount=75):
    positions = layout(session)
    if not 0 <= index < len(session.clips): raise djmix.MixError('Select a song in the mix.')
    if side not in ('intro', 'outro') or mode not in MODES or type(amount) is not int or not 1 <= amount <= 100:
        raise djmix.MixError('Choose a neighboring blend and a reduction from 1 to 100 percent.')
    pair = index if side == 'intro' else index + 1
    if not 0 < pair < len(positions): raise djmix.MixError('This song has no neighboring blend on that side.')
    clip = session.clips[index]; track = session.tracks[clip.track_id]
    if track.duration > 1800: raise djmix.MixError('Separated copies support songs up to 30 minutes long.')
    points = mapping(track, clip)
    if points is not None and track.timing.get('source') != source_signature(track.path):
        raise djmix.MixError('Analyze timing again before using this song’s old correction map.')
    seconds = positions[pair]['incoming'] / djmix.SR * session.target_bpm / track.bpm
    if side == 'intro': a, b = corrected_time(track, clip, clip.cue_in), corrected_time(track, clip, clip.cue_in) + seconds
    else: a, b = corrected_time(track, clip, clip.cue_out) - seconds, corrected_time(track, clip, clip.cue_out)
    if points is not None: a, b = np.interp([a, b], points[:, 1], points[:, 0])
    a, b = max(clip.cue_in, float(a)), min(clip.cue_out, float(b))
    if not 0 < b - a <= 240: raise djmix.MixError('Choose an overlap shorter than four source minutes.')
    if any(p['start'] < b and p['end'] > a for p in clip.protected_moments):
        raise djmix.MixError('This overlap touches a protected passage. Adjust the overlap or review its protection before changing its musical parts.')
    return dict(index=index, pair=pair, side=side, mode=mode, amount=amount,
                start=round(a * djmix.SR), end=round(b * djmix.SR),
                fade=min(round(60 / track.bpm * djmix.SR), round((b - a) * djmix.SR) // 4))


def _run_worker(work, progress):
    command = worker_command() + [str(work)]
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    previous = None
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options) as process:
        try:
            while True:
                check_cancelled()
                try:
                    path = work / 'progress.json'
                    if path.stat().st_size <= 4096:
                        value = json.loads(path.read_text())
                        if isinstance(value, str) and value != previous: progress(value); previous = value
                except (OSError, ValueError): pass
                try: code = process.wait(timeout=.1); break
                except subprocess.TimeoutExpired: pass
            check_cancelled()
            path = work / 'result.json'
            if not path.is_file() or path.stat().st_size > 4096:
                raise djmix.MixError('Separation stopped before finishing. Try again; the mix has not changed.')
            result = json.loads(path.read_text())
            if code or 'error' in result:
                raise djmix.MixError('Separation could not finish: ' + str(result.get('error', 'worker stopped'))[:1200])
            return result
        finally: _stop(process)


def _stems(track, plan, cache, progress):
    signature = source_signature(track.path)
    key = [VERSION, MODEL_SHA256, engine_signature(), signature, track.duration, plan['start'], plan['end']]
    folder = cache / 'alternatives' / digest(['separated-parts', key])
    names = ('decoded', *STEMS)
    try:
        saved = json.loads((folder / 'report.json').read_text())
        if saved['key'] != key or any(source_signature(folder / (n + '.wav')) != saved['files'][n] for n in names):
            raise ValueError('Stale parts')
        progress('Reusing the separated musical parts'); os.utime(folder / 'report.json', None)
        return folder, key
    except (OSError, ValueError, KeyError): pass
    if not available(): raise djmix.MixError('Local separation is not installed. Install the separation edition to prepare musical parts; the normal mix tools remain available.')
    require_space(cache, math.ceil(track.duration * djmix.SR * 8 + (plan['end'] - plan['start']) * 40), 'separate this overlap')
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.separate-', dir=cache) as temp:
        work = Path(temp)
        progress('Reading the original song')
        djmix.ffmpeg('-i', track.path, '-map', '0:a:0', '-t', '1800.2', '-ar', str(djmix.SR), '-ac', '2', '-c:a', 'pcm_f32le', str(work / 'decoded.wav'))
        info = sf.info(work / 'decoded.wav'); expected = round(track.duration * djmix.SR)
        if abs(info.frames - expected) > .15 * djmix.SR: raise djmix.MixError('The source duration changed. Re-import this song before separating it.')
        if info.frames < expected:
            with sf.SoundFile(work / 'decoded.wav', 'r+') as file:
                file.seek(0, sf.SEEK_END); file.write(np.zeros((expected - info.frames, 2), dtype='float32'))
        (work / 'request.json').write_text(json.dumps({'start': plan['start'], 'end': plan['end'], 'models': str(model_directory())}))
        result = _run_worker(work, progress)
        if result != {'frames': plan['end'] - plan['start'], 'rate': djmix.SR, 'channels': 2}:
            raise djmix.MixError('The separated parts did not cover the complete overlap.')
        for name in STEMS:
            info = sf.info(work / (name + '.wav'))
            if (info.frames, info.samplerate, info.channels) != (result['frames'], djmix.SR, 2):
                raise djmix.MixError('A separated part is incomplete.')
        check_cancelled()
        if source_signature(track.path) != signature: raise djmix.MixError('The source changed during separation. Try again.')
        folder.mkdir(exist_ok=True)
        for name in names: (work / (name + '.wav')).replace(folder / (name + '.wav'))
        saved = dict(key=key, files={n: source_signature(folder / (n + '.wav')) for n in names})
        (work / 'report.json').write_text(json.dumps(saved)); (work / 'report.json').replace(folder / 'report.json')
    return folder, key


def write_modified(source, target, stems, plan):
    a, b, fade = plan['start'], plan['end'], plan['fade']
    if plan['mode'] == 'drums': estimate = stems['drums']
    else: estimate = stems['drums'] + stems['bass'] + stems['other']
    if estimate.shape != (b - a, 2) or not np.isfinite(estimate).all():
        raise djmix.MixError('Invalid separated audio. Prepare this overlap again.')
    weight = np.full(b - a, plan['amount'] / 100, dtype='float32')
    if fade:
        ramp = np.linspace(0, 1, fade, dtype='float32')
        weight[:fade] *= ramp; weight[-fade:] *= ramp[::-1]
    with sf.SoundFile(source) as audio, sf.SoundFile(target, 'w', samplerate=djmix.SR, channels=2, subtype='FLOAT') as out:
        if audio.samplerate != djmix.SR or audio.channels != 2 or not 0 <= a < b <= audio.frames:
            raise djmix.MixError('The decoded source does not match this overlap.')
        while audio.tell() < audio.frames:
            check_cancelled(); left = audio.tell()
            chunk = audio.read(65536, dtype='float32', always_2d=True)
            if not len(chunk) or not np.isfinite(chunk).all(): raise djmix.MixError('The source contains invalid audio.')
            lo, hi = max(a, left), min(b, left + len(chunk))
            if lo < hi:
                gains = weight[lo - a:hi - a, None]
                chunk[lo - left:hi - left] = chunk[lo - left:hi - left] * (1 - gains) + estimate[lo - a:hi - a] * gains
            out.write(chunk)


def draft_with_copy(session, index, replacement):
    draft = copy.deepcopy(session); original = draft.clips[index].track_id
    draft.tracks[replacement.id] = copy.deepcopy(replacement)
    draft.clips[index].track_id = replacement.id
    # Separation is fallible; do not turn a stem choice into a drums-only claim.
    draft.clips[index].intro_percussive = draft.clips[index].outro_percussive = False
    for members in draft.collections.values():
        if original in members and replacement.id not in members: members.append(replacement.id)
    if layout(draft) != layout(session): raise djmix.MixError('The separated copy would move the arrangement. Prepare it again.')
    return draft


@managed_cache
def prepare_separation(session, index, side, mode, amount, cache_dir, progress=lambda _: None):
    check_cancelled()
    plan = plan_separation(session, index, side, mode, amount)
    original = session.tracks[session.clips[index].track_id]; signature = source_signature(original.path)
    cache = Path(cache_dir); cache.mkdir(parents=True, exist_ok=True)
    parts, parts_key = _stems(original, plan, cache, progress)
    key = [VERSION, parts_key, mode, amount, plan['fade']]
    folder = cache / 'alternatives' / digest(['separated-source', key]); target = folder / 'modified.wav'
    try:
        saved = json.loads((folder / 'report.json').read_text())
        if saved['key'] != key or saved['copy_signature'] != source_signature(target): raise ValueError('Stale copy')
        progress('Reusing this separated source copy'); os.utime(folder / 'report.json', None)
    except (OSError, ValueError, KeyError):
        require_space(cache, round(original.duration * djmix.SR * 8), 'prepare this separated source copy')
        with tempfile.TemporaryDirectory(prefix='.separation-copy-', dir=cache) as temp:
            work = Path(temp)
            stems = {name: sf.read(parts / (name + '.wav'), dtype='float32', always_2d=True)[0] for name in STEMS}
            write_modified(parts / 'decoded.wav', work / 'modified.wav', stems, plan)
            saved = dict(key=key, frames=sf.info(work / 'modified.wav').frames, waveform=_peaks(work / 'modified.wav'))
            check_cancelled()
            folder.mkdir(exist_ok=True); (work / 'modified.wav').replace(target)
            saved['copy_signature'] = source_signature(target)
            (work / 'report.json').write_text(json.dumps(saved)); (work / 'report.json').replace(folder / 'report.json')
    check_cancelled()
    if source_signature(original.path) != signature: raise djmix.MixError('The source changed while preparing this draft.')
    replacement = copy.deepcopy(original)
    replacement.id, replacement.path = track_id(target), str(target.resolve())
    replacement.name = f'{original.name} · {"less voice" if mode == "instrumental" else "mostly drums"} in {"intro" if side == "intro" else "outro"} ({amount}%)'
    replacement.waveform = saved['waveform']; replacement.fingerprint = {}
    replacement.content = {}; replacement.structure = {}; replacement.song_sections = {}
    # Timing and beat positions remain exactly the same; content observations do not.
    rebind_analysis(replacement, signature, saved['copy_signature'])
    replacement.status = 'Separated overlap copy · timing and key inherited; audition for artifacts'
    draft = draft_with_copy(session, index, replacement)
    return dict(draft=draft, index=index, plan=plan, copy=replacement, source=signature,
                copy_signature=saved['copy_signature'], key=key, folder=str(folder), parts_folder=str(parts))


def publish_separation(result, directory, progress=lambda _: None):
    source = Path(result['copy'].path)
    if source_signature(source) != result['copy_signature']: raise djmix.MixError('The prepared copy changed. Prepare it again.')
    root = Path(directory).resolve(); root.mkdir(parents=True, exist_ok=True)
    target = root / ('separation-' + digest(result['key']) + '.wav')
    known = fingerprint(source, progress)
    require_space(root, 0 if target.exists() else source.stat().st_size, 'keep this separated source copy')
    with tempfile.NamedTemporaryFile(prefix='.separation-', suffix='.wav', dir=root, delete=False) as handle:
        pending = Path(handle.name)
    try:
        if not target.exists():
            with source.open('rb') as inp, pending.open('wb') as out:
                while block := inp.read(1024 * 1024):
                    check_cancelled(); out.write(block); progress('Saving the separated source copy')
            if fingerprint(pending, progress)['sha256'] != known['sha256']: raise djmix.MixError('The prepared audio changed during saving.')
            check_cancelled()
            # Same-directory hard link publishes a complete file exclusively, including on NTFS.
            try: os.link(pending, target)
            except FileExistsError: pass
        found = fingerprint(target, progress)
        if found['sha256'] != known['sha256']:
            raise djmix.MixError('The stored separated copy was changed outside BeatStitch. It will not be overwritten.')
    finally: pending.unlink(missing_ok=True)
    check_cancelled()
    if source_signature(source) != result['copy_signature']: raise djmix.MixError('The prepared audio changed during saving.')
    replacement = copy.deepcopy(result['copy'])
    old = source_signature(source)
    replacement.path, replacement.id, replacement.fingerprint = str(target), track_id(target), found
    rebind_analysis(replacement, old, found['source'])
    return replacement
