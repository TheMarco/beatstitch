"""Pure section-change scoring for DJ Studio.

Stateless, dependency-light (numpy only) analysis component: given bar
boundaries plus precomputed log-mel and RMS frames, score likely section
changes and report repeat-pattern (8-bar phase) evidence.

No model inference, I/O, Qt, scipy, or audio decoding happens here.
Inputs are treated as read-only.
"""

from __future__ import annotations

import math

import numpy as np

_FLOOR = 1e-8
_GAP_TOL = 0.05
_GAP_EPS = 1e-9
_MIN_SCORE = 0.18
_NEAR_ZERO = 1e-9
_MIN_SCALE = 0.1


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_bars(bars):
    if not isinstance(bars, (list, tuple)):
        raise ValueError("bars must be a list of {start, end, number} dicts")
    checked = []
    prev_end = None
    for entry in bars:
        if not isinstance(entry, dict):
            raise ValueError("each bar must be a dict")
        try:
            start = entry["start"]
            end = entry["end"]
            number = entry["number"]
        except KeyError as exc:
            raise ValueError("each bar needs start, end, number") from exc
        if not _is_number(start) or not _is_number(end):
            raise ValueError("bar start/end must be numbers")
        start = float(start)
        end = float(end)
        if not (math.isfinite(start) and math.isfinite(end)):
            raise ValueError("bar start/end must be finite")
        if not end > start:
            raise ValueError("bars must have positive length")
        if isinstance(number, bool):
            raise ValueError("bar number must be an integer")
        if isinstance(number, int):
            num = number
        elif isinstance(number, float) and number.is_integer():
            num = int(number)
        else:
            raise ValueError("bar number must be an integer")
        if prev_end is not None and start < prev_end:
            raise ValueError("bars must be sorted and non-overlapping")
        prev_end = end
        checked.append({"start": start, "end": end, "number": num})
    return checked


def _validate_arrays(logmel, rms):
    try:
        mel = np.asarray(logmel)
        energy = np.asarray(rms)
    except Exception as exc:
        raise ValueError("logmel/rms must be array-like") from exc
    if mel.ndim != 2:
        raise ValueError("logmel must be a (frames, mel_bins) array")
    if energy.ndim != 1:
        raise ValueError("rms must be a (frames,) array")
    if mel.shape[0] != energy.shape[0]:
        raise ValueError("logmel and rms frame counts must match")
    if mel.shape[0] > 0 and mel.shape[1] < 1:
        raise ValueError("logmel must have at least one mel bin")
    if mel.size and not bool(np.all(np.isfinite(mel))):
        raise ValueError("logmel must be finite")
    if energy.size and not bool(np.all(np.isfinite(energy))):
        raise ValueError("rms must be finite")
    if energy.size and bool(np.any(energy < 0)):
        raise ValueError("rms must be nonnegative")
    return mel, energy


def _validate_frame_rate(frame_rate):
    if not _is_number(frame_rate):
        raise ValueError("frame_rate must be a positive number")
    fps = float(frame_rate)
    if not math.isfinite(fps) or not fps > 0:
        raise ValueError("frame_rate must be positive and finite")
    return fps


def _db(amplitude: float) -> float:
    return 20.0 * math.log10(max(float(amplitude), _FLOOR))


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < _NEAR_ZERO and nb < _NEAR_ZERO:
        return 0.0
    if na < _NEAR_ZERO or nb < _NEAR_ZERO:
        return 1.0
    cos_sim = float(np.dot(a, b) / (na * nb))
    cos_sim = min(1.0, max(-1.0, cos_sim))
    return float(min(2.0, max(0.0, 1.0 - cos_sim)))


def analyze_sections(bars, logmel, rms, frame_rate=50):
    """Score section changes between bars.

    Args:
        bars: list of {start, end, number} dicts (source seconds, bar number).
        logmel: finite (frames, mel_bins) array aligned with ``rms``.
        rms: finite nonnegative (frames,) array.
        frame_rate: frames per second, positive.

    Returns:
        dict with ``boundaries`` (sorted by time) and ``phrase`` evidence
        (or None). Each boundary holds time, bar_index (number of the bar
        starting at the boundary), change (raw score, NOT a probability),
        energy_before_db, energy_after_db, and a label.
    """
    checked_bars = _validate_bars(bars)
    mel, energy = _validate_arrays(logmel, rms)
    fps = _validate_frame_rate(frame_rate)

    n_bars = len(checked_bars)
    n_frames = int(mel.shape[0])
    if n_bars == 0 or n_frames == 0:
        return {"boundaries": [], "phrase": None}

    # Pool mean log-mel and mean RMS per bar; bars with no covered frames
    # are missing analysis and block any window using them.
    bar_mel = [None] * n_bars
    bar_rms = [None] * n_bars
    for idx, bar in enumerate(checked_bars):
        start_idx = int(math.floor(bar["start"] * fps))
        end_idx = int(math.ceil(bar["end"] * fps))
        start_idx = min(max(start_idx, 0), n_frames)
        end_idx = min(max(end_idx, 0), n_frames)
        if end_idx <= start_idx:
            continue
        bar_mel[idx] = np.mean(mel[start_idx:end_idx], axis=0)
        bar_rms[idx] = float(np.mean(energy[start_idx:end_idx]))

    valid_idx = [i for i in range(n_bars) if bar_mel[i] is not None]
    if len(valid_idx) < 4:
        return {"boundaries": [], "phrase": None}

    # Standardize per-bar means with per-bin median and max(IQR, 0.1).
    stacked = np.stack([bar_mel[i] for i in valid_idx], axis=0)
    median = np.median(stacked, axis=0)
    q75 = np.percentile(stacked, 75, axis=0)
    q25 = np.percentile(stacked, 25, axis=0)
    scale = np.maximum(q75 - q25, _MIN_SCALE)
    normed = {i: (bar_mel[i] - median) / scale for i in valid_idx}

    # Candidate boundaries: index i = first bar AFTER the boundary, needing
    # the four-bar window [i-2, i-1, i, i+1] with no time gaps.
    candidates = []
    for i in range(2, n_bars - 1):
        window = (i - 2, i - 1, i, i + 1)
        if any(j not in normed for j in window):
            continue
        consecutive = True
        for a, b in zip(window[:-1], window[1:]):
            gap = checked_bars[b]["start"] - checked_bars[a]["end"]
            if gap > _GAP_TOL + _GAP_EPS:
                consecutive = False
                break
        if not consecutive:
            continue
        before = (normed[i - 2] + normed[i - 1]) / 2.0
        after = (normed[i] + normed[i + 1]) / 2.0
        texture = _cosine_distance(before, after)
        e_before = _db((bar_rms[i - 2] + bar_rms[i - 1]) / 2.0)
        e_after = _db((bar_rms[i] + bar_rms[i + 1]) / 2.0)
        delta = e_after - e_before
        score = texture + 0.25 * min(1.0, abs(delta) / 12.0)
        if not math.isfinite(score):
            continue
        candidates.append(
            {
                "pos": i,
                "score": float(score),
                "energy_before_db": float(e_before),
                "energy_after_db": float(e_after),
                "delta_db": float(delta),
            }
        )
    if not candidates:
        return {"boundaries": [], "phrase": None}

    # Peak picking: local maxima in +/-1 candidate with an adaptive floor.
    scores = np.array([c["score"] for c in candidates], dtype=float)
    med = float(np.median(scores))
    mad = float(np.median(np.abs(scores - med)))
    threshold = max(_MIN_SCORE, med + 1.5 * mad)
    peaks = []
    last = len(candidates) - 1
    for j, cand in enumerate(candidates):
        if cand["score"] < threshold:
            continue
        if j > 0 and cand['pos'] - candidates[j - 1]['pos'] == 1 and cand["score"] < candidates[j - 1]["score"]:
            continue
        if j < last and candidates[j + 1]['pos'] - cand['pos'] == 1 and cand["score"] < candidates[j + 1]["score"]:
            continue
        peaks.append(cand)

    # Greedy pass: keep strongest changes at least four bar numbers apart.
    peaks.sort(key=lambda c: (-c["score"], checked_bars[c["pos"]]["start"]))
    kept = []
    for cand in peaks:
        num = checked_bars[cand["pos"]]["number"]
        if any(abs(num - checked_bars[k["pos"]]["number"]) < 4 for k in kept):
            continue
        kept.append(cand)
    kept.sort(key=lambda c: checked_bars[c["pos"]]["start"])

    boundaries = []
    for cand in kept:
        bar = checked_bars[cand["pos"]]
        delta = cand["delta_db"]
        if delta >= 3.0:
            label = "Energy rises"
        elif delta <= -3.0:
            label = "Energy falls"
        else:
            label = "Texture changes"
        boundaries.append(
            {
                "time": float(bar["start"]),
                "bar_index": int(bar["number"]),
                "change": float(cand["score"]),
                "energy_before_db": float(cand["energy_before_db"]),
                "energy_after_db": float(cand["energy_after_db"]),
                "label": str(label),
            }
        )

    return {"boundaries": boundaries, "phrase": _phrase_evidence(boundaries)}


def _phrase_evidence(boundaries):
    """Repeat-pattern evidence for an 8-bar phase, or None."""
    if len(boundaries) < 3:
        return None
    weights = {}
    for b in boundaries:
        phase = int(b["bar_index"]) % 8
        weights[phase] = weights.get(phase, 0.0) + float(b["change"])
    total = sum(weights.values())
    if not total > 0 or not math.isfinite(total):
        return None
    ranked = sorted(weights.items(), key=lambda kv: -kv[1])
    (winner, w_score), runner = ranked[0], ranked[1] if len(ranked) > 1 else (None, 0.0)
    agreement = w_score / total
    margin = (w_score - runner[1]) / total
    support = [b for b in boundaries if int(b["bar_index"]) % 8 == winner]
    if agreement < 0.65 or margin < 0.20 or len(support) < 3:
        return None
    return {
        "bars": 8,
        "phase": int(winner),
        "agreement": float(agreement),
        "anchor": float(min(b["time"] for b in support)),
        "support": int(len(support)),
    }
