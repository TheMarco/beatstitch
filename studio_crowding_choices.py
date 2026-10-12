"""Bounded searches of rendered entrances, offered only as auditionable drafts."""
import copy
from pathlib import Path

import numpy as np
import soundfile as sf

import djmix
from studio_beat_grid import display_grid
from studio_cache import source_signature
from studio_engine import layout
from studio_overlap_content import overlap_voices
from studio_overlap_crowding import analyze_crowding
from studio_render_cache import audio_recipe
from studio_storage import managed_cache
from studio_transition_audio import pair_range, prepare_blend

SEARCH_LIMIT = 8


def choice_fingerprint(session, index):
    return audio_recipe(session, pair_range(session, index))['fingerprint']


def draft_for_cue(session, index, time):
    original = layout(session)
    draft = copy.deepcopy(session)
    draft.clips[index].cue_in = time
    draft.clips[index].intro_percussive = False
    positions = layout(draft)
    def clear_audio(points):
        return (points[index]['length'] - points[index]['incoming']
                - (points[index + 1]['incoming'] if index + 1 < len(points) else 0))
    # A mathematically valid entrance can leave almost nothing to hear between
    # blends. Preserve four clear bars, or the original clear length if shorter.
    minimum = min(clear_audio(original), round(4 * 240 / session.target_bpm * djmix.SR))
    if clear_audio(positions) < minimum:
        raise djmix.MixError('This suggested entrance would leave too little song outside its blends.')
    return draft


def cue_candidates(session, index):
    """Supported bar starts at spread-out offsets, never invented downbeats."""
    if not 0 < index < len(session.clips):
        return []
    clip = session.clips[index]; track = session.tracks[clip.track_id]
    bars = display_grid(track).get('bars', [])
    if not bars:
        return []
    anchor = min(bars, key=lambda b: abs(b['start'] - clip.cue_in))['number']
    by_number = {b['number']: b['start'] for b in bars}
    choices = []
    for offset in (0, -4, 4, -8, 8, -16, 16, -32, 32):
        time = by_number.get(anchor + offset)
        if time is None or abs(time - clip.cue_in) < 1 / djmix.SR:
            continue
        try:
            draft_for_cue(session, index, time)
        except djmix.MixError:
            continue
        choices.append(float(time))
        if len(choices) == SEARCH_LIMIT: break
    return choices


def audible_entry(session, index, report):
    """Do not win a search by fading into silence; drums can still qualify."""
    positions = layout(session)
    from studio_tempo_curve import preview_overlap
    left, right = preview_overlap(session, index, report['start'])
    audible, measured = 0, 0
    with sf.SoundFile(Path(report['folder']) / 'incoming.wav') as stream:
        if stream.samplerate != djmix.SR or stream.channels != 2 or left < 0 or right > stream.frames:
            raise djmix.MixError('The prepared entrance does not cover this blend.')
        stream.seek(left)
        while measured < right - left:
            audio = stream.read(min(djmix.SR // 2, right - left - measured), dtype='float32', always_2d=True)
            if not len(audio) or not np.isfinite(audio).all():
                raise djmix.MixError('The prepared entrance has invalid audio. Prepare it again.')
            if float(np.mean(audio.astype(np.float64) ** 2)) >= 1e-5:
                audible += len(audio)
            measured += len(audio)
    return bool(measured and audible >= .25 * measured)


def activity_evidence(result):
    """Continuous support beneath the hard warning threshold, not a quality score."""
    total = 0.
    for part in result['parts']:
        seconds = part['end'] - part['start']
        moving = [part[side]['coverage'] * min(1., part[side]['events'] / seconds)
                  * min(1., max(0., part[side]['notes'] - 1) / 2) for side in ('outgoing', 'incoming')]
        total += seconds * part['different_fraction'] * min(moving)
    return total


@managed_cache
def search_cues(session, index, cache_dir, progress=lambda _: None):
    """Same-length windows and rendering for baseline and every candidate.

No persisted project state is written. Partial cancelled searches do not publish
choices, while completely prepared audio/checks remain reusable on retry.
"""
    candidates = cue_candidates(session, index)
    if not candidates:
        return {'choices': [], 'checked': 0, 'reason': 'No valid nearby bar starts. Check beats in Song cues or keep your own entrance.'}
    paths = [session.tracks[c.track_id].path for c in session.clips[index - 1:index + 1]]
    signatures = [source_signature(p) for p in paths]
    fingerprint = choice_fingerprint(session, index)
    original_end = layout(session)[-1]['end']
    from studio_tempo_curve import session_curve
    curve = session_curve(session)
    baseline_report = prepare_blend(session, index, cache_dir, progress)
    baseline = analyze_crowding(session, index, baseline_report, cache_dir, progress)
    if baseline['state'] != 'possible_crowding':
        if signatures != [source_signature(p) for p in paths]:
            raise djmix.MixError('A source changed during the search. Reopen this blend.')
        return {'choices': [], 'checked': 0, 'baseline': baseline,
                'fingerprint': fingerprint,
                'reason': 'This draft has no flagged crowding to reduce. Listen to judge the entrance.'}
    voices = overlap_voices(session, index)
    before_activity = activity_evidence(baseline)
    choices, checked = [], 0
    for number, time in enumerate(candidates, 1):
        draft = draft_for_cue(session, index, time)
        observed = overlap_voices(draft, index)
        if voices['state'] != 'not_checked' and observed['seconds'] > voices['seconds'] + .05:
            continue
        def step(message):
            progress(f'Entrance {number} of {len(candidates)} · {message}')
        step('Preparing this bar-aligned start')
        report = prepare_blend(draft, index, cache_dir, step)
        checked += 1
        if not audible_entry(draft, index, report):
            continue
        result = analyze_crowding(draft, index, report, cache_dir, step)
        # The exact same overlap length gives comparable observation windows;
        # this measures evidence, not a musical-quality or confidence score.
        if result['state'] == 'uncertain' or result['seconds'] >= baseline['seconds'] - .05:
            continue
        # A threshold crossing alone (e.g. one extra note near a window edge)
        # must not masquerade as a substantial improvement. Remain conservative.
        if activity_evidence(result) >= before_activity - max(.5, before_activity * .15):
            continue
        choices.append({'time': time, 'source_delta': time - session.clips[index].cue_in,
                        'mix_delta': float(curve.to_output(layout(draft)[-1]['end'] / djmix.SR)
                                           - curve.to_output(original_end / djmix.SR)),
                        'before_seconds': baseline['seconds'], 'seconds': result['seconds'],
                        'analysis': result})
    progress('Entrance search complete')
    if signatures != [source_signature(p) for p in paths]:
        raise djmix.MixError('A source changed during the search. Reopen this blend.')
    choices.sort(key=lambda c: (c['seconds'], abs(c['source_delta'])))
    return {'choices': choices[:2], 'checked': checked, 'baseline': baseline, 'fingerprint': fingerprint,
            'reason': ('Less crowding was flagged at these entrances. This does not guarantee a better blend.' if choices else
                       'No entrance with less flagged crowding found in this limited search. Try a shorter blend or your own cue.')}
