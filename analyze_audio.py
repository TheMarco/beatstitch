#!/usr/bin/env python3
"""Provisional local BPM/key estimates for a bounded audio excerpt, using librosa."""

import argparse
import json
from pathlib import Path

import librosa
import numpy as np

from djmix import MixError, ffmpeg, key_name, number, probe, seconds


RATE = 22050
HOP = 128
# Krumhansl-Kessler pitch-class profiles. Scores are correlations, NOT probabilities.
PROFILES = {
    "major": np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]),
    "minor": np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]),
}


def key_candidates(chroma):
    vector = np.mean(chroma, axis=1)
    if np.std(vector) < 1e-8:
        return []
    scored = []
    for mode, profile in PROFILES.items():
        for root in range(12):
            score = float(np.corrcoef(vector, np.roll(profile, root))[0, 1])
            scored.append({"key": key_name((root, mode)), "correlation": round(score, 4)})
    return sorted(scored, key=lambda x: x["correlation"], reverse=True)[:3]


def analyze(path: Path, start: float, duration: float, bars: int = 8):
    info = probe(path)
    if start < 0 or duration < 4 or start + duration > info["duration"] + 0.01:
        raise MixError("Choose an excerpt of at least four seconds within the source file.")
    decoded = ffmpeg("-i", str(path), "-ss", str(start), "-t", str(duration), "-map", "0:a:0",
                     "-ar", str(RATE), "-ac", "1", "-f", "f32le", "-").stdout
    y = np.frombuffer(decoded, dtype="<f4").copy()
    if np.max(np.abs(y)) < 1e-5:
        raise MixError("The selected excerpt is effectively silent.")
    spectrum = librosa.stft(y, n_fft=2048, hop_length=HOP)
    harmonic, percussive = librosa.decompose.hpss(spectrum, margin=(3, 3))
    onset = librosa.onset.onset_strength(S=librosa.amplitude_to_db(np.abs(percussive), ref=np.max),
                                        sr=RATE, hop_length=HOP, max_size=3)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=RATE, hop_length=HOP,
                                         start_bpm=128, tightness=100)
    times = librosa.frames_to_time(beats, sr=RATE, hop_length=HOP)
    harmonic_y = librosa.istft(harmonic, hop_length=HOP, length=len(y))
    chroma = librosa.feature.chroma_cqt(y=harmonic_y, sr=RATE, hop_length=256, n_octaves=6)
    windows = []
    count = bars * 4
    for index in range(0, len(times) - count, 4):
        beat_times = times[index:index + count + 1]
        period, origin = np.polyfit(np.arange(len(beat_times)), beat_times, 1)
        residuals = beat_times - (origin + period * np.arange(len(beat_times)))
        first = max(0, round(origin * RATE / 256))
        last = min(chroma.shape[1], round((origin + count * period) * RATE / 256))
        if last <= first or origin < 0 or origin + count * period > duration:
            continue
        windows.append({
            "cue_seconds": round(start + float(origin), 6), "bpm": round(float(60 / period), 4),
            "end_seconds": round(start + float(origin + count * period), 6),
            "grid_residual_p95_ms": round(float(np.percentile(np.abs(residuals), 95) * 1000), 2),
            "keys": key_candidates(chroma[:, first:last]),
            "onset_strength": round(float(np.mean(onset[beats[index:index+count]])), 3),
        })
    return {
        "file": str(path.resolve()), "start": start, "duration": duration,
        "tempo_tracker_estimate": float(np.asarray(tempo).item()),
        "section_key_candidates": key_candidates(chroma), "candidate_windows": windows,
        "beat_times": [round(start + float(t), 6) for t in times],
        "limitations": [
            "These are unverified estimates. Half/double-tempo ambiguity and offbeat tracking remain possible.",
            "Candidate cues are beats, not verified downbeats or phrase boundaries.",
            "Residuals describe consistency of the detected beat grid, not independently measured timing accuracy.",
            "Key correlations are not confidence probabilities, and tonal ambiguity or modulation may remain.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--start", type=seconds, default=0)
    parser.add_argument("--duration", type=number, default=60)
    parser.add_argument("--bars", type=int, choices=(4, 8, 16), default=8)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output already exists; choose a new JSON filename.")
    result = analyze(args.file, args.start, args.duration, args.bars)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("candidate_windows", "beat_times")}, indent=2))
    print(f"Saved {len(result['candidate_windows'])} candidate windows to {args.out}")


if __name__ == "__main__":
    main()
