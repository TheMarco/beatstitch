"""Musical mix-edge envelopes, independent of source stretching and blend caches."""
import numpy as np

import djmix
from studio_protection import BOUNDARY_TOLERANCE, protection_limits

VERSION = 1


def limits(session, positions, *, check_protection=True):
    if not positions:
        return {'in': (0, ''), 'out': (0, '')}
    total = positions[-1]['end']
    first = positions[1]['start'] if len(positions) > 1 else total
    last = total - positions[-1]['start'] - positions[-1]['incoming']
    result = {}
    for side, available, clip, direction in [('in', first, session.clips[0], 'incoming'),
                                               ('out', last, session.clips[-1], 'outgoing')]:
        reason = 'before the first blend' if side == 'in' else 'after the last blend'
        if len(positions) == 1: reason = 'inside this song'
        if check_protection:
            protected = protection_limits(session.tracks[clip.track_id], clip, session.target_bpm)[direction]
            if protected and protected[0] < available:
                available = protected[0]; reason = f'outside protected “{protected[1]}”'
        bars = max((n for n in range(33) if round(n * 240 / session.target_bpm * djmix.SR)
                    <= available + BOUNDARY_TOLERANCE), default=0)
        result[side] = bars, reason
    return result


def validate(session, positions, *, check_protection=True):
    for value in (session.fade_in_bars, session.fade_out_bars):
        if type(value) is not int or not 0 <= value <= 32:
            raise djmix.MixError('Opening and ending fades must be whole bars from 0 (off) to 32.')
    if not positions: return
    available = limits(session, positions, check_protection=check_protection)
    for side, name, value in [('in', 'Fade-in', session.fade_in_bars), ('out', 'Fade-out', session.fade_out_bars)]:
        maximum, reason = available[side]
        if value > maximum:
            raise djmix.MixError(f'{name} has room for at most {maximum} bars {reason}. '
                                 'Open Start & ending to shorten the fade, or widen the first/last song’s cues.')
    if round((session.fade_in_bars + session.fade_out_bars) * 240 / session.target_bpm * djmix.SR) > positions[-1]['end']:
        raise djmix.MixError('The opening and ending fades overlap. Shorten one in Start & ending.')


def recipe(session, positions, start, end):
    """Only include edges that are audible inside this particular preview."""
    result = []
    opening = round(session.fade_in_bars * 240 / session.target_bpm * djmix.SR)
    ending = round(session.fade_out_bars * 240 / session.target_bpm * djmix.SR)
    total = positions[-1]['end']
    if opening and start < opening:
        result.append(['opening', VERSION, -start, opening])
    if ending and end > total - ending:
        result.append(['ending', VERSION, total - start, ending])
    return result


def envelope(session, positions, frames):
    gain = np.ones(len(frames))
    opening = round(session.fade_in_bars * 240 / session.target_bpm * djmix.SR)
    ending = round(session.fade_out_bars * 240 / session.target_bpm * djmix.SR)
    if opening:
        gain *= np.sin(np.clip(frames / max(1, opening - 1), 0, 1) * np.pi / 2) ** 2
    if ending:
        gain *= np.sin(np.clip((positions[-1]['end'] - 1 - frames) / max(1, ending - 1), 0, 1) * np.pi / 2) ** 2
    return gain


def preview_region(session, side):
    from studio_engine import layout
    positions = layout(session)
    if not positions: raise djmix.MixError('Add a song before listening to the opening or ending.')
    bars = session.fade_in_bars if side == 'in' else session.fade_out_bars
    context = round(max(8, bars + 4) * 240 / session.target_bpm * djmix.SR)
    total = positions[-1]['end']
    return (0, min(total, context)) if side == 'in' else (max(0, total - context), total)
