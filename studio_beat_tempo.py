"""Reviewable tempo estimates from observed beats; no automatic source edits."""
import math

import numpy as np

VERSION = 1


def tempo_from_beats(beats, duration):
    """Fit consecutive observed beats; do not invent beats or force a tempo range.

    16-interval fits reduce the detector's 20-ms quantization. Missed/extra beats
    and large local changes reject a window, rather than bias its tempo fit.
    The thresholds are engineering checks, not calibrated confidence.
    """
    beats = np.asarray(beats, dtype=float)
    if (beats.ndim != 1 or not np.isfinite(beats).all() or not math.isfinite(duration)
            or duration <= 0 or np.any(beats < 0) or np.any(beats >= duration)
            or np.any(np.diff(beats) <= 0)):
        raise ValueError('Expected finite, ordered beat observations inside the audio.')
    windows = []
    x = np.arange(17, dtype=float) - 8
    for start in range(0, len(beats) - 16, 8):
        observed = beats[start:start + 17]
        intervals = np.diff(observed)
        median = np.median(intervals)
        if np.any(abs(intervals / median - 1) > .2):
            continue
        slope = float(np.dot(x, observed - observed.mean()) / np.dot(x, x))
        residual = float(np.percentile(abs(observed - (observed.mean() + slope * x)), 95))
        if not 20 <= 60 / slope <= 300 or residual > .07:
            continue
        windows.append({'start': float(observed[0]), 'end': float(observed[-1]),
                        'period': slope, 'bpm': 60 / slope, 'residual_p95_seconds': residual})
    if len(windows) < 2:
        return {'bpm': None, 'windows': windows, 'coverage': 0., 'state': 'uncertain'}
    period = float(np.median([w['period'] for w in windows]))
    covered, end = 0., 0.
    for window in windows:
        covered += max(0., window['end'] - max(end, window['start']))
        end = max(end, window['end'])
    return {'bpm': 60 / period, 'windows': windows, 'coverage': covered / duration,
            'state': 'estimated', 'window_bpm_p10_p90': np.percentile([w['bpm'] for w in windows], [10, 90]).tolist()}

