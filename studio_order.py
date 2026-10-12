"""Reviewable song-order planning. Scores are preferences, never quality ratings."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import math

import djmix
from studio_engine import layout
from studio_recommendations import _key, transition_harmony


PACES = {'smooth': 'Keep energy changes gentle', 'build': 'Build energy gradually',
         'peak': 'Build to a peak, then ease down'}


@dataclass
class OrderOptions:
    pace: str = 'smooth'
    opener: str = ''
    closer: str = ''
    target_seconds: float | None = None
    allow_omissions: bool = False
    auto_energy: bool = True

    def validate(self, session):
        ids = {c.id for c in session.clips}
        if self.pace not in PACES:
            raise djmix.MixError('Choose an energy direction.')
        if type(self.auto_energy) is not bool:
            raise djmix.MixError('Automatic energy estimates must be on or off.')
        if any(identity and identity not in ids for identity in (self.opener, self.closer)):
            raise djmix.MixError('An opener or closer is no longer in the mix. Choose it again.')
        if self.opener and self.opener == self.closer:
            raise djmix.MixError('Choose different songs for the opener and closer.')
        if self.opener:
            first = session.clips[0]
            chosen = next(c for c in session.clips if c.id == self.opener)
            if first.order_locked and chosen.id != first.id:
                raise djmix.MixError(f'Position 1 is locked to {session.tracks[first.track_id].name}. '
                                     'Release that lock or choose it as the opener.')
            if chosen.order_locked and chosen.id != first.id:
                place = session.clips.index(chosen) + 1
                raise djmix.MixError(f'{session.tracks[chosen.track_id].name} is locked at position {place}. '
                                     'Release its position lock before making it the opener.')
        if self.closer and not self.allow_omissions:
            last = session.clips[-1]
            chosen = next(c for c in session.clips if c.id == self.closer)
            if (last.order_locked and chosen.id != last.id) or (chosen.order_locked and chosen.id != last.id):
                raise djmix.MixError('The closer conflicts with a fixed position. Release that position lock or choose another closer.')
        if self.target_seconds is not None and (not math.isfinite(self.target_seconds) or self.target_seconds <= 0):
            raise djmix.MixError('Enter a positive target duration.')
        if self.allow_omissions and self.target_seconds is None:
            raise djmix.MixError('Set a target duration before allowing songs to be left out.')


def default_options(session):
    values = {'opener': session.clips[0].id if session.clips else '', **session.planning}
    return OrderOptions(**{k: v for k, v in values.items() if k in OrderOptions.__dataclass_fields__})


def _fixed(session, options, count):
    fixed = {i: c.id for i, c in enumerate(session.clips) if c.order_locked}
    if fixed and max(fixed) >= count:
        return None
    for index, identity in ((0, options.opener), (count - 1, options.closer)):
        if not identity:
            continue
        if index in fixed and fixed[index] != identity:
            return None
        if identity in fixed.values() and fixed.get(index) != identity:
            return None
        fixed[index] = identity
    return fixed


def _safe_labels(original, ordered):
    """A beats-only label applies only within the passage the user marked."""
    previous = layout(original)
    original_index = {c.id: i for i, c in enumerate(original.clips)}
    changes = []
    for i, clip in enumerate(ordered.clips):
        old = original_index[clip.id]
        if i and clip.intro_percussive and previous[old]['incoming'] == 0:
            clip.intro_percussive = False
            changes.append(f'{original.tracks[clip.track_id].name}: cleared drum-intro label for the newly used incoming overlap.')
        if i + 1 < len(ordered.clips) and clip.outro_percussive:
            old_bars = original.clips[old + 1].blend_bars if old + 1 < len(original.clips) else 0
            if ordered.clips[i + 1].blend_bars > old_bars:
                clip.outro_percussive = False
                changes.append(f'{original.tracks[clip.track_id].name}: cleared drum-outro label because the outgoing overlap is longer.')
    return changes


def draft_for_order(session, identities, options):
    options.validate(session)
    by_id = {c.id: c for c in session.clips}
    if len(identities) < 2 or len(set(identities)) != len(identities) or not set(identities) <= set(by_id):
        raise djmix.MixError('The proposed order must contain at least two different clips from this mix.')
    if not options.allow_omissions and set(identities) != set(by_id):
        raise djmix.MixError('This plan must use every song. Enable leaving songs out to suggest a shorter set.')
    if any(c.protected_moments and c.id not in identities for c in session.clips):
        raise djmix.MixError('Songs with protected passages must stay in the plan. Remove their protection before leaving them out.')
    fixed = _fixed(session, options, len(identities))
    if fixed is None or any(identities[i] != identity for i, identity in fixed.items()):
        raise djmix.MixError('The proposed order conflicts with a fixed position, opener or closer.')
    draft = copy.deepcopy(session)
    draft.clips = [copy.deepcopy(by_id[i]) for i in identities]
    draft.planning = asdict(options)
    label_changes = _safe_labels(session, draft)
    layout(draft)
    return draft, label_changes


def propose_order(session, options=None, progress=lambda _: None, beam_width=192, energy_estimates=None):
    """Bounded beam search across complete valid arrangements, including optional subsets.

    Cues, tempo, pitch, overlap lengths and audio stay under user control. The
    returned duration is exact, while the search is a suggestion, not an optimum proof.
    """
    options = options or default_options(session)
    options.validate(session)
    positions = layout(session)
    from studio_tempo_curve import session_curve
    curve = session_curve(session)
    if len(positions) < 2:
        raise djmix.MixError('Add at least two songs before planning their order.')
    clips = {c.id: c for c in session.clips}
    original = list(clips)
    number = {identity: i for i, identity in enumerate(original)}
    lengths = {p['clip_id']: p['length'] / djmix.SR for p in positions}
    overlaps = {c.id: round(c.blend_bars * 240 / session.target_bpm * djmix.SR) / djmix.SR for c in session.clips}
    length_frames = {p['clip_id']: p['length'] for p in positions}
    overlap_frames = {c.id: round(c.blend_bars * 240 / session.target_bpm * djmix.SR) for c in session.clips}
    from studio_protection import protection_limits, BOUNDARY_TOLERANCE
    protected = {c.id for c in session.clips if c.protected_moments}
    limits = {c.id: protection_limits(session.tracks[c.track_id], c, session.target_bpm) for c in session.clips}
    adjusted_total = sum(lengths[x] - overlaps[x] for x in original)
    energy_estimates = energy_estimates or {}
    energy_info = {}
    for clip in session.clips:
        automatic = energy_estimates.get(clip.id, {}) if options.auto_energy else {}
        value = automatic.get('value')
        if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 5:
            value = None
        energy_info[clip.id] = (
            {'value': clip.energy, 'origin': 'manual', 'reason': 'Your rating overrides automatic analysis.'}
            if clip.energy is not None else
            {'value': value, 'origin': 'estimated' if value is not None else 'unknown',
             'reason': automatic.get('reason', 'No energy estimate. Use a personal rating, or enable automatic estimates.')})
    ratings = {identity: info['value'] for identity, info in energy_info.items()}
    strength = {identity: 1 if info['origin'] == 'manual' else .5 for identity, info in energy_info.items()}
    known = [v for v in ratings.values() if v is not None]
    low, high = (min(known), max(known)) if known else (3, 3)
    keys = {c.id: djmix.transpose(_key(session.tracks[c.track_id].key), c.shift) for c in session.clips}
    old_incoming = {p['clip_id']: p['incoming'] for p in positions}
    old_outro_bars = {c.id: session.clips[i + 1].blend_bars if i + 1 < len(session.clips) else 0
                     for i, c in enumerate(session.clips)}
    edges = {}
    for a in original:
        for b in original:
            if a == b:
                continue
            # Unknown keys receive an uncertainty cost, not a claim of a clash.
            ka, kb = keys[a], keys[b]
            marked_drums = ((clips[b].intro_percussive and old_incoming[b] > 0) or
                            (clips[a].outro_percussive and overlaps[b] <= old_outro_bars[a] * 240 / session.target_bpm))
            harmony = 0 if marked_drums or (ka and kb and djmix.relationship(ka, kb)) else 1 if not ka or not kb else 2
            tempo = abs(math.log2(session.tracks[clips[b].track_id].bpm / session.tracks[clips[a].track_id].bpm))
            energy = abs(ratings[a] - ratings[b]) if ratings[a] is not None and ratings[b] is not None else 0
            edges[a, b] = 3 * harmony + 2 * tempo + .45 * energy ** 2 * min(strength[a], strength[b])

    def node_cost(identity, index, count):
        energy = ratings[identity]
        if energy is None or options.pace == 'smooth':
            return 0
        phase = index / max(1, count - 1)
        target = low + (high - low) * (phase if options.pace == 'build' else 1 - abs(2 * phase - 1))
        return 1.4 * (energy - target) ** 2 * strength[identity]

    def duration_cost(duration):
        if options.target_seconds is None:
            return 0
        duration = float(curve.to_output(duration))
        return 4 * abs(duration - options.target_seconds) / max(30, .05 * options.target_seconds)

    def total_cost(order, duration):
        return (sum(edges[a, b] for a, b in zip(order, order[1:]))
                + sum(node_cost(identity, i, len(order)) for i, identity in enumerate(order)) + duration_cost(duration))

    counts = range(2, len(original) + 1) if options.allow_omissions else [len(original)]
    candidates = []
    for count in counts:
        if count < len(protected):
            continue
        fixed = _fixed(session, options, count)
        if fixed is None:
            continue
        reserved = set(fixed.values())
        states = [(0., (), 0.)]
        for slot in range(count):
            progress(f'Planning {count} songs: position {slot + 1} of {count}')
            following = []
            for cost, order, duration in states:
                used = set(order)
                choices = [fixed[slot]] if slot in fixed else [x for x in original if x not in reserved and x not in used]
                for identity in choices:
                    if identity in used:
                        continue
                    incoming = overlaps[identity] if order else 0
                    incoming_frames = overlap_frames[identity] if order else 0
                    if incoming_frames >= length_frames[identity]:
                        continue
                    intro = limits[identity]['incoming']
                    if intro is not None and incoming_frames > intro[0] + BOUNDARY_TOLERANCE:
                        continue
                    if order and incoming_frames + (overlap_frames[order[-1]] if len(order) > 1 else 0) >= length_frames[order[-1]]:
                        continue
                    outro = limits[order[-1]]['outgoing'] if order else None
                    if outro is not None and incoming_frames > outro[0] + BOUNDARY_TOLERANCE:
                        continue
                    new_cost = cost + node_cost(identity, slot, count) + (edges[order[-1], identity] if order else 0)
                    new_duration = duration + lengths[identity] - incoming
                    new_order = order + (identity,)
                    if len(protected.difference(new_order)) > count - len(new_order):
                        continue
                    remaining = len(original) - len(new_order)
                    remaining_average = (adjusted_total - new_duration + overlaps[new_order[0]]) / remaining if remaining else 0
                    estimate = new_duration + (count - slot - 1) * remaining_average
                    following.append((new_cost + duration_cost(estimate), new_cost, new_order, new_duration))
            following.sort(key=lambda v: (v[0], tuple(number[x] for x in v[2])))
            states = [(cost, order, duration) for _, cost, order, duration in following[:max(1, beam_width)]]
            if not states:
                break
        for cost, order, duration in states:
            if len(order) == count:
                candidates.append((cost + duration_cost(duration), order))
    # Always consider the original complete arrangement if it meets the selected locks.
    fixed = _fixed(session, options, len(original))
    if fixed is not None and all(original[i] == identity for i, identity in fixed.items()):
        candidates.append((total_cost(original, positions[-1]['end'] / djmix.SR), tuple(original)))
    if not candidates:
        raise djmix.MixError('No valid order fits those locks, protected passages and overlaps. Change an opener/closer, '
                             'shorten an overlap, or review the protections before trying again.')
    candidates.sort(key=lambda item: (item[0], sum(a != b for a, b in zip(item[1], original)), tuple(number[x] for x in item[1])))
    _, order = candidates[0]
    draft, label_changes = draft_for_order(session, order, options)
    duration = float(curve.to_output(layout(draft)[-1]['end'] / djmix.SR))
    return {'draft': draft, 'order': list(order), 'duration': duration,
            'current_duration': float(curve.to_output(positions[-1]['end'] / djmix.SR)),
            'target_difference': duration - options.target_seconds if options.target_seconds is not None else None,
            'omitted': [identity for identity in original if identity not in order],
            'moved': sum(number[identity] != i for i, identity in enumerate(order)),
            'rated': sum(info['origin'] == 'manual' for info in energy_info.values()),
            'estimated': sum(info['origin'] == 'estimated' for info in energy_info.values()),
            'energy': energy_info, 'label_changes': label_changes,
            'transitions': [transition_harmony(draft, i) for i in range(1, len(order))]}
