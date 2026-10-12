"""Editable regular reference grid and tap estimates, separate from audio warping."""
from __future__ import annotations

from functools import lru_cache
import math
import statistics
import time

from studio_cache import source_signature


def grid_settings(track, bpm, anchor):
    if not math.isfinite(bpm) or not 20 <= bpm <= 300:
        raise ValueError('Source BPM must be between 20 and 300.')
    if not math.isfinite(anchor) or not 0 <= anchor < track.duration:
        raise ValueError('Put beat 1 inside the song.')
    return {'version': 1, 'source': source_signature(track.path), 'bpm': float(bpm), 'anchor': float(anchor)}


def manual_settings(track, check_source=False):
    value = track.beat_grid
    if (not isinstance(track.bpm, (float, int)) or not 20 <= track.bpm <= 300 or
            value.get('version') != 1 or value.get('bpm') != track.bpm or
            not isinstance(value.get('anchor'), (float, int)) or
            not math.isfinite(value['anchor']) or not 0 <= value['anchor'] < track.duration):
        return {}
    if check_source:
        try:
            if value.get('source') != source_signature(track.path):
                return {}
        except OSError:
            return {}
    return value


@lru_cache(maxsize=16)
def _regular(bpm, anchor, duration):
    beat = 60 / bpm
    first, stop = math.ceil(-anchor / beat), math.ceil((duration - anchor) / beat)
    numbered = [(n, anchor + n * beat) for n in range(first, stop)]
    beats = [t for _, t in numbered if 0 <= t < duration]
    downbeats = [t for n, t in numbered if n % 4 == 0 and 0 <= t < duration]
    bars = [{'start': a, 'end': b, 'number': i} for i, (a, b) in enumerate(zip(downbeats, downbeats[1:]))]
    return {'beats': beats, 'downbeats': downbeats, 'bars': bars, 'manual': True}


def display_grid(track):
    """A manual reference is explicit; detection coverage never describes it."""
    settings = manual_settings(track, check_source=True)
    if settings:
        return _regular(track.bpm, settings['anchor'], track.duration)
    from studio_structure import current_analysis
    return current_analysis(track, check_source=True)


class TapTempo:
    def __init__(self):
        self.times = []

    def tap(self, now=None):
        now = time.monotonic() if now is None else float(now)
        if not math.isfinite(now):
            raise ValueError('Tap time must be finite.')
        if self.times and (now <= self.times[-1] or now - self.times[-1] > 3):
            self.times.clear()
        if self.times and now - self.times[-1] < .18:
            return None  # Ignore accidental double taps.
        self.times = (self.times + [now])[-9:]
        if len(self.times) < 4:
            return None
        intervals = [b - a for a, b in zip(self.times, self.times[1:])]
        median = statistics.median(intervals)
        spread = statistics.median(abs(t - median) for t in intervals) / median
        bpm = 60 / median
        if not 20 <= bpm <= 300:
            return None
        return {'bpm': round(bpm, 2), 'steady': spread <= .12, 'taps': len(self.times)}
