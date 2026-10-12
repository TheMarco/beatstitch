"""Reusable, fixed-tempo kick-check audio with live, reversible B offsets.

The beat check deliberately hears both sides before mix fades/bass handoff.
Applying an offset changes B's source cue through the existing timing map;
the normal renderer remains responsible for the final blend and export.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path
import tempfile

import numpy as np

import djmix
from studio_cache import AudioCache, source_signature
from studio_engine import layout
from studio_storage import managed_cache, require_space
from studio_timing import corrected_time, mapping, prepare as prepare_timing

RATE = djmix.SR


def alignment_identity(session, index):
    """Incoming cue changes can reuse this check; different audio cannot."""
    parts = []
    for i in (index - 1, index):
        clip = session.clips[i]; track = session.tracks[clip.track_id]
        points = mapping(track, clip)
        parts.append([clip.id, source_signature(track.path), track.bpm, clip.shift,
                      clip.cue_in if i != index else None, clip.cue_out,
                      points.tolist() if points is not None else None])
    return [session.target_bpm, session.clips[index].blend_bars, parts]


def offset_for_cue(session, index, original_cue):
    """Positive offset plays B later; negative offset plays B earlier."""
    clip = session.clips[index]; track = session.tracks[clip.track_id]
    return float((corrected_time(track, clip, original_cue) -
                  corrected_time(track, clip, clip.cue_in)) * track.bpm / session.target_bpm)


def cue_for_offset(session, index, original_cue, offset):
    """Invert the accepted drift map instead of assuming source seconds are uniform."""
    if not math.isfinite(offset):
        raise djmix.MixError('Choose a finite beat adjustment.')
    clip = session.clips[index]; track = session.tracks[clip.track_id]
    if offset == 0:
        return float(original_cue)
    corrected = corrected_time(track, clip, original_cue) - offset * session.target_bpm / track.bpm
    if not 0 <= corrected < corrected_time(track, clip, clip.cue_out):
        raise djmix.MixError('This move reaches the beginning or ending of the available song audio.')
    points = mapping(track, clip)
    return float(corrected if points is None else np.interp(corrected, points[:, 1], points[:, 0]))


@managed_cache
def prepare_alignment(session, index, cache_dir, progress=lambda message: None):
    """Prepare only the overlap plus one bar of handles; reuse transformed source bands.

Uses the shared reference BPM even when a set tempo curve is enabled. Once
reference beats coincide, the shared tempo curve carries both together.
"""
    positions = layout(session)
    if not 0 < index < len(positions):
        raise djmix.MixError('Choose a blend between two songs.')
    identity = alignment_identity(session, index)
    incoming = positions[index]
    beat = 60 / session.target_bpm
    margin = round(4 * beat * RATE)
    # Center a bounded whole-bar excerpt inside the actual overlap.
    bars = min(8, session.clips[index].blend_bars, max(1, int(30 / (4 * beat))))
    frames = round(bars * 4 * beat * RATE)
    start = incoming['start'] + (incoming['incoming'] - frames) // 2
    left, right = start - margin, start + frames + margin
    length = right - left
    cache = AudioCache(cache_dir)
    arrays = []
    require_space(Path(cache_dir), length * 64, 'prepare the beat alignment check')
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='beat-check-', dir=cache_dir) as directory:
        work = Path(directory)
        for i in (index - 1, index):
            clip = session.clips[i]; track = session.tracks[clip.track_id]
            progress(f'Preparing beat-check audio · {track.name}')
            ratio = track.bpm / session.target_bpm
            base = corrected_time(track, clip, clip.cue_in)
            points = mapping(track, clip)
            if clip.stabilize and track.timing.get('source') != source_signature(track.path):
                raise djmix.MixError(f'{track.name} changed. Analyze its timing again.')
            corrected = base + np.array([left - positions[i]['start'], right - positions[i]['start']]) / RATE / ratio
            corrected = np.clip(corrected, 0, track.duration)
            source = corrected if points is None else np.interp(corrected, points[:, 1], points[:, 0])
            local_a, local_b = np.rint((corrected - base) * ratio * RATE).astype(int)
            first = positions[i]['start'] + local_a - left
            low_buffer = np.zeros((length, 2), dtype=np.float32)
            full_buffer = np.zeros_like(low_buffer)
            if local_b > local_a:
                gain = min(12, -18 - cache.measurement(Path(track.path))['lufs'])
                family = cache.family(track, clip, session.target_bpm, gain)
                cached = cache.get(family, int(local_a), int(local_b))
                if cached:
                    progress(f'Reusing beat-check audio · {track.name}')
                    low, high = (djmix.raw_audio(p) for p in cached[:2]); offset = cached[2]
                else:
                    if points is None:
                        raw = djmix.prepare(Path(track.path), {'start': float(source[0]), 'end': float(source[1]),
                                            'frames': int(local_b - local_a)}, track.duration, track.bpm,
                                            session.target_bpm, clip.shift, True, work, str(i), gain)
                    else:
                        extended = copy.deepcopy(clip)
                        extended.cue_in, extended.cue_out = map(float, source)
                        raw = prepare_timing(track, extended, session.target_bpm, work, str(i), gain)
                    low, high = djmix.split_bands(raw, work, str(i))
                    if len(low) != local_b - local_a:
                        low._mmap.close(); high._mmap.close()
                        raise djmix.MixError('Beat-check audio length changed unexpectedly.')
                    cached_dir = cache.put(family, int(local_a), int(local_b), Path(low.filename), Path(high.filename))
                    low._mmap.close(); high._mmap.close()
                    low, high = (djmix.raw_audio(cached_dir / name) for name in ('low.raw', 'high.raw')); offset = 0
                try:
                    a = max(0, int(first)); b = min(length, int(first + local_b - local_a))
                    if b > a:
                        selection = slice(int(offset + a - first), int(offset + b - first))
                        low_buffer[a:b] = low[selection]
                        full_buffer[a:b] = low[selection] + high[selection]
                finally:
                    low._mmap.close(); high._mmap.close()
            arrays.append({'low': low_buffer, 'full': full_buffer})
    if identity != alignment_identity(session, index):
        raise djmix.MixError('A source changed during the beat check. Reopen this blend.')
    # A fixed shared gain remains safe for every live offset/solo combination.
    maximum = sum(float(np.max(abs(side['full']))) for side in arrays)
    gain = min(1., .8 / max(maximum, 1e-12))
    for side in arrays:
        for audio in side.values(): audio *= gain
    cache.prune()
    return {'sides': arrays, 'margin': margin, 'frames': frames, 'start': start / RATE,
            'bpm': session.target_bpm, 'cue': session.clips[index].cue_in, 'identity': identity,
            'intro_percussive': session.clips[index].intro_percussive}


class AlignmentMixer:
    """Loop one check while offset, kick focus and solo change without DSP reruns."""
    WEIGHTS = {'outgoing': (1., 0.), 'blend': (1., 1.), 'incoming': (0., 1.)}

    def __init__(self, report):
        self.sides = report['sides']
        self.margin, self.length = int(report['margin']), int(report['frames'])
        if (self.length <= 0 or self.margin <= 0 or len(self.sides) != 2 or any(
                x.shape != (self.length + 2 * self.margin, 2) or not np.isfinite(x).all()
                for side in self.sides for x in side.values())):
            raise ValueError('Beat-check audio needs finite, padded stereo samples.')
        self.frame = 0
        self.offset = self.old_offset = 0.
        self.offset_fade = 960
        self.focus = self.target_focus = 1.
        self.gains = self.target_gains = (1., 1.)

    @property
    def frames(self): return math.inf

    def seek(self, frame): self.frame = max(0, int(frame))

    def select(self, variant): self.target_gains = self.WEIGHTS[variant]

    def set_focus(self, enabled): self.target_focus = float(bool(enabled))

    def set_offset(self, seconds):
        frames = float(seconds) * RATE
        if not math.isfinite(frames) or abs(frames) > self.margin:
            raise ValueError('The beat adjustment exceeds the prepared audio handles.')
        if frames != self.offset:
            self.old_offset, self.offset = self.offset, frames
            self.offset_fade = 0

    @staticmethod
    def sample(audio, indices):
        indices = np.clip(indices, 0, len(audio) - 1)
        left = np.floor(indices).astype(int); right = np.minimum(left + 1, len(audio) - 1)
        fraction = (indices - left)[:, None]
        return audio[left] * (1 - fraction) + audio[right] * fraction

    def read(self, frames):
        from studio_ab_audio import ABMixer
        count = int(frames)
        if count <= 0: return b''
        indices = np.arange(self.frame, self.frame + count) % self.length
        focus = ABMixer.ramp(self.focus, self.target_focus, count)
        fade = np.clip((self.offset_fade + np.arange(1, count + 1)) / 960., 0, 1)
        chunk = np.zeros((count, 2))
        for i, side in enumerate(self.sides):
            def portion(offset):
                n = indices + self.margin - offset
                full = self.sample(side['full'], n); low = self.sample(side['low'], n)
                return full * (1 - focus[:, None]) + low * focus[:, None]
            audio = portion(0 if i == 0 else self.offset)
            if i and self.offset_fade < 960:
                audio = portion(self.old_offset) * (1 - fade[:, None]) + audio * fade[:, None]
            weights = ABMixer.ramp(self.gains[i], self.target_gains[i], count)
            chunk += audio * weights[:, None]
        # Safety fades at loop edges, rather than clicks at an arbitrary cut.
        edge = np.minimum(np.clip(indices / 240., 0, 1), np.clip((self.length - 1 - indices) / 240., 0, 1))
        chunk *= edge[:, None]
        self.frame += count; self.offset_fade += count
        self.focus = float(focus[-1])
        self.gains = tuple(float(ABMixer.ramp(g, t, count)[-1]) for g, t in zip(self.gains, self.target_gains))
        return np.asarray(chunk, dtype='<f4').tobytes()


from studio_ab_audio import ABTransport


class AlignmentTransport(ABTransport):
    def load_report(self, report):
        self._set_playing(False); self._reset()
        self.mixer = AlignmentMixer(report)
        self.origin, self.last_reported = 0, -100
        self.positionChanged.emit(0)

    def position(self):
        if self.mixer is None: return 0
        return round((self.position_frame() % self.mixer.length) * 1000 / RATE)

    def set_offset(self, seconds):
        if self.mixer: self.mixer.set_offset(seconds)

    def set_focus(self, enabled):
        if self.mixer: self.mixer.set_focus(enabled)
