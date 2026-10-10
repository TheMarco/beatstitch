#!/usr/bin/env python3
"""Compare local tempo throughout the audition library, with inspectable evidence.

Analysis only. A pulse-grid fit is a candidate, not proof of a musical beat.
The chosen recording still needs landmark review before time warping.
"""
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import correlate, find_peaks

from prototype_tempo import kick_features

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs/tempo-prototype/drift-survey"


def fit_grid(onset, nominal_bpm):
    # First locate likely periods using several lags, then score whole grids.
    lo, hi = round(60000 / nominal_bpm * .85), round(60000 / nominal_bpm * 1.15)
    centered = onset - np.mean(onset)
    ac = correlate(centered, centered, mode="full", method="fft")[len(onset)-1:]
    periods = np.arange(lo, hi + 1)
    score = sum(ac[periods * m] / (len(onset) - periods * m) / np.sqrt(m) for m in (1, 2, 4))
    peaks, _ = find_peaks(score)
    best = peaks[np.argsort(score[peaks])[-4:]] if len(peaks) else [int(np.argmax(score))]
    trial = sorted(set(int(periods[i] + d) for i in best for d in range(-3, 4)
                       if lo <= periods[i] + d <= hi))
    # Capping extreme accents prevents one fill from winning the whole fit.
    bounded = np.minimum(onset, np.percentile(onset, 98))
    indices = np.arange(len(onset))
    choices = []
    for period in trial:
        folded = np.bincount(indices % period, weights=bounded, minlength=period)
        folded = gaussian_filter1d(folded, 5, mode="wrap") / (len(onset) / period)
        phase = int(np.argmax(folded))
        choices.append((folded[phase], period, phase))
    _, period, phase = max(choices)
    predicted = np.arange(phase, len(onset), period)
    predicted = predicted[(predicted > 120) & (predicted < len(onset)-120)]
    found, strength = [], []
    for position in predicted:
        lo, hi = position - 90, position + 91
        peak = lo + int(np.argmax(onset[lo:hi]))
        found.append(peak / 1000)
        strength.append(onset[peak])
    found, strength = np.asarray(found), np.asarray(strength)
    strong = strength > .22 * np.median(strength)
    # Robust fit drops missing kicks / fills without inventing anchors for them.
    beat_numbers = np.arange(len(found))
    keep = strong.copy()
    for _ in range(3):
        slope, intercept = np.polyfit(beat_numbers[keep], found[keep], 1)
        deviations = found - (intercept + slope * beat_numbers)
        keep = strong & (np.abs(deviations) < .04)
        if sum(keep) < 12:
            return None
    quality = float(np.mean(keep))
    prominence = float(np.median(strength[keep]) / max(1e-9, np.percentile(onset, 95)))
    if quality < .8 or prominence < 1.1:
        return None
    return {"bpm": float(60 / slope), "quality": quality, "prominence": prominence,
            "residual_p95_ms": float(np.percentile(abs(deviations[keep]), 95) * 1000),
            "beats": found[keep].tolist(), "beat_numbers": beat_numbers[keep].tolist()}


def scan(path, bpm):
    identity = {"path": str(path), "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns}
    cache = OUT / f"{path.stem}-features.npz"
    if cache.exists():
        data = np.load(cache)
        if json.loads(str(data["identity"])) == identity:
            onset = data["onset"]
        else:
            onset, _ = kick_features(path)
    else:
        onset, _ = kick_features(path)
    np.savez_compressed(cache, identity=json.dumps(identity), onset=onset)
    windows = []
    for offset in range(0, len(onset) - 16000, 6000):
        result = fit_grid(onset[offset:offset+16000], bpm)
        if result:
            result["start"] = offset / 1000
            result["end"] = (offset + 16000) / 1000
            result["beats"] = [t + offset / 1000 for t in result["beats"]]
            windows.append(result)
    bpms = [w["bpm"] for w in windows]
    result = {**identity, "nominal_bpm": bpm, "windows": windows,
              "coverage": len(windows) / max(1, len(range(0, len(onset)-16000, 6000)))}
    if bpms:
        low, high = np.percentile(bpms, [10, 90])
        result.update({"bpm_p10": low, "bpm_p90": high,
                       "tempo_spread_percent": float(100 * (high-low) / np.median(bpms))})
    (OUT / f"{path.stem}.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tracks = []
    for cache in sorted((ROOT / "outputs/studio/library-cache").glob("*.json")):
        track = json.loads(cache.read_text())["track"]
        result = scan(Path(track["path"]), track["bpm"])
        tracks.append({k: v for k, v in result.items() if k != "windows"})
        print(Path(track["path"]).name, "windows", len(result["windows"]),
              "range", [round(result.get(k, 0), 3) for k in ("bpm_p10", "bpm_p90")],
              "spread %", round(result.get("tempo_spread_percent", 0), 3), flush=True)
    tracks.sort(key=lambda t: t.get("tempo_spread_percent", -1), reverse=True)
    (OUT / "ranking.json").write_text(json.dumps(tracks, indent=2) + "\n")


if __name__ == "__main__":
    main()
