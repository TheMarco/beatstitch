"""Bounded, source-sensitive passage observations and cautious drum-led candidates."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
import soundfile as sf

import djmix
from studio_cache import digest, source_signature
from studio_storage import managed_cache, require_space

VERSION = 1
RATE, WINDOW, STEP = 32000, 10, 5
# Listening prompts use the validated, more sensitive cutoff. Raw observations
# and their version-1 classification remain reusable; this is not a probability.
VOICE_PROMPT_THRESHOLD = .08
MODEL_SHA256 = '5240bdc47444e331bbeb54fd741d2b3f933a4526e84b0c17bf3593df94b13962'
MODEL_SIZE = 25237595
MODEL_URL = 'https://zenodo.org/api/records/3987831/files/Cnn10_mAP=0.380.pth/content'
# AudioSet indices from the pinned author's class_labels_indices.csv.
# Pitched percussion is deliberately not counted as unpitched drums.
GROUPS = {'voice': (*range(0, 40), 254), 'drums': (*range(161, 169), *range(171, 177)),
          'pitched': (*range(139, 159), 160, 169, 170, *range(177, 212)), 'music': (137,)}


def model_path():
    from studio_model_store import directory
    return directory('content') / 'panns-cnn10.pth'


def verify_model(path=None):
    path = Path(path) if path is not None else model_path()
    if not path.is_file() or path.stat().st_size != MODEL_SIZE:
        from studio_model_store import missing_message
        raise djmix.MixError(missing_message('content'))
    h = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024): h.update(chunk)
    if h.hexdigest() != MODEL_SHA256:
        from studio_model_store import missing_message
        raise djmix.MixError(missing_message('content'))
    return path


def state(scores, rms_db):
    """Version-1 cached classification, not current voice-prompt policy.

    Keep this stable so saved raw observations remain valid. Drum-led selection
    already vetoes voice scores >= .08; voice_regions uses that same cutoff.
    Neither classification nor score is a calibrated probability or absence proof.
    """
    if rms_db < -65: return 'quiet'
    if scores['voice'] >= .2: return 'voice'
    if scores['drums'] >= .35 and scores['voice'] < .08 and scores['pitched'] < .15:
        return 'drum_led'
    return 'uncertain'


def valid_result(result, duration):
    try:
        if (result['version'] != VERSION or result['model_sha256'] != MODEL_SHA256
                or result['duration'] != duration or not 0 < len(result['windows']) <= 2881): return False
        prior = -1
        for window in result['windows']:
            a, b = window['start'], window['end']
            if not (prior < a < b <= duration + .001 and b - a <= WINDOW + .001): return False
            prior = a
            scores = window['scores']
            if set(scores) != set(GROUPS) or not all(math.isfinite(v) and 0 <= v <= 1 for v in scores.values()): return False
            if not math.isfinite(window['rms_db']) or window['state'] != state(scores, window['rms_db']): return False
        return True
    except (TypeError, ValueError, KeyError): return False


def current_content(track):
    result = track.content
    if not isinstance(result, dict): return {}
    try:
        return result if (result.get('source') == source_signature(track.path)
                          and valid_result(result, track.duration)) else {}
    except OSError: return {}


@managed_cache
def analyze_content(path, duration, cache_dir, progress=lambda _: None):
    """Decode to temporary disk, then infer one ten-second window at a time."""
    if not math.isfinite(duration) or not 2 <= duration <= 4 * 3600:
        raise djmix.MixError('Passage analysis needs a song between two seconds and four hours long.')
    signature = source_signature(path)
    cache_dir = Path(cache_dir); cache_dir.mkdir(parents=True, exist_ok=True)
    key = [VERSION, MODEL_SHA256, signature, duration]
    destination = cache_dir / (digest(['passages', key]) + '.json')
    try:
        if destination.stat().st_size > 2 * 1024 * 1024: raise ValueError('Oversized passage cache')
        saved = json.loads(destination.read_text())
        if saved['key'] == key and valid_result(saved['analysis'], duration):
            progress('Reusing passage observations'); os.utime(destination, None)
            return {**saved['analysis'], 'source': signature}
    except (OSError, ValueError, KeyError, TypeError): pass
    checkpoint = verify_model()
    if abs(djmix.probe(Path(path))['duration'] - duration) > .1:
        raise djmix.MixError('The song changed on disk. Re-import it before finding passages.')
    require_space(cache_dir, math.ceil(duration * RATE * 8) + 65536, 'analyze song passages')
    import torch
    from studio_content_model import ContentModel
    prior_threads = torch.get_num_threads()
    torch.set_num_threads(min(4, max(1, os.cpu_count() or 1)))
    try:
        progress('Loading the local passage detector')
        model = ContentModel(checkpoint)
        with tempfile.TemporaryDirectory(prefix='.pending-passages-', dir=cache_dir) as temporary:
            work = Path(temporary); decoded = work / 'source.wav'
            progress('Reading audio for passage analysis')
            djmix.ffmpeg('-i', str(path), '-t', str(duration + .1), '-ar', str(RATE), '-ac', '2',
                         '-c:a', 'pcm_f32le', str(decoded))
            windows = []
            with sf.SoundFile(decoded) as source, torch.inference_mode():
                length = len(source) / RATE
                if source.samplerate != RATE or source.channels != 2 or abs(length - duration) > .15:
                    raise djmix.MixError('Decoded audio length changed. Re-import this song before analysis.')
                length = min(duration, length)
                starts = sorted(set([float(i) for i in range(0, max(1, math.floor(length - WINDOW) + 1), STEP)]
                                    + [max(0., length - WINDOW)]))
                for number, a in enumerate(starts, 1):
                    progress(f'Checking drums and voices · passage {number} of {len(starts)}')
                    source.seek(round(a * RATE))
                    audio = source.read(min(round(WINDOW * RATE), round((length - a) * RATE)), dtype='float32', always_2d=True)
                    if not len(audio) or not np.isfinite(audio).all(): raise djmix.MixError('Invalid audio during passage analysis.')
                    energy = np.mean(audio.astype(np.float64) ** 2, axis=0)
                    mono = audio.mean(axis=1)
                    if np.mean(mono ** 2) < .25 * max(energy): mono = audio[:, int(np.argmax(energy))]
                    rms_db = 10 * math.log10(max(float(np.mean(energy)), 1e-12))
                    scores = dict.fromkeys(GROUPS, 0.)
                    if rms_db >= -65:
                        padded = np.pad(mono, (0, max(0, RATE * WINDOW - len(mono))))
                        values = model(torch.from_numpy(padded.copy())[None, :])[0].numpy()
                        if not np.isfinite(values).all(): raise djmix.MixError('The passage detector returned invalid observations.')
                        scores = {name: float(np.max(values[list(indices)])) for name, indices in GROUPS.items()}
                    windows.append({'start': a, 'end': a + len(audio) / RATE, 'scores': scores,
                                    'rms_db': rms_db, 'state': state(scores, rms_db)})
            result = {'version': VERSION, 'model_sha256': MODEL_SHA256, 'duration': duration, 'windows': windows}
            if not valid_result(result, duration): raise djmix.MixError('The passage observations could not be validated.')
            if source_signature(path) != signature: raise djmix.MixError('The song changed during analysis. Analyze it again.')
            progress('Passage candidates ready to review')
            stage = work / 'observations.json'; stage.write_text(json.dumps({'key': key, 'analysis': result}, allow_nan=False))
            os.replace(stage, destination)
            return {**result, 'source': signature}
    finally: torch.set_num_threads(prior_threads)


def drum_regions(result):
    """Keep only intervals where all covering observations agree; never bridge a voice/unknown window."""
    windows = result.get('windows', [])
    edges = sorted({w[k] for w in windows for k in ('start', 'end')})
    regions = []
    for a, b in zip(edges, edges[1:]):
        covering = [i for i, w in enumerate(windows) if w['start'] < b and w['end'] > a]
        if not covering or any(windows[i]['state'] != 'drum_led' for i in covering): continue
        if regions and abs(regions[-1]['end'] - a) < 1e-6:
            regions[-1]['end'] = b; regions[-1]['observations'].update(covering)
        else: regions.append({'start': a, 'end': b, 'observations': set(covering)})
    return [{**r, 'observations': len(r['observations'])} for r in regions
            if len(r['observations']) >= 2 and r['end'] - r['start'] >= 5]


def voice_regions(result, *, threshold=VOICE_PROMPT_THRESHOLD):
    """Tentative listening ranges, never precise vocal boundaries or absence claims.

    Merge only overlapping/touching positive windows. A single positive window
    can prompt a listen, but uncertainty or an observation gap cannot join two
    separated candidates. Other windows may disagree inside a candidate. Use
    saved scores so this policy improvement needs no new inference. The optional
    cutoff lets offline measurements reproduce the former .2 prompt policy.
    """
    if not math.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError('Voice prompt cutoff must be between zero and one.')
    regions = []
    for window in result.get('windows', []):
        if window['rms_db'] < -65 or window['scores']['voice'] < threshold:
            continue
        start, end = window['start'], window['end']
        if regions and start <= regions[-1]['end']:
            regions[-1]['end'] = max(regions[-1]['end'], end)
            regions[-1]['observations'] += 1
        else:
            regions.append({'start': start, 'end': end, 'observations': 1})
    return regions


def cue_candidates(session, index, result):
    """Explicit cue/overlap drafts that fit entirely inside an observed passage."""
    from studio_beat_grid import display_grid
    from studio_engine import layout
    from studio_timing import corrected_time
    clip = session.clips[index]; track = session.tracks[clip.track_id]
    grid = display_grid(track)
    bars = grid.get('bars', [])
    candidates = []
    for region in drum_regions(result):
        within = [b for b in bars if b['start'] >= region['start'] and b['end'] <= region['end'] + .001]
        if not within: continue
        a, b = within[0]['start'], within[-1]['end']
        # Require a continuous grid, not two isolated bars separated by an unverified gap.
        if any(abs(x['end'] - y['start']) > .05 for x, y in zip(within, within[1:])): continue
        try: source_beats = (corrected_time(track, clip, b) - corrected_time(track, clip, a)) * track.bpm / 240
        except djmix.MixError: return []
        room = min(32, math.floor(source_beats + 1e-7))
        if room < 1: continue
        for kind, cue, pair in [('start', a, index), ('end', b, index + 1)]:
            if kind == 'start' and a > min(90, track.duration * .3): continue
            if kind == 'end' and b < max(track.duration - 90, track.duration * .65): continue
            draft = copy.deepcopy(session)
            setattr(draft.clips[index], 'cue_in' if kind == 'start' else 'cue_out', cue)
            overlap = 0
            if 0 < pair < len(session.clips):
                current = session.clips[pair].blend_bars
                overlap = current if current <= room else max(n for n in (1, 2, 4, 8, 16, 32) if n <= room)
            if overlap: draft.clips[pair].blend_bars = overlap
            try: layout(draft)
            except djmix.MixError: continue
            candidates.append({**region, 'kind': kind, 'time': cue, 'overlap': overlap, 'pair': pair,
                               'removed_seconds': cue if kind == 'start' else track.duration - cue})
    return candidates
