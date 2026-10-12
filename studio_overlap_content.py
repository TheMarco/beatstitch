"""Tentative voice collisions on the rendered timeline, with reviewable cue choices.

Source-model windows are coarse evidence, never proof of actual competing vocals
or a vocal-free passage. Pitch changes do not turn a voice observation into silence.
"""
import copy

import djmix
from studio_beat_grid import display_grid
from studio_content import analyze_content, current_content, voice_regions
from studio_engine import layout
from studio_timing import corrected_time
from studio_transition_effects import echo_timing


def _merge(ranges):
    result = []
    for a, b in sorted(ranges):
        if b <= a:
            continue
        if result and a <= result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result


def _voice_frames(session, index, positions, content, left, right):
    clip = session.clips[index]; track = session.tracks[clip.track_id]
    origin = corrected_time(track, clip, clip.cue_in)
    scale = track.bpm / session.target_bpm * djmix.SR
    def frame(seconds):
        return positions[index]['start'] + round((corrected_time(track, clip, seconds) - origin) * scale)
    return _merge((max(left, frame(max(clip.cue_in, v['start']))),
                   min(right, frame(min(clip.cue_out, v['end']))))
                  for v in voice_regions(content)
                  if v['end'] > clip.cue_in and v['start'] < clip.cue_out)


def _report(session, index, contents, positions=None):
    positions = layout(session) if positions is None else positions
    left, right = positions[index]['start'], positions[index - 1]['end']
    outgoing, incoming = [_voice_frames(session, i, positions, content, left, right)
                          for i, content in zip((index - 1, index), contents)]
    if session.clips[index].transition_effect == 'echo':
        delay, cut, send_start = echo_timing(right - left, session.target_bpm)
        cut += left; send_start += left
        dry = [(a, min(b, cut)) for a, b in outgoing]
        repeats = [(max(a, send_start) + tap * delay, min(b, cut) + tap * delay)
                   for a, b in outgoing if a < cut and b > send_start for tap in range(1, 5)]
        outgoing = _merge(dry + repeats)
    collisions = _merge((max(a, c), min(b, d)) for a, b in outgoing for c, d in incoming)
    from studio_tempo_curve import session_curve
    curve = session_curve(session)
    collisions = [curve.output_span(a, b) for a, b in collisions]
    left, right = curve.output_span(left, right)
    return {'state': 'possible_conflict' if collisions else 'not_flagged' if all(contents) else 'not_checked',
            'checked': [bool(c) for c in contents],
            'seconds': sum(b - a for a, b in collisions) / djmix.SR,
            'ranges': [{'start': (a - left) / djmix.SR, 'end': (b - left) / djmix.SR} for a, b in collisions],
            'overlap_seconds': (right - left) / djmix.SR}


def overlap_voices(session, index):
    if not 0 < index < len(session.clips):
        raise djmix.MixError('Select a transition between two songs.')
    contents = [current_content(session.tracks[c.track_id]) for c in session.clips[index - 1:index + 1]]
    return _report(session, index, contents)


def review_voice_overlaps(session):
    """Read saved evidence for every blend without running inference.

    Validate the layout once and each used source once, including repeated clips.
    """
    if len(session.clips) < 2:
        return {}
    positions = layout(session)
    contents = {identity: current_content(session.tracks[identity])
                for identity in {c.track_id for c in session.clips}}
    return {session.clips[i].id: _report(session, i, [contents[c.track_id] for c in session.clips[i - 1:i + 1]], positions)
            for i in range(1, len(session.clips))}


def analyze_pair_voices(session, index, cache_dir, progress=lambda _: None):
    results = {}
    for clip in session.clips[index - 1:index + 1]:
        track = session.tracks[clip.track_id]
        if track.id in results:
            continue
        result = current_content(track)
        progress(('Reusing voice observations: ' if result else 'Checking voices: ') + track.name)
        results[track.id] = result or analyze_content(track.path, track.duration, cache_dir, progress)
    return results


def voice_advice(result):
    if result['state'] == 'not_checked':
        return 'Voices not fully checked. Check both songs, then solo each side of the blend to listen.'
    if result['state'] == 'possible_conflict':
        return (f'Possible voices together for about {result["seconds"]:.1f} seconds of this blend. '
                'Listen for competing words or melodies; try another incoming start or a shorter overlap.')
    return ('No simultaneous voice-like passages flagged in the available observations. '
            'Vocals or melodies may still compete; this does not prove a vocal-free blend.')


def vocal_cue_choices(session, index, limit=3):
    """Nearby supported bar starts with less observed voice overlap, not guarantees.

    Keep the outgoing song, incoming ending and overlap length. Full layout
    validation checks both neighbors, protection and the remaining audio.
    """
    if not 0 < index < len(session.clips):
        return []
    contents = [current_content(session.tracks[c.track_id]) for c in session.clips[index - 1:index + 1]]
    before = _report(session, index, contents)
    if before['state'] != 'possible_conflict':
        return []
    clip = session.clips[index]; track = session.tracks[clip.track_id]
    grid = display_grid(track)
    # Limit both musical displacement and work for long songs.
    radius = 32 * 240 / track.bpm
    starts = sorted({b['start'] for b in grid.get('bars', [])
                     if 0 <= b['start'] < clip.cue_out and 1 / djmix.SR < abs(b['start'] - clip.cue_in) <= radius},
                    key=lambda t: abs(t - clip.cue_in))[:128]
    choices = []
    original_end = layout(session)[-1]['end']
    from studio_tempo_curve import session_curve
    curve = session_curve(session)
    for start in starts:
        draft = copy.copy(session)
        draft.clips = list(session.clips)
        draft.clips[index] = copy.deepcopy(clip)
        draft.clips[index].cue_in = start
        draft.clips[index].intro_percussive = False
        try:
            result = _report(draft, index, contents)
        except djmix.MixError:
            continue
        if result['seconds'] >= before['seconds'] - .05:
            continue
        choices.append({'time': start, 'seconds': result['seconds'],
                        'source_delta': start - clip.cue_in,
                        'mix_delta': float(curve.to_output(layout(draft)[-1]['end'] / djmix.SR) - curve.to_output(original_end / djmix.SR))})
    choices.sort(key=lambda c: (c['seconds'], abs(c['source_delta'])))
    return choices[:limit]
