"""Reviewable loop extensions as ordinary, durable source copies for one clip."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf

import djmix
from studio_cache import digest, engine_signature, source_signature
from studio_drum_loops import JOIN_SECONDS, loop_choices
from studio_engine import Clip, Track, layout, track_id
from studio_process import check_cancelled
from studio_source_identity import fingerprint
from studio_storage import managed_cache, require_space
from studio_timing import corrected_time, mapping, prepare as prepare_timing

VERSION = 1


def choices_for_clip(session, index, bars, side):
    clip = session.clips[index]; track = session.tracks[clip.track_id]
    return [{**c, 'side': side} for c in loop_choices(track, bars, side)
            if c['start'] >= clip.cue_in and c['end'] <= clip.cue_out
            and not any(p['start'] < c['end'] and p['end'] > c['start'] for p in clip.protected_moments)]


def extension_plan(session, index, choice, repeats, smooth=True, overlap=None):
    check_cancelled()
    layout(session)
    if not 0 <= index < len(session.clips): raise djmix.MixError('Select a song in the mix first.')
    if type(repeats) is not int or not 1 <= repeats <= 8:
        raise djmix.MixError('Add one to eight copies of the loop.')
    clip = session.clips[index]; track = session.tracks[clip.track_id]
    available = choices_for_clip(session, index, choice['bars'], choice['side'])
    if not any(c['start'] == choice['start'] and c['end'] == choice['end'] for c in available):
        raise djmix.MixError('Choose complete bars inside the kept song and outside protected passages. Check the beat grid if no choices are available.')
    if track.duration > 1800: raise djmix.MixError('Loop copies currently support individual songs up to 30 minutes long.')
    points = mapping(track, clip)
    if points is not None and track.timing.get('source') != source_signature(track.path):
        raise djmix.MixError('The tempo-correction map belongs to an older source. Analyze timing again.')
    a, b = [round(corrected_time(track, clip, choice[k]) * djmix.SR) for k in ('start', 'end')]
    if not round(JOIN_SECONDS * djmix.SR) <= a < b or b - a <= 2 * round(JOIN_SECONDS * djmix.SR):
        raise djmix.MixError('Choose a loop with a few milliseconds of natural lead-in.')
    added = (b - a) * repeats
    pair = index if choice['side'] == 'intro' else index + 1
    pair = pair if 0 < pair < len(session.clips) else None
    if overlap is not None and (pair is None or type(overlap) is not int or not 1 <= overlap <= 32):
        raise djmix.MixError('Choose a blend length from 1 to 32 bars for a neighboring song.')
    return {'choice': choice, 'repeats': repeats, 'smooth': bool(smooth), 'start_frame': a, 'end_frame': b,
            'added_frames': added, 'baked_timing': points is not None, 'pair': pair,
            'overlap': overlap if overlap is not None else (session.clips[pair].blend_bars if pair is not None else None)}


def draft_with_copy(session, index, plan, replacement):
    """Keep all other clips, shift kept/protected times through the insertion."""
    draft = copy.deepcopy(session)
    original_clip = session.clips[index]; original = session.tracks[original_clip.track_id]
    clip = draft.clips[index]
    extra = plan['added_frames'] / djmix.SR
    boundary = plan['choice']['end']
    def converted(seconds, *, end=False):
        base = float(corrected_time(original, original_clip, seconds))
        # A protected interval ending at the insertion stays before the repeats;
        # one starting there moves after them. The clip End keeps all added bars.
        after = seconds > boundary if end else seconds >= boundary
        return base + (extra if after else 0)
    clip.cue_in = converted(original_clip.cue_in)
    clip.cue_out = float(corrected_time(original, original_clip, original_clip.cue_out)) + extra
    clip.protected_moments = [{**p, 'start': converted(p['start']), 'end': converted(p['end'], end=True)}
                              for p in original_clip.protected_moments]
    clip.track_id, clip.stabilize = replacement.id, False
    # Both old labels are observations about different source-file positions.
    clip.intro_percussive = clip.outro_percussive = False
    draft.tracks[replacement.id] = copy.deepcopy(replacement)
    for members in draft.collections.values():
        if original.id in members and replacement.id not in members: members.append(replacement.id)
    if plan['pair'] is not None: draft.clips[plan['pair']].blend_bars = plan['overlap']
    layout(draft)
    return draft


def _write_extended(source, destination, a, b, repeats, smooth, progress):
    fade = round(JOIN_SECONDS * djmix.SR)
    with sf.SoundFile(source) as audio, sf.SoundFile(destination, 'w', samplerate=djmix.SR,
                                                   channels=2, subtype='FLOAT') as out:
        if audio.samplerate != djmix.SR or audio.channels != 2 or not fade <= a < b <= audio.frames:
            raise djmix.MixError('The decoded song does not cover this loop.')
        audio.seek(a); loop = audio.read(b - a, dtype='float32', always_2d=True)
        if not np.isfinite(loop).all() or float(np.mean(loop.astype(np.float64) ** 2)) < 1e-10:
            raise djmix.MixError('This passage is almost silent or invalid. Choose audible drums.')
        audio.seek(a - fade); before = audio.read(fade, dtype='float32', always_2d=True)
        weight = np.linspace(0, 1, fade, dtype=np.float32)[:, None]
        tail = loop[-fade:] * (1 - weight) + before * weight if smooth else loop[-fade:]
        def write_range(start, end):
            audio.seek(start)
            while audio.tell() < end:
                check_cancelled()
                chunk = audio.read(min(65536, end - audio.tell()), dtype='float32', always_2d=True)
                if not len(chunk) or not np.isfinite(chunk).all(): raise djmix.MixError('The source contains invalid audio.')
                out.write(chunk)
        write_range(0, b - fade)
        for n in range(repeats):
            progress(f'Adding loop copy {n + 1} of {repeats}')
            out.write(tail); write_range(a, b - fade)
        write_range(b - fade, audio.frames)


def _peaks(path):
    with sf.SoundFile(path) as audio:
        peaks = []
        while audio.tell() < audio.frames:
            check_cancelled()
            peaks.append(float(np.max(abs(audio.read(max(1, math.ceil(audio.frames / 1400)), dtype='float32', always_2d=True)))))
    scale = max(.001, float(np.percentile(peaks, 98)))
    return np.clip(np.asarray(peaks) / scale, 0, 1).round(4).tolist()


@managed_cache
def prepare_extension(session, index, choice, repeats, cache_dir, progress=lambda _: None, *, smooth=True, overlap=None):
    plan = extension_plan(session, index, choice, repeats, smooth, overlap)
    original_clip = session.clips[index]; original = session.tracks[original_clip.track_id]
    signature = source_signature(original.path)
    points = mapping(original, original_clip)
    key = [VERSION, engine_signature(), signature, plan['start_frame'], plan['end_frame'], repeats,
           bool(smooth), original.bpm, original.duration, points.tolist() if points is not None else None]
    cache_dir = Path(cache_dir); folder = cache_dir / 'alternatives' / digest(['extended-source', key])
    destination = folder / 'extended.wav'
    try:
        saved = json.loads((folder / 'report.json').read_text())
        info = sf.info(destination)
        if (saved['key'] != key or info.frames != saved['frames'] or info.channels != 2
                or info.samplerate != djmix.SR or saved['copy_signature'] != source_signature(destination)):
            raise ValueError('Stale copy')
        progress('Reusing this extended source copy'); os.utime(folder / 'report.json', None)
    except (OSError, ValueError, KeyError, RuntimeError):
        folder.parent.mkdir(parents=True, exist_ok=True)
        require_space(cache_dir, math.ceil(original.duration * djmix.SR * 32 + plan['added_frames'] * 8), 'create the extended song')
        with tempfile.TemporaryDirectory(prefix='.pending-extension-', dir=cache_dir) as temporary:
            work = Path(temporary); decoded = work / 'decoded.wav'
            if plan['baked_timing']:
                progress('Including the accepted tempo correction in the copy')
                full = Clip(original.id, 0, original.duration, stabilize=True)
                raw = prepare_timing(original, full, original.bpm, work, 'corrected', 0.)
                djmix.ffmpeg('-f', 'f32le', '-ar', str(djmix.SR), '-ac', '2', '-i', str(raw), '-c:a', 'pcm_f32le', str(decoded))
            else:
                progress('Reading the complete source song')
                djmix.ffmpeg('-i', original.path, '-map', '0:a:0', '-ar', str(djmix.SR), '-ac', '2', '-c:a', 'pcm_f32le', str(decoded))
            info = sf.info(decoded)
            expected = round(original.duration * djmix.SR)
            if abs(info.frames - expected) > .15 * djmix.SR:
                raise djmix.MixError('Decoded duration differs from this project. Re-import the current recording.')
            # Some compressed duration tags include encoder padding. Preserve the
            # entire decoded recording and pad only a short missing tagged tail.
            if info.frames < expected:
                with sf.SoundFile(decoded, 'r+') as audio:
                    audio.seek(0, sf.SEEK_END); audio.write(np.zeros((expected - info.frames, 2), dtype='float32'))
            _write_extended(decoded, work / 'extended.wav', plan['start_frame'], plan['end_frame'], repeats, smooth, progress)
            check_cancelled()
            if source_signature(original.path) != signature: raise djmix.MixError('The source changed while creating the copy.')
            frames = sf.info(work / 'extended.wav').frames
            waveform = _peaks(work / 'extended.wav')
            folder.mkdir(exist_ok=True); os.replace(work / 'extended.wav', destination)
            saved = {'key': key, 'frames': frames, 'waveform': waveform, 'copy_signature': source_signature(destination)}
            (work / 'report.json').write_text(json.dumps(saved, allow_nan=False))
            os.replace(work / 'report.json', folder / 'report.json')
    check_cancelled()
    if source_signature(original.path) != signature: raise djmix.MixError('The source changed while creating the copy.')
    replacement = Track(track_id(destination), str(destination.resolve()),
                        f'{original.name} · +{choice["bars"] * repeats} drum bars', saved['frames'] / djmix.SR,
                        bpm=original.bpm, key=original.key, cue_out=saved['frames'] / djmix.SR,
                        estimated=original.estimated, waveform=saved['waveform'],
                        status='Extended copy · BPM and key inherited; check its beats and timing')
    draft = draft_with_copy(session, index, plan, replacement)
    return {'draft': draft, 'index': index, 'plan': plan, 'copy': replacement,
            'source': signature, 'copy_signature': source_signature(destination), 'key': key, 'folder': str(folder)}


def publish_extension(result, directory, progress=lambda _: None):
    """Make a verified, ordinary source file that cache cleanup cannot remove."""
    check_cancelled()
    source = Path(result['copy'].path)
    if source_signature(source) != result['copy_signature']:
        raise djmix.MixError('The prepared copy changed. Prepare the extension again.')
    root = Path(directory).resolve(); root.mkdir(parents=True, exist_ok=True)
    destination = root / ('drum-loop-' + digest(result['key']) + '.wav')
    progress('Verifying the extended audio')
    known = fingerprint(source, progress)
    if destination.exists():
        found = fingerprint(destination, progress)
        if found['sha256'] != known['sha256']:
            raise djmix.MixError('The stored extended copy was changed outside DJ Studio. Keep it and prepare a different extension; it will not be overwritten.')
    else:
        require_space(root, source.stat().st_size, 'keep the extended source copy')
        # Exclusive creation never overwrites an original, previous copy or race.
        created = False
        try:
            with destination.open('xb') as out, source.open('rb') as inp:
                created = True
                while block := inp.read(1024 * 1024):
                    check_cancelled(); out.write(block)
                    progress('Saving the extended source copy')
            found = fingerprint(destination, progress)
            check_cancelled()
            if found['sha256'] != known['sha256'] or source_signature(source) != result['copy_signature']:
                raise djmix.MixError('The prepared audio changed during saving. Prepare it again.')
        except Exception:
            if created: destination.unlink(missing_ok=True)
            raise
    check_cancelled()
    if source_signature(source) != result['copy_signature']:
        raise djmix.MixError('The prepared audio changed during saving. Prepare it again.')
    replacement = copy.deepcopy(result['copy'])
    replacement.path, replacement.id, replacement.fingerprint = str(destination), track_id(destination), found
    return replacement
