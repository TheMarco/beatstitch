"""Bounded beat-phase alignment estimation for EDM mixing.

Limitations:
- Assumes both excerpts are the same overlap on one clock, already at the
  same playback tempo; it estimates only the smallest beat-phase correction
  within +/- half a quarter-note beat, optionally extending to the nearest
  repeated strong kick accent within half a four-beat bar. This does not
  establish the musical first-beat-of-bar.
  Equally accented kicks make bar phase fundamentally ambiguous.
- Prefers repeated low-frequency kick evidence; music without clear kicks,
  strong half-beat offbeat bass, drifting tempo, or sparse events yields
  ``uncertain`` rather than a misleading shift.
- Correlation values in diagnostics are evidence strength, not calibrated
  accuracy percentages.
"""

from __future__ import annotations

import numpy as np
from scipy import signal

_ALIGNED_TOL_S = 0.010
_MIN_PEAK_CORR = 0.25
_AMBIGUITY_RATIO = 0.88
_WINDOW_STABILITY_TOL_S = 0.020


def _validate_array(name, value):
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a NumPy PCM array")
    try:
        arr = np.asarray(value)
    except Exception as exc:
        raise ValueError(f"{name} must be a NumPy PCM array") from exc
    if arr.dtype == object or not (
        np.issubdtype(arr.dtype, np.number) and not np.issubdtype(arr.dtype, np.complexfloating)
    ):
        raise ValueError(f"{name} must be a real numeric PCM array")
    if arr.ndim not in (1, 2):
        raise ValueError(f"{name} must be mono (N,) or stereo (N, channels)")
    if arr.ndim == 2:
        # Accept (N, 1|2) and channels-first (1|2, N).
        if arr.shape[1] in (1, 2):
            pass
        elif arr.shape[0] in (1, 2):
            arr = arr.T
        else:
            raise ValueError(f"{name} must have 1 or 2 channels")
    if arr.shape[0] == 0:
        raise ValueError(f"{name} must not be empty")
    arr = np.array(arr, dtype=np.float64, copy=True)
    if arr.ndim == 1:
        arr = arr[:, None]
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} must contain only finite samples")
    return arr


def _validate_positive(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise ValueError(f"{name} must be a positive finite number")
    v = float(value)
    if not np.isfinite(v) or v <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return v


def _kick_envelope(channels: np.ndarray, sr: float):
    """Low-frequency power envelope + onset envelope at ~1 kHz.

    Power sums per-channel squares so opposite-polarity stereo cannot cancel.
    Low-pass at ~160 Hz suppresses offbeat cymbals / vocals / snares.
    """
    n = channels.shape[0]
    nyq = sr / 2.0
    cutoff = min(160.0, nyq * 0.8)
    if cutoff <= 5:
        raise ValueError("sample_rate is too low for beat analysis")
    sos = signal.butter(4, cutoff, btype="lowpass", fs=sr, output="sos")
    if n > 64:
        filtered = signal.sosfiltfilt(sos, channels, axis=0)
    else:
        filtered = signal.sosfilt(sos, channels, axis=0)
    power = np.sum(filtered * filtered, axis=1)
    env_cut = min(30.0, nyq * 0.8)
    sos_env = signal.butter(2, env_cut, btype="lowpass", fs=sr, output="sos")
    if n > 64:
        env = signal.sosfiltfilt(sos_env, power)
    else:
        env = signal.sosfilt(sos_env, power)
    env = np.maximum(env, 0.0)
    factor = max(1, int(round(sr / 1000.0)))
    if factor > 1:
        env = env[::factor]
    env_sr = sr / factor
    onset = np.maximum(np.diff(env, prepend=env[0]), 0.0)
    return env, onset, env_sr


def _count_onsets(env, env_sr, beat_period):
    if env.size < 3 or np.max(env) <= 0:
        return 0
    distance = max(1, int(0.5 * beat_period * env_sr))
    span = float(np.max(env) - np.median(env))
    if span <= 0:
        return 0
    peaks, _ = signal.find_peaks(env, distance=distance, prominence=0.15 * span)
    return int(len(peaks))


def _delay_for_segment(o_on, i_on, env_sr, beat_period):
    """Delay of incoming vs outgoing (positive = incoming late)."""
    max_lag = int(round(0.5 * beat_period * env_sr))
    if max_lag < 1 or o_on.size < 8:
        return None, 0.0, np.array([]), np.array([])
    o = o_on - np.mean(o_on)
    i = i_on - np.mean(i_on)
    corr = signal.correlate(i, o, mode="full", method="fft")
    lags = signal.correlation_lags(i.size, o.size, mode="full")
    mask = (lags >= -max_lag) & (lags < max_lag) if max_lag > 0 else (lags == 0)
    # include +max_lag only via wrap equivalence; search [-half, half)
    c_sel = corr[mask]
    l_sel = lags[mask]
    if c_sel.size == 0:
        return None, 0.0, c_sel, l_sel
    idx = int(np.argmax(c_sel))
    denom = float(np.linalg.norm(o) * np.linalg.norm(i)) + 1e-18
    peak_corr = float(c_sel[idx] / denom)
    lag = float(l_sel[idx])
    # Parabolic sub-sample refinement (not at edges).
    if 0 < idx < c_sel.size - 1:
        y0, y1, y2 = float(c_sel[idx - 1]), float(c_sel[idx]), float(c_sel[idx + 1])
        d = y0 - 2 * y1 + y2
        if d != 0:
            frac = 0.5 * (y0 - y2) / d
            if np.isfinite(frac) and abs(frac) <= 1.0:
                lag += float(frac)
    return lag / env_sr, peak_corr, c_sel / denom, l_sel / env_sr


def _circular_median(delays, beat_period):
    # Median on circle via minimizing circular distance to candidates.
    best, best_cost = delays[0], np.inf
    for cand in delays:
        diffs = (np.array(delays) - cand + 0.5 * beat_period) % beat_period - 0.5 * beat_period
        cost = float(np.sum(np.abs(diffs)))
        if cost < best_cost:
            best, best_cost = cand, cost
    return float(best)


def _wrap_delay(delay, beat_period):
    return float((delay + 0.5 * beat_period) % beat_period - 0.5 * beat_period)


def estimate_beat_positions(outgoing, incoming, bpm, duration):
    """Check actual beat rates before estimating a single timing offset.

    Inputs are detector observations on the shared playback clock, not an
    invented BPM grid. A changing gap is never repaired by a constant offset.
    No downbeat or bar-phase assertion is made here.
    """
    nominal = _validate_positive('bpm', bpm)
    length = _validate_positive('duration', duration)
    if not 20 <= nominal <= 400 or length > 90:
        raise ValueError('Beat positions need a supported BPM and bounded excerpt')
    period = 60 / nominal
    rows, fits = [], []
    diagnostics = {'beat_period_seconds': period, 'duration_seconds': length,
                   'method': 'detected beat positions'}
    def uncertain(reason, code):
        return {'state': 'uncertain', 'offset_seconds': None, 'reason': reason,
                'reason_code': code, 'diagnostics': diagnostics}
    for values in (outgoing, incoming):
        beats = np.asarray(values, dtype=float)
        if (beats.ndim != 1 or not np.isfinite(beats).all() or
                np.any(beats < 0) or np.any(beats >= length) or np.any(np.diff(beats) <= 0)):
            raise ValueError('Expected ordered finite beat observations inside the excerpt')
        beats = beats[(beats >= .5 * period) & (beats < length - .5 * period)]
        if len(beats) < 12 or beats[-1] - beats[0] < 8 * period:
            return uncertain('Too few detected beats to check both tempo and alignment.', 'sparse_beats')
        intervals = np.diff(beats)
        if np.any((intervals < .7 * period) | (intervals > 1.3 * period)):
            return uncertain('Detected beats are missing, doubled, or disagree with the expected pulse. '
                             'Check the source BPM and listen.', 'ambiguous_beats')
        numbers = np.arange(len(beats), dtype=float)
        keep = np.ones(len(beats), dtype=bool)
        # A detector can place a few beats poorly during a fill or fade. Retain
        # their original sequence numbers and require at least 85% support;
        # dropping an observation must never compress or invent beat intervals.
        for _ in range(3):
            slope, intercept = np.polyfit(numbers[keep], beats[keep], 1)
            keep &= abs(beats - (intercept + slope * numbers)) <= .025
            if np.mean(keep) < .85 or np.sum(keep) < 12:
                return uncertain('Detected beat timing varies within this overlap. A single left/right move '
                                 'cannot keep the whole blend aligned.', 'irregular_beats')
        slope, intercept = np.polyfit(numbers[keep], beats[keep], 1)
        residual = beats - (intercept + slope * numbers)
        fits.append({'bpm': float(60 / slope), 'period_seconds': float(slope),
                     'residual_p95_seconds': float(np.percentile(abs(residual[keep]), 95)),
                     'beats': len(beats), 'supported_beats': int(np.sum(keep))})
        rows.append(beats[keep])
    diagnostics['measured_tempos'] = fits
    if any(f['residual_p95_seconds'] > .035 for f in fits):
        return uncertain('Detected beat timing varies within this overlap. A single left/right move '
                         'cannot keep the whole blend aligned.', 'irregular_beats')
    shared_span = min(row[-1] for row in rows) - max(row[0] for row in rows)
    slip = shared_span * abs(fits[0]['period_seconds'] / fits[1]['period_seconds'] - 1)
    diagnostics['predicted_slip_seconds'] = float(slip)
    # The optional detector has 20 ms frame spacing. A fit can differ slightly
    # with context near a fill; require more than two frames of accumulated
    # slip before treating a small slope difference as a speed mismatch. The
    # independent gap check below still rejects a changing alignment.
    if slip > .050:
        return uncertain(f"The actual beat rates differ: A {fits[0]['bpm']:.1f} BPM, "
                         f"B {fits[1]['bpm']:.1f} BPM in this overlap. Correct the source BPM "
                         'or supported tempo drift before aligning; moving B alone cannot fix the speed.',
                         'tempo_mismatch')
    delays = []
    for hit in rows[0]:
        nearest = rows[1][np.argmin(abs(rows[1] - hit))]
        if abs(nearest - hit) < .5 * period:
            delays.append((float(hit), float(nearest - hit)))
    if len(delays) < 12:
        return uncertain('Not enough matching detected beats to support a timing move.', 'sparse_matches')
    times, gaps = np.asarray(delays).T
    windows = [gaps[(times >= left) & (times < right)]
               for left, right in zip(np.linspace(times[0], times[-1] + .001, 5)[:-1],
                                      np.linspace(times[0], times[-1] + .001, 5)[1:])]
    medians = [float(np.median(window)) for window in windows if len(window) >= 3]
    diagnostics['window_delays_seconds'] = medians
    if len(medians) < 3 or np.ptp(medians) > .035 or np.percentile(abs(gaps - np.median(gaps)), 95) > .04:
        return uncertain('The beat gap changes through this overlap. A single timing move is insufficient.',
                         'changing_gap')
    offset = float(-np.median(gaps))
    if abs(offset) > .5 * period - .015:
        return uncertain('The detected gap is close to half a beat; its direction is ambiguous.', 'half_beat')
    aligned = abs(offset) <= _ALIGNED_TOL_S
    return {'state': 'aligned' if aligned else 'suggested', 'offset_seconds': 0. if aligned else offset,
            'reason': ('Detected beats have matching speeds and already line up closely.' if aligned else
                       'Detected beats have matching speeds. Try moving B '
                       + ('later' if offset > 0 else 'earlier') + ' to line up their pulse.'),
            'alignment_kind': 'detected beats', 'diagnostics': diagnostics}


def _accent_correction(out_env, in_env, env_sr, beat, pulse_offset):
    """Require a distinct, repeated strongest kick in at least three full bars.

    This matches acoustic accents, not musical bar labels. Equal kick strengths
    or different/unstable accent patterns supply no extra whole-beat adjustment.
    """
    rows = []
    for env in (out_env, in_env):
        peaks, _ = signal.find_peaks(env, distance=max(1, round(.6 * beat * env_sr)),
                                     prominence=.06 * np.percentile(env, 99))
        rows.append((peaks / env_sr, env[peaks]))
    if not len(rows[0][0]): return None
    origin = rows[0][0][0]
    accents = []
    for i, (times, powers) in enumerate(rows):
        shifted = times + (pulse_offset if i else 0)
        numbers = np.rint((shifted - origin) / beat).astype(int)
        good = abs(shifted - origin - numbers * beat) < .04
        numbers, powers = numbers[good], powers[good]
        by_bar = {}
        for number, power in zip(numbers, powers):
            by_bar.setdefault(int(number // 4), {})[int(number % 4)] = float(power)
        complete = [np.array([row[n] for n in range(4)]) for row in by_bar.values() if len(row) == 4]
        if len(complete) < 3: return None
        strengths = np.asarray(complete)
        medians = np.median(strengths, axis=0)
        best = int(np.argmax(medians)); second = np.partition(medians, -2)[-2]
        if medians[best] < 1.7 * max(second, 1e-12): return None
        supported = [(np.argmax(row) == best and row[best] > 1.4 * max(np.partition(row, -2)[-2], 1e-12))
                     for row in strengths]
        if sum(supported) < max(3, int(np.ceil(.8 * len(supported)))): return None
        accents.append(best)
    offset = pulse_offset + (accents[0] - accents[1]) * beat
    offset = (offset + 2 * beat) % (4 * beat) - 2 * beat
    return float(offset), {'accent_slots': accents, 'evidence': 'Repeated strongest low-frequency kick accents in both songs.'}


def estimate_alignment(outgoing, incoming, sample_rate, bpm):
    """Estimate smallest beat-phase correction for ``incoming``.

    Positive ``offset_seconds`` means move incoming later, negative means
    move it earlier. Outgoing is the reference. Returns a JSON-serializable
    dict with ``state`` in {suggested, aligned, uncertain}.
    """
    sr = _validate_positive("sample_rate", sample_rate)
    bpm_f = _validate_positive("bpm", bpm)
    if not (20.0 <= bpm_f <= 400.0):
        raise ValueError("bpm is outside the supported range")
    if sr < 1000.0:
        raise ValueError("sample_rate is too low for beat analysis")
    out_ch = _validate_array("outgoing", outgoing)
    in_ch = _validate_array("incoming", incoming)
    if out_ch.shape[0] != in_ch.shape[0]:
        raise ValueError("outgoing and incoming must have equal lengths")

    beat_period = 60.0 / bpm_f
    duration = out_ch.shape[0] / sr
    if duration > 90:
        raise ValueError('Beat alignment checks are limited to 90 seconds')

    def uncertain(reason, diag):
        return {"state": "uncertain", "offset_seconds": None, "reason": reason, "diagnostics": diag}

    diag_base = {
        "beat_period_seconds": float(beat_period),
        "duration_seconds": float(duration),
        "sample_rate": float(sr),
    }

    # Silence check on raw power.
    if float(np.max(np.abs(out_ch))) < 1e-7 or float(np.max(np.abs(in_ch))) < 1e-7:
        return uncertain("One track is silent, so no beat alignment can be estimated.", diag_base)

    out_env, out_on, env_sr = _kick_envelope(out_ch, sr)
    in_env, in_on, _ = _kick_envelope(in_ch, sr)
    # Compress attack magnitudes so differing bar accents do not obscure the
    # regular kick pulse. Original power is retained for the accent check.
    out_on, in_on = np.sqrt(out_on), np.sqrt(in_on)
    diag = dict(diag_base)
    diag["envelope_rate"] = float(env_sr)
    out_count = _count_onsets(out_env, env_sr, beat_period)
    in_count = _count_onsets(in_env, env_sr, beat_period)
    diag["outgoing_onsets"] = int(out_count)
    diag["incoming_onsets"] = int(in_count)

    if duration < 2.0 * beat_period:
        return uncertain("Excerpt is too short to contain repeated beat evidence.", diag)
    if out_count < 3 or in_count < 3:
        return uncertain("Too few repeated low-frequency onsets to support a suggestion.", diag)

    # Independent windows (~4 beats each, 2-4 windows).
    total_beats = duration / beat_period
    n_windows = int(np.clip(int(total_beats // 4), 2, 4)) if total_beats >= 6 else 2
    win_len = out_on.size // n_windows
    if win_len < int(2 * beat_period * env_sr):
        n_windows = 1
        win_len = out_on.size

    window_delays, window_corrs = [], []
    for w in range(n_windows):
        s = w * win_len
        e = out_on.size if w == n_windows - 1 else s + win_len
        d, pc, _, _ = _delay_for_segment(out_on[s:e], in_on[s:e], env_sr, beat_period)
        if d is not None and np.isfinite(d):
            window_delays.append(float(d))
            window_corrs.append(float(pc))
    diag["window_delays_seconds"] = [float(x) for x in window_delays]
    diag["window_peak_correlations"] = [float(x) for x in window_corrs]

    global_delay, global_corr, corr_sel, lag_sel = _delay_for_segment(out_on, in_on, env_sr, beat_period)
    if global_delay is None:
        return uncertain("Could not compute a reliable beat correlation.", diag)
    diag["global_delay_seconds"] = float(global_delay)
    diag["peak_correlation"] = float(global_corr)

    if global_corr < _MIN_PEAK_CORR or (window_corrs and float(np.median(window_corrs)) < 0.18):
        return uncertain("Low-frequency beat evidence does not correlate strongly enough.", diag)

    # Stability across windows (circular w.r.t. beat period).
    if len(window_delays) >= 2:
        med = _circular_median(window_delays, beat_period)
        diffs = [_wrap_delay(d - med, beat_period) for d in window_delays]
        diag["window_offset_seconds"] = [float(-d) for d in window_delays]
        if float(np.max(np.abs(diffs))) > 0.030 or float(np.std(diffs)) > 0.015:
            return uncertain(
                "Beat phase is not stable across independent windows; tempo may be drifting or tracks disagree.",
                diag,
            )
        if abs(_wrap_delay(global_delay - med, beat_period)) > 0.025:
            return uncertain("Global and windowed beat estimates disagree.", diag)
        delay = float(med)
    else:
        delay = float(global_delay)
        diag["window_offset_seconds"] = [float(-delay)]

    # Half-beat ambiguity: correlation half a beat away nearly as strong.
    if corr_sel.size:
        # Recompute extended correlation for ambiguity check.
        o = out_on - np.mean(out_on)
        i = in_on - np.mean(in_on)
        corr_full = signal.correlate(i, o, mode="full", method="fft")
        lags_full = signal.correlation_lags(i.size, o.size, mode="full") / env_sr
        denom = float(np.linalg.norm(o) * np.linalg.norm(i)) + 1e-18
        corr_full = corr_full / denom
        half = 0.5 * beat_period
        for shift in (delay + half, delay - half):
            j = int(np.argmin(np.abs(lags_full - shift)))
            if abs(float(lags_full[j]) - shift) < 1.5 / env_sr and global_corr > 0:
                ratio = float(corr_full[j] / global_corr) if global_corr != 0 else 0.0
                diag["half_beat_correlation_ratio"] = float(ratio)
                if ratio > _AMBIGUITY_RATIO:
                    return uncertain(
                        "On-beat and half-beat-offbeat low-frequency evidence are similarly strong, so the beat phase is ambiguous.",
                        diag,
                    )
                break

    offset = float(-delay)
    diag["estimated_delay_seconds"] = float(delay)

    if abs(offset) > 0.5 * beat_period - 0.005:
        return uncertain("The required correction is near half a beat, so its direction is ambiguous.", diag)

    accent = _accent_correction(out_env, in_env, env_sr, beat_period, offset)
    if accent is not None:
        accent_offset, evidence = accent
        diag['kick_accent'] = evidence
        if abs(accent_offset - offset) > .5 * beat_period:
            offset = accent_offset
            return {'state': 'suggested', 'offset_seconds': offset,
                    'reason': 'Repeated strongest kick accents suggest moving the incoming song '
                              + ('later.' if offset > 0 else 'earlier.')
                              + ' This matches audible accents; listen to confirm musical bar starts.',
                    'diagnostics': diag, 'alignment_kind': 'kick accents'}

    if abs(offset) <= _ALIGNED_TOL_S:
        return {
            "state": "aligned",
            "offset_seconds": 0.0,
            "reason": "Repeated low-frequency onsets already align within 10 ms; no shift is suggested.",
            "diagnostics": diag,
        }
    return {
        "state": "suggested",
        "offset_seconds": float(offset),
        "reason": (
            "Repeated low-frequency onsets indicate the incoming track should be moved "
            + ("later." if offset > 0 else "earlier.")
        ),
        "diagnostics": diag,
    }


def kick_markers(audio, sample_rate, bpm):
    """Visible onset candidates from the same low-frequency evidence as alignment.

    These mark measured attacks, not an invented regular grid or bar numbers.
    Ambiguous bass/snare activity can still appear; the UI labels estimates.
    """
    sr = _validate_positive('sample_rate', sample_rate)
    bpm = _validate_positive('bpm', bpm)
    channels = _validate_array('audio', audio)
    if len(channels) / sr > 90:
        raise ValueError('Beat alignment checks are limited to 90 seconds')
    _, onset, env_sr = _kick_envelope(channels, sr)
    if np.max(onset) < 1e-10:
        return []
    peaks, _ = signal.find_peaks(onset, distance=max(1, round(.6 * 60 / bpm * env_sr)),
                                 prominence=.08 * np.percentile(onset, 99.5))
    return (peaks / env_sr).tolist()
