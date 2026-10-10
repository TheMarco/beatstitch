#!/usr/bin/env python3
"""Isolated tempo-straightening audition; never modifies the source recordings.

This deliberately targets continuous four-on-the-floor passages. Detected kick
landmarks need review; they are not a general-purpose beat/downbeat estimator.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d, uniform_filter1d
from scipy.signal import butter, find_peaks, sosfiltfilt
from scipy.io import wavfile

from djmix import SR, ffmpeg, meter, probe, run

ANALYSIS_RATE = 12000
FEATURE_RATE = 1000


def kick_features(path: Path, start: float = 0, duration: float | None = None):
    selection = ["-ss", str(start)]
    if duration is not None:
        selection += ["-t", str(duration)]
    decoded = ffmpeg("-i", str(path), *selection, "-map", "0:a:0", "-ar",
                     str(ANALYSIS_RATE), "-ac", "1", "-f", "f32le", "-").stdout
    y = np.frombuffer(decoded, dtype="<f4")
    low = sosfiltfilt(butter(3, [35, 180], btype="bandpass", fs=ANALYSIS_RATE,
                            output="sos"), y)
    energy = np.sqrt(np.maximum(0, uniform_filter1d(low * low, 60)))[::12]
    # Use the rising edge, not the position of the kick's later bass maximum.
    rise = np.maximum(0, energy - np.r_[np.zeros(10), energy[:-10]])
    return gaussian_filter1d(rise, 2), energy


def landmarks(path: Path, bpm: float):
    onset, energy = kick_features(path)
    period = 60 / bpm
    peaks, properties = find_peaks(onset, distance=round(period * .65 * FEATURE_RATE),
                                  prominence=np.percentile(onset, 95) * .18)
    times = peaks / FEATURE_RATE
    candidates = []
    for first in range(len(times) - 64):
        selected = times[first:first + 65]
        intervals = np.diff(selected)
        if np.any((intervals < period * .82) | (intervals > period * 1.18)):
            continue
        strengths = onset[peaks[first:first + 65]]
        if np.min(strengths) < .18 * np.median(strengths):
            continue
        slope, intercept = np.polyfit(np.arange(65), selected, 1)
        residual = selected - (intercept + slope * np.arange(65))
        candidates.append({
            "start": float(selected[0]), "end": float(selected[-1]),
            "bpm": float(60 / slope),
            "p95_deviation_ms": float(np.percentile(abs(residual), 95) * 1000),
            "range_ms": float(np.ptp(residual) * 1000),
            "min_strength_ratio": float(min(strengths) / np.median(strengths)),
            "beats": selected.tolist(),
        })
    return {"path": str(path.resolve()), "nominal_bpm": bpm,
            "detected_landmarks": len(times),
            "candidates": sorted(candidates, key=lambda x: x["p95_deviation_ms"], reverse=True)}


def read_stereo(path: Path):
    data = ffmpeg("-i", str(path), "-ar", str(SR), "-ac", "2", "-f", "f32le", "-").stdout
    return np.frombuffer(data, dtype="<f4").reshape(-1, 2).copy()


def write_float(path: Path, data):
    wavfile.write(path, SR, np.asarray(data, dtype=np.float32))


def measure_landmarks(path: Path, expected):
    onset, _ = kick_features(path)
    measured = []
    for point in expected:
        center = round(point * FEATURE_RATE)
        lo, hi = max(0, center - 100), min(len(onset), center + 101)
        measured.append((lo + int(np.argmax(onset[lo:hi]))) / FEATURE_RATE)
    return np.asarray(measured)


def render_comparison(scan: Path, out: Path, candidate: int = 0, warp_engine: str = "r2", crisp: int = 4):
    result = json.loads(scan.read_text())
    source = Path(result["path"])
    selected = result["candidates"][candidate]
    beats = np.asarray(selected["beats"])
    if len(beats) != 65 or np.any(np.diff(beats) <= 0):
        raise ValueError("Expected 65 strictly increasing beat landmarks.")
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    work.mkdir(exist_ok=True)
    # Keep the same average speed in A and B. The only difference is local timing.
    target_bpm = round(selected["bpm"])
    period = 60 / target_bpm
    ratio = 64 * period / (beats[-1] - beats[0])
    start = round((beats[0] - 2) * SR) / SR
    end = min(probe(source)["duration"], beats[-1] + 2)
    if start < 0 or end - beats[-1] < .6:
        raise ValueError("Choose a passage with context on both sides.")
    decoded = work / "source-padded.wav"
    ffmpeg("-i", str(source), "-ss", str(start), "-t", str(end - start),
           "-ar", str(SR), "-ac", "2", "-af", "volume=-12dB",
           "-c:a", "pcm_f32le", str(decoded))
    n = len(read_stereo(decoded))
    source_frames = np.round((beats - start) * SR).astype(int)
    target_frames = np.round(((beats[0] - start) * ratio + np.arange(65) * period) * SR).astype(int)
    anchors = list(zip(source_frames, target_frames))
    anchors.append((n - 1, round(n * ratio) - 1))
    time_map = work / "beat-map.txt"
    # Origin is implicit. No redundant 0->0 mapping in Rubber Band R3.
    time_map.write_text("".join(f"{a} {b}\n" for a, b in anchors))
    expected = .5 + np.arange(65) * period
    crop_start = target_frames[0] - round(.5 * SR)
    frames = round((expected[-1] + .5) * SR)
    metrics, rendered = {}, {}
    for variant in ("fixed", "straightened"):
        stretched = work / f"{variant}-padded.wav"
        args = ["rubberband", "--fine", "--centre-focus", "--quiet", "--time", str(ratio)]
        if variant == "straightened":
            if warp_engine == "r2":
                args[1:2] = ["--fast", "--crisp", str(crisp)]
            args += ["--timemap", str(time_map)]
        run([*args, str(decoded), str(stretched)])
        audio = read_stereo(stretched)[crop_start:crop_start + frames] * 10 ** (12 / 20)
        if len(audio) != frames:
            raise ValueError("Render did not contain all requested audio frames.")
        fade = np.linspace(0, 1, round(.01 * SR))
        audio[:len(fade)] *= fade[:, None]
        audio[-len(fade):] *= fade[::-1, None]
        raw = work / f"{variant}.wav"
        write_float(raw, audio)
        level = meter(raw)
        metrics[variant] = {"before_normalization": level}
        rendered[variant] = (raw, audio)
    # Match integrated loudness using static gain, never limiting/compression.
    loudness = min(-18, *(v["before_normalization"]["lufs"] -
                         v["before_normalization"]["true_peak_db"] - 3 for v in metrics.values()))
    click = np.zeros((frames, 2), dtype=np.float32)
    t = np.arange(round(.022 * SR)) / SR
    tick = .095 * np.sin(2 * np.pi * 1600 * t) * np.exp(-t * 220)
    for point in expected:
        pos = round(point * SR)
        click[pos:pos + len(tick)] += tick[:, None]
    for variant, (raw, audio) in rendered.items():
        gain = loudness - metrics[variant]["before_normalization"]["lufs"]
        audio *= 10 ** (gain / 20)
        write_float(raw, audio)
        for suffix, samples in (("", audio), ("-click", audio + click)):
            intermediate = work / "export.wav"
            write_float(intermediate, samples)
            ffmpeg("-i", str(intermediate), "-c:a", "pcm_s24le", str(out / f"{variant}{suffix}.wav"))
        measured = measure_landmarks(out / f"{variant}.wav", expected)
        deviations = (measured - expected) * 1000
        metrics[variant].update({
            "loudness": meter(out / f"{variant}.wav"),
            "click_loudness": meter(out / f"{variant}-click.wav"),
            "p95_landmark_error_ms": float(np.percentile(abs(deviations[1:-1]), 95)),
            "max_landmark_error_ms": float(max(abs(deviations[1:-1]))),
            "landmark_errors_ms": deviations.tolist(),
        })
    report = {
        "name": source.stem.split("-", 1)[-1].replace("_", " "), "source": str(source),
        "source_start": float(beats[0]), "source_end": float(beats[-1]),
        "target_bpm": target_bpm, "duration": frames / SR, "source_beats": beats.tolist(),
        "warp_engine": warp_engine, "crisp": crisp,
        "expected_beats": expected.tolist(), "metrics": metrics,
        "notes": ["Experimental four-on-the-floor kick landmark detection, not verified downbeats.",
                  "Before/after timing is re-detected from rendered audio with the same kick feature; it is not independent beat ground truth.",
                  "Both versions have identical average stretch, original pitch, duration and matched loudness.",
                  "The click is a timing reference, not a bar/downbeat guide."]}
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"name": report["name"], "bpm": target_bpm, "duration": frames / SR,
                      "metrics": {k: {a: b for a, b in v.items() if a != "landmark_errors_ms"}
                                  for k, v in metrics.items()}}, indent=2), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", type=Path, nargs="+")
    parser.add_argument("--bpm", type=float, nargs="+")
    parser.add_argument("--render", type=Path)
    parser.add_argument("--candidate", type=int, default=0)
    parser.add_argument("--warp-engine", choices=("r2", "r3"), default="r2")
    parser.add_argument("--crisp", type=int, choices=range(7), default=4)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.render:
        render_comparison(args.render, args.out, args.candidate, args.warp_engine, args.crisp)
        return
    if not args.scan or not args.bpm or len(args.scan) != len(args.bpm):
        parser.error("Supply one BPM estimate per file.")
    args.out.mkdir(parents=True, exist_ok=True)
    for path, bpm in zip(args.scan, args.bpm):
        result = landmarks(path, bpm)
        (args.out / f"{path.stem}-scan.json").write_text(json.dumps(result, indent=2) + "\n")
        print(path.name, "continuous 64-beat windows:", len(result["candidates"]), flush=True)
        for row in result["candidates"][:3]:
            print({k: round(v, 3) for k, v in row.items() if k != "beats"}, flush=True)


if __name__ == "__main__":
    main()
