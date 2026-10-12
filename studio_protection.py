"""User-marked source passages that must stay complete and outside blends."""
import copy
import math

import djmix
from studio_timing import corrected_time

KINDS = {'passage': 'Passage', 'vocal': 'Vocal', 'build': 'Build', 'drop': 'Drop', 'favorite': 'Favorite'}
# Separate rounded endpoints can differ by one sample from a rounded overlap.
# This admits a touching musical boundary, never an extra audible interval.
BOUNDARY_TOLERANCE = 1


def validate_moments(track, clip):
    moments = clip.protected_moments
    if not isinstance(moments, list) or len(moments) > 64:
        raise djmix.MixError('A song can have up to 64 protected passages.')
    for moment in moments:
        if not isinstance(moment, dict):
            raise djmix.MixError('Invalid protected passage.')
        start, end = moment.get('start'), moment.get('end')
        label, kind = moment.get('label'), moment.get('kind')
        if (type(start) not in (int, float) or type(end) not in (int, float) or
                not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= track.duration):
            raise djmix.MixError(f'A protected passage in {track.name} needs a start before its end, within the source song.')
        if not isinstance(label, str) or not label.strip() or len(label) > 160 or not isinstance(kind, str) or kind not in KINDS:
            raise djmix.MixError('Give the protected passage a short name and a valid passage type.')
    return moments


def protection_limits(track, clip, tempo):
    """Maximum incoming/outgoing overlap frames, including tempo stabilization."""
    moments = validate_moments(track, clip)
    if not moments:
        return {'incoming': None, 'outgoing': None}
    for moment in moments:
        if moment['start'] < clip.cue_in or moment['end'] > clip.cue_out:
            raise djmix.MixError(f'Keep the whole protected passage “{moment["label"]}” in {track.name}. '
                                 'Widen Start/End, or remove that protection if you intend to leave it out.')
    first = min(moments, key=lambda m: m['start'])
    last = max(moments, key=lambda m: m['end'])
    scale = track.bpm / tempo * djmix.SR
    origin = corrected_time(track, clip, clip.cue_in)
    offset = lambda t: round((corrected_time(track, clip, t) - origin) * scale)
    length = offset(clip.cue_out)
    return {'incoming': (offset(first['start']), first['label']),
            'outgoing': (length - offset(last['end']), last['label'])}


def validate_protection(session, positions):
    for i, (clip, pos) in enumerate(zip(session.clips, positions)):
        track = session.tracks[clip.track_id]
        limits = protection_limits(track, clip, session.target_bpm)
        for side, frames, pair in (('incoming', pos['incoming'], i),
                                   ('outgoing', positions[i + 1]['incoming'] if i + 1 < len(positions) else 0, i + 1)):
            constraint = limits[side]
            if constraint is not None and frames > constraint[0] + BOUNDARY_TOLERANCE:
                raise djmix.MixError(f'Blend {pair} → {pair + 1} covers protected “{constraint[1]}” in {track.name}. '
                                     'Shorten that blend or move the song’s cues. Remove the protection only if intended.')


def fit_protected_overlaps(session, index):
    """Return a draft with only the two adjacent blends shortened as needed."""
    from studio_engine import layout, overlap_limit
    draft = copy.deepcopy(session)
    # Check kept ranges even for an opener/closer without a neighboring blend.
    for clip in draft.clips:
        protection_limits(draft.tracks[clip.track_id], clip, draft.target_bpm)
    changes = []
    for pair in (index, index + 1):
        if not 0 < pair < len(draft.clips):
            continue
        limit = overlap_limit(draft, pair, positions=layout(draft, check_protection=False))
        if limit['bars'] < 1:
            raise djmix.MixError(f'Blend {pair} → {pair + 1} has no room for even one bar outside a protected passage. '
                                 'Widen the source cues, choose another position, or remove the protection.')
        clip = draft.clips[pair]
        if clip.blend_bars > limit['bars']:
            changes.append(f'{pair} → {pair + 1}: {clip.blend_bars} → {limit["bars"]} bars')
            clip.blend_bars = limit['bars']
    layout(draft)
    return draft, changes
