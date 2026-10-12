"""A shared musical clock for optional gradual set-tempo changes.

Anchors are quarter-note beats and absolute BPM. BPM is linear in beat position;
elapsed seconds are its integral, not linear interpolation of endpoint times.
The existing fixed-tempo layout is the reference musical coordinate system.
"""
from __future__ import annotations

import math

import numpy as np

import djmix

VERSION = 1


def session_curve(session):
    """Validate saved controls even while the curve is switched off."""
    if type(session.tempo_enabled) is not bool:
        raise djmix.MixError('Gradual tempo must be on or off.')
    curve = TempoCurve(session.target_bpm, session.tempo_points)
    return curve if session.tempo_enabled else TempoCurve(session.target_bpm)


def output_positions(curve, positions):
    result = []
    for position in positions:
        start, end = curve.output_span(position['start'], position['end'])
        incoming_end = int(curve.output_frame(position['start'] + position['incoming']))
        result.append({**position, 'start': start, 'end': end, 'length': end - start,
                       'incoming': incoming_end - start})
    return result


def render_span(session, region):
    return session_curve(session).output_span(*region)


def preview_overlap(session, index, reference_origin):
    from studio_engine import layout
    positions = layout(session); curve = session_curve(session)
    a, b = curve.output_span(positions[index]['start'], positions[index - 1]['end'])
    origin = int(curve.output_frame(round(reference_origin * djmix.SR)))
    return a - origin, b - origin


def tempo_token(session):
    curve = session_curve(session)
    return None if curve.identity else ['set-tempo', VERSION, curve.points]


def clip_tempo_range(session, index, positions=None):
    if positions is None:
        from studio_engine import layout
        positions = layout(session)
    pos = positions[index]; curve = session_curve(session)
    a, b = pos['start'] / djmix.SR, pos['end'] / djmix.SR
    values = [float(curve.bpm_at(a)), float(curve.bpm_at(b))]
    values.extend(p['bpm'] for p in curve.points if a < p['beat'] * 60 / session.target_bpm < b)
    return min(values), max(values)


class TempoCurve:
    def __init__(self, reference_bpm, points=()):
        if (type(reference_bpm) not in (float, int) or not math.isfinite(reference_bpm)
                or not 20 <= reference_bpm <= 300):
            raise djmix.MixError('Choose a reference tempo from 20 to 300 BPM.')
        if not isinstance(points, (list, tuple)) or len(points) > 256:
            raise djmix.MixError('A tempo curve can contain up to 256 points.')
        self.reference_bpm = float(reference_bpm)
        self.points = []
        for item in points:
            if not isinstance(item, dict) or set(item) != {'beat', 'bpm'}:
                raise djmix.MixError('Each tempo point needs a beat position and BPM.')
            beat, bpm = item['beat'], item['bpm']
            if (any(type(v) not in (float, int) or not math.isfinite(v) for v in (beat, bpm))
                    or not 0 <= beat <= 10_000_000 or not 20 <= bpm <= 300
                    or (self.points and beat <= self.points[-1]['beat'])
                    or (not self.points and beat != 0)):
                raise djmix.MixError('Start the curve at beat zero, place later points in increasing order, and use 20–300 BPM.')
            self.points.append({'beat': float(beat), 'bpm': float(bpm)})
        if not self.points: self.points = [{'beat': 0., 'bpm': self.reference_bpm}]
        self.beats = np.array([p['beat'] for p in self.points], dtype=float)
        self.bpms = np.array([p['bpm'] for p in self.points], dtype=float)
        self.slopes = np.r_[np.diff(self.bpms) / np.diff(self.beats), 0.]
        times = [0.]
        for i, span in enumerate(np.diff(self.beats)):
            times.append(times[-1] + float(self._integral(span, self.bpms[i], self.slopes[i])))
        self.times = np.asarray(times)
        self.identity = bool(np.all(self.bpms == self.reference_bpm))

    @staticmethod
    def _integral(beats, bpm, slope):
        # log1p(x)/x remains well-conditioned for nearly constant segments.
        beats, bpm, slope = np.broadcast_arrays(beats, bpm, slope)
        x = slope * beats / bpm
        ratio = np.ones_like(x, dtype=float)
        np.divide(np.log1p(x), x, out=ratio, where=x != 0)
        return 60 * beats / bpm * ratio

    @staticmethod
    def _inverse(seconds, bpm, slope):
        seconds, bpm, slope = np.broadcast_arrays(seconds, bpm, slope)
        x = slope * seconds / 60
        ratio = np.ones_like(x, dtype=float)
        np.divide(np.expm1(x), x, out=ratio, where=x != 0)
        return seconds * bpm / 60 * ratio

    @staticmethod
    def _values(value):
        values = np.asarray(value, dtype=float)
        if not np.isfinite(values).all(): raise djmix.MixError('Playback positions must be finite.')
        return values

    def to_output(self, reference_seconds):
        value = self._values(reference_seconds)
        if self.identity: return value.copy()
        beats = value * self.reference_bpm / 60
        indices = np.clip(np.searchsorted(self.beats, beats, side='right') - 1, 0, len(self.beats) - 1)
        # Before beat zero and after the last point, hold the endpoint tempo.
        span = np.maximum(beats, 0) - self.beats[indices]
        result = self.times[indices] + self._integral(span, self.bpms[indices], self.slopes[indices])
        return np.where(beats < 0, beats * 60 / self.bpms[0], result)

    def to_reference(self, output_seconds):
        value = self._values(output_seconds)
        if self.identity: return value.copy()
        indices = np.clip(np.searchsorted(self.times, value, side='right') - 1, 0, len(self.times) - 1)
        elapsed = np.maximum(value, 0) - self.times[indices]
        beats = self.beats[indices] + self._inverse(elapsed, self.bpms[indices], self.slopes[indices])
        beats = np.where(value < 0, value * self.bpms[0] / 60, beats)
        return beats * 60 / self.reference_bpm

    def bpm_at(self, reference_seconds):
        beats = self._values(reference_seconds) * self.reference_bpm / 60
        return np.interp(beats, self.beats, self.bpms)

    def segment_token(self, start, end):
        """All clock information inside this reference-time interval.

        Absolute elapsed endpoints retain sample-rounding phase. Knots outside
        the interval matter only through the BPM at the interval boundaries.
        This is deliberately conservative for changes before the interval.
        """
        start, end = float(start), float(end)
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise djmix.MixError('Invalid tempo cache interval.')
        beats = np.r_[start * self.reference_bpm / 60,
                      self.beats[(self.beats > start * self.reference_bpm / 60)
                                 & (self.beats < end * self.reference_bpm / 60)],
                      end * self.reference_bpm / 60]
        values = np.interp(beats, self.beats, self.bpms)
        return ['set-tempo-interval', VERSION, start, end,
                np.column_stack((beats, values)).tolist(),
                float(self.to_output(start)), float(self.to_output(end))]

    def output_frame(self, reference_frame):
        return np.rint(self.to_output(np.asarray(reference_frame) / djmix.SR) * djmix.SR).astype(np.int64)

    def reference_frame(self, output_frame):
        return np.rint(self.to_reference(np.asarray(output_frame) / djmix.SR) * djmix.SR).astype(np.int64)

    def output_span(self, start, end):
        a, b = self.output_frame([start, end])
        return int(a), int(b)


def keyframes(function, knots, tolerance=.25):
    """Approximate a monotone composed time map to sub-sample precision.

    Input/output units are samples. All changes of slope must be supplied as
    knots. Recursive quarter-point checks capture curved intervals; integers
    are rounded once and degenerate interior keyframes are dropped.
    """
    knots = np.unique(np.asarray(knots, dtype=np.int64))
    if len(knots) < 2 or knots[0] != 0 or knots[-1] < 2:
        raise djmix.MixError('A tempo map needs a complete nonempty input interval.')
    values = np.asarray(function(knots), dtype=float)
    if (values.shape != knots.shape or not np.isfinite(values).all()
            or abs(values[0]) > 1e-6 or np.any(np.diff(values) <= 0)):
        raise djmix.MixError('The tempo curve produced an invalid time map.')
    points = [(int(knots[0]), float(values[0]))]
    def split(a, b, ya, yb):
        if b - a <= 1: points.append((b, yb)); return
        x = np.unique(np.rint(a + (b - a) * np.array([.25, .5, .75])).astype(int))
        x = x[(x > a) & (x < b)]
        y = np.asarray(function(x), dtype=float)
        if not np.isfinite(y).all() or np.any(y <= ya) or np.any(y >= yb):
            raise djmix.MixError('The tempo curve must move forward through the music.')
        error = np.max(np.abs(y - (ya + (yb - ya) * (x - a) / (b - a))))
        if error <= tolerance: points.append((b, yb)); return
        middle = (a + b) // 2; ym = float(function(np.array([middle]))[0])
        split(a, middle, ya, ym); split(middle, b, ym, yb)
    for a, b, ya, yb in zip(knots[:-1], knots[1:], values[:-1], values[1:]):
        split(int(a), int(b), float(ya), float(yb))
        if len(points) > 1_000_000: raise djmix.MixError('The tempo curve needs too many processing points.')
    rounded = np.rint(points).astype(np.int64)
    final = tuple(rounded[-1]); kept = [tuple(rounded[0])]
    for x, y in rounded[1:-1]:
        if kept[-1][0] < x < final[0] and kept[-1][1] < y < final[1]: kept.append((x, y))
    if final[1] <= kept[-1][1]: raise djmix.MixError('The tempo curve compresses the passage beyond sample resolution.')
    kept.append(final)
    return np.asarray(kept, dtype=np.int64)
