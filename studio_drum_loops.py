"""Non-destructive drum-loop listening trials; no project or source edits."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf

import djmix
from studio_beat_grid import display_grid
from studio_cache import digest, source_signature
from studio_content import current_content, drum_regions, voice_regions
from studio_process import check_cancelled
from studio_storage import managed_cache, require_space

VERSION = 1
JOIN_SECONDS = .005
MAX_SECONDS = 180


def loop_choices(track, bars=4, side='intro'):
    """Whole, consecutive observed/reference bars near a song's beginning/end.

    A valid bar grid is timing evidence only. Content labels remain tentative;
    nothing here certifies drums-only material or a musically useful loop.
    """
    if bars not in (1, 2, 4, 8) or side not in ('intro', 'outro'):
        raise djmix.MixError('Choose a 1, 2, 4 or 8-bar loop near the beginning or ending.')
    grid = display_grid(track)
    by_number = {b['number']: b for b in grid.get('bars', [])}
    content = current_content(track)
    drums, voices = drum_regions(content), voice_regions(content)
    choices = []
    edge = min(90., track.duration * .3)
    for number in sorted(by_number):
        group = [by_number.get(n) for n in range(number, number + bars)]
        if any(b is None for b in group): continue
        a, b = group[0]['start'], group[-1]['end']
        if (not all(math.isfinite(v) for v in (a, b)) or not JOIN_SECONDS <= a < b <= track.duration
                or b - a > 60 or any(abs(x['end'] - y['start']) > .02 for x, y in zip(group, group[1:]))):
            continue
        if side == 'intro' and b > edge: continue
        if side == 'outro' and a < track.duration - edge: continue
        evidence = ('Drum-led candidate' if any(r['start'] <= a and r['end'] >= b for r in drums) else
                    'Possible voices — listen carefully' if any(r['start'] < b and r['end'] > a for r in voices) else
                    'Content uncertain' if content else 'Drums not checked')
        durations = [g['end'] - g['start'] for g in group]
        choices.append({'start': a, 'end': b, 'bars': bars, 'bar': number,
                        'manual': bool(grid.get('manual')), 'evidence': evidence,
                        'uneven': bool((max(durations) - min(durations)) / np.mean(durations) > .04)})
    return sorted(choices, key=lambda c: (c['evidence'] != 'Drum-led candidate',
                                         c['start'] if side == 'intro' else -c['end']))


def repeat_audio(audio, start, end, repeats, fade_frames):
    """Insert extra copies without shortening the loop or moving its attacks.

    Before each repeated downbeat, crossfade the previous loop tail into the
    source's real pre-roll. The new attack and final natural continuation are
    untouched. Linear weights avoid boosting correlated tails. This removes
    the hard splice, not musical discontinuity or timing errors in the grid.
    """
    audio = np.asarray(audio, dtype=np.float32)
    if (audio.ndim != 2 or audio.shape[1] != 2 or not np.isfinite(audio).all()
            or not all(type(n) is int for n in (start, end, repeats, fade_frames))
            or not 1 <= repeats <= 8 or not 1 < fade_frames <= start < end <= len(audio)
            or end - start <= fade_frames * 2):
        raise djmix.MixError('The loop needs stereo audio, a short lead-in and complete bars.')
    loop = audio[start:end]
    if float(np.mean(loop.astype(np.float64) ** 2)) < 1e-10:
        raise djmix.MixError('This loop is almost silent. Choose a passage with audible drums.')
    raw = np.concatenate([audio[:end], *([loop] * repeats), audio[end:]])
    smooth = raw.copy()
    joins = [end + n * len(loop) for n in range(repeats)]
    weight = np.linspace(0., 1., fade_frames, dtype=np.float32)[:, None]
    before = audio[start - fade_frames:start]
    for join in joins:
        smooth[join - fade_frames:join] = raw[join - fade_frames:join] * (1 - weight) + before * weight
    return raw, smooth, joins


def _wave(audio, bins=2400):
    """Actual stereo amplitude, retaining opposite-polarity channels."""
    step = max(1, math.ceil(len(audio) / bins))
    return [float(np.max(np.abs(audio[i:i + step]))) for i in range(0, len(audio), step)]


def _valid_saved(folder, key):
    try:
        report = json.loads((folder / 'report.json').read_text())
        if report['key'] != key: return None
        for name, frames in [('source', report['source_frames']), ('current', report['frames']), ('draft', report['frames'])]:
            info = sf.info(folder / f'{name}.wav')
            if info.frames != frames or info.samplerate != djmix.SR or info.channels != 2: return None
        return report
    except (OSError, ValueError, KeyError, TypeError, RuntimeError): return None


@managed_cache
def prepare_loop(track, choice, repeats, cache_dir, progress=lambda _: None):
    """Render a bounded, original-speed trial with two bars of natural context."""
    if type(repeats) is not int or not 1 <= repeats <= 8:
        raise djmix.MixError('Add between one and eight copies of the selected loop.')
    candidates = loop_choices(track, choice.get('bars'), choice.get('side', 'intro'))
    if not any(c['start'] == choice.get('start') and c['end'] == choice.get('end') for c in candidates):
        raise djmix.MixError('This loop no longer matches the song or bar grid. Choose the passage again.')
    identity = source_signature(track.path)
    a, b = [round(choice[k] * djmix.SR) for k in ('start', 'end')]
    context = max(round(JOIN_SECONDS * djmix.SR), round(2 * (b - a) / choice['bars']))
    lo, hi = max(0, a - context), min(math.floor(track.duration * djmix.SR), b + context)
    frames = hi - lo + (b - a) * repeats
    if frames > MAX_SECONDS * djmix.SR:
        raise djmix.MixError('This trial would exceed three minutes. Choose fewer repetitions or a shorter loop.')
    key = [VERSION, identity, lo, hi, a, b, repeats, djmix.SR, JOIN_SECONDS]
    cache_dir = Path(cache_dir); cache_dir.mkdir(parents=True, exist_ok=True)
    folder = cache_dir / 'alternatives' / digest(['drum-loop', key])
    check_cancelled()
    saved = _valid_saved(folder, key)
    if saved:
        progress('Reusing this exact loop trial')
        os.utime(folder / 'report.json', None)
        return {**saved, 'choice': choice, 'folder': str(folder)}
    require_space(cache_dir, (hi - lo + 2 * frames) * 8 + 65536, 'prepare the drum-loop trial')
    with tempfile.TemporaryDirectory(prefix='.pending-loop-', dir=cache_dir) as temporary:
        work = Path(temporary)
        progress('Reading the selected loop and its natural context')
        djmix.ffmpeg('-ss', str(lo / djmix.SR), '-i', track.path, '-t', str((hi - lo) / djmix.SR),
                     '-ar', str(djmix.SR), '-ac', '2', '-c:a', 'pcm_f32le', str(work / 'source.wav'))
        audio, rate = sf.read(work / 'source.wav', dtype='float32', always_2d=True)
        if rate != djmix.SR or len(audio) != hi - lo:
            raise djmix.MixError('The decoded passage has an unexpected length. Check the source and choose again.')
        check_cancelled(); progress('Repeating complete bars and smoothing the joins')
        raw, smooth, joins = repeat_audio(audio, a - lo, b - lo, repeats, round(JOIN_SECONDS * djmix.SR))
        for name, values in [('current', raw), ('draft', smooth)]:
            check_cancelled()
            sf.write(work / f'{name}.wav', values, djmix.SR, subtype='FLOAT')
        report = {'key': key, 'source': identity, 'choice': choice, 'repeats': repeats,
                  'source_start': lo / djmix.SR, 'source_frames': len(audio), 'frames': len(raw),
                  'duration': len(raw) / djmix.SR, 'added_seconds': (b - a) * repeats / djmix.SR,
                  'loop_start': (a - lo) / djmix.SR, 'loop_end': (b - lo) / djmix.SR,
                  'join_times': [n / djmix.SR for n in joins], 'wave': _wave(smooth),
                  'source_wave': _wave(audio), 'join_ms': JOIN_SECONDS * 1000}
        (work / 'report.json').write_text(json.dumps(report, allow_nan=False))
        check_cancelled()
        if source_signature(track.path) != identity:
            raise djmix.MixError('The song changed while preparing the loop. Select it again.')
        # Publish files only after successful validation; interrupted work never
        # becomes a reusable result. Corrupt old entries can be replaced in place.
        folder.mkdir(parents=True, exist_ok=True)
        for name in ('source.wav', 'current.wav', 'draft.wav', 'report.json'):
            os.replace(work / name, folder / name)
        progress('Loop trial ready — listen before deciding')
        return {**report, 'folder': str(folder)}
