#!/usr/bin/env python3
"""An offline, user-directed two-track mixing proof using FFmpeg and Rubber Band."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import html
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np


SR = 48000
CHANNELS = 2
BLOCK = 65536
NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MAJOR = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


class MixError(ValueError):
    """A user-readable validation or processing failure."""


def run(args: list[str]) -> subprocess.CompletedProcess:
    command = [str(x) for x in args]
    if command[0] in ("ffmpeg", "ffprobe", "rubberband"):
        command[0] = tool_path(command[0]) or command[0]
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    from studio_process import run_command
    result = run_command(command, options)
    if result.returncode:
        detail = result.stderr.decode(errors="replace")[-3000:]
        raise MixError(f"{Path(args[0]).name} failed:\n{detail}")
    return result


def tool_path(name: str) -> str | None:
    filename = name + (".exe" if sys.platform == "win32" else "")
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "bin" / filename
    if bundled.is_file():
        return str(bundled)
    found = shutil.which(name)
    if found:
        return found
    # Finder launches do not inherit the interactive shell's Homebrew PATH.
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        candidate = Path(prefix) / filename
        if candidate.is_file():
            return str(candidate)
    return None


def require_tools() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe", "rubberband") if not tool_path(name)]
    if missing:
        raise MixError("Missing tools: " + ", ".join(missing) + ". See README.md for setup.")


def ffmpeg(*args: str) -> subprocess.CompletedProcess:
    return run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args])


def stream_ffmpeg(*args: str, chunk_bytes=512 * 1024):
    from studio_process import stream_command
    command = [tool_path('ffmpeg') or 'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)]
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    yield from stream_command(command, options, chunk_bytes)


def number(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a number.") from exc
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError("Expected a finite number.")
    return result


def seconds(value: str) -> float:
    parts = value.split(":")
    if len(parts) > 3:
        raise argparse.ArgumentTypeError("Use seconds, MM:SS, or HH:MM:SS.")
    values = [number(p) for p in parts]
    if any(v < 0 for v in values) or any(v >= 60 for v in values[1:]):
        raise argparse.ArgumentTypeError("Invalid timestamp.")
    return sum(v * 60 ** i for i, v in enumerate(reversed(values)))


def parse_key(value: str | None) -> tuple[int, str] | None:
    if not value:
        return None
    value = value.strip().replace("♯", "#").replace("♭", "b")
    match = re.fullmatch(r"([A-Ga-g])([#b]?)(.*)", value)
    if not match:
        raise MixError(f"Invalid key {value!r}. Use a note and mode, e.g. 'D minor', 'Dm', or 'F#'.")
    letter, accidental, suffix = match.groups()
    suffix = suffix.strip().lower()
    if suffix not in ("", "maj", "major", "m", "min", "minor"):
        raise MixError(f"Invalid key {value!r}. Use major/minor note names, not Camelot numbers.")
    pitch = (MAJOR[letter.upper()] + {"": 0, "#": 1, "b": -1}[accidental]) % 12
    return pitch, "minor" if suffix in ("m", "min", "minor") else "major"


def key_name(key: tuple[int, str] | None) -> str:
    return "Not supplied" if key is None else f"{NOTES[key[0]]} {key[1]}"


def transpose(key: tuple[int, str] | None, semitones: int) -> tuple[int, str] | None:
    return None if key is None else ((key[0] + semitones) % 12, key[1])


def relationship(a: tuple[int, str], b: tuple[int, str]) -> str | None:
    if a == b:
        return "same key"
    if a[1] == b[1] and (b[0] - a[0]) % 12 in (5, 7):
        return "neighboring keys on the circle of fifths"
    relative = (a[0] + (9 if a[1] == "major" else 3)) % 12
    if a[1] != b[1] and b[0] == relative:
        return "relative major/minor"
    return None


def harmonic_advice(key_a: str | None, key_b: str | None, shift_a: int, shift_b: int,
                    texture_a: str, texture_b: str) -> dict:
    a, b = parse_key(key_a), parse_key(key_b)
    shifted_a, shifted_b = transpose(a, shift_a), transpose(b, shift_b)
    advice = {
        "source_a": key_name(a), "source_b": key_name(b),
        "processed_a": key_name(shifted_a), "processed_b": key_name(shifted_b),
        "basis": "User-supplied keys and overlap textures; no automatic key detection.",
        "suggested_b_shifts": [],
    }
    if "percussive" in (texture_a, texture_b):
        advice["assessment"] = "Harmonic overlap is reduced by your percussive-section designation."
        advice["caution"] = "This assumes that section stays free of pitched bass, melodies, and tonal tails throughout the overlap."
    elif shifted_a is None or shifted_b is None:
        advice["assessment"] = "Harmonic compatibility unknown: supply both keys or audition carefully."
    else:
        relation = relationship(shifted_a, shifted_b)
        advice["assessment"] = (f"Plausible harmonic pairing: {relation}." if relation else
                                "No simple harmonic match at the selected pitch shifts. Consider another cue or a shorter overlap.")
        for shift in sorted(range(-2, 3), key=lambda n: (abs(n), n)):
            relation = relationship(shifted_a, transpose(b, shift))
            if relation:
                advice["suggested_b_shifts"].append({"semitones": shift, "reason": relation,
                                                    "resulting_key": key_name(transpose(b, shift))})
        advice["caution"] = "Key compatibility is a suggestion, not a guarantee that melodies or vocals will blend."
    return advice


def probe(path: Path) -> dict:
    if not path.is_file():
        raise MixError(f"Audio file not found: {path}")
    data = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                           "-show_entries", "stream=sample_rate,channels,duration:format=duration",
                           "-of", "json", str(path)]).stdout)
    streams = data.get("streams", [])
    if not streams:
        raise MixError(f"No audio stream in {path.name}")
    duration = streams[0].get("duration", data.get("format", {}).get("duration"))
    try:
        duration = float(duration)
    except (TypeError, ValueError) as exc:
        raise MixError(f"Cannot determine duration of {path.name}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise MixError(f"Invalid audio duration in {path.name}")
    return {"path": str(path.resolve()), "duration": duration, **{
        name: int(streams[0][name]) for name in ("sample_rate", "channels")}}


def meter(path: Path, *, require_loudness: bool = True) -> dict:
    result = run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-map", "0:a:0",
                  "-af", "loudnorm=I=-18:TP=-1:print_format=json", "-f", "null", "-"])
    stderr = result.stderr.decode(errors="replace")
    match = re.search(r'\{\s*"input_i".*?\}', stderr, re.DOTALL)
    if not match:
        raise MixError(f"Could not measure loudness of {path.name}")
    values = json.loads(match.group())
    output = {"lufs": float(values["input_i"]), "true_peak_db": float(values["input_tp"])}
    valid = (all(math.isfinite(v) for v in output.values()) if require_loudness else
             math.isfinite(output['true_peak_db']) or output['true_peak_db'] == -math.inf)
    if not valid:
        raise MixError(f"{path.name} is silent or too short to measure reliably.")
    return output


@dataclass
class Settings:
    bpm_a: float
    bpm_b: float
    target_bpm: float
    cue_a: float
    cue_b: float = 0.0
    bars: int = 16
    context_bars: int = 4
    shift_a: int = 0
    shift_b: int = 0
    key_a: str | None = None
    key_b: str | None = None
    texture_a: str = "musical"
    texture_b: str = "musical"
    full: bool = False
    formants: bool = True
    bass_swap: bool = True
    gain_a: float = 0.0
    gain_b: float = 0.0
    estimated_inputs: bool = False


def plan(settings: Settings, duration_a: float, duration_b: float) -> dict:
    s = settings
    numeric = (s.bpm_a, s.bpm_b, s.target_bpm, s.cue_a, s.cue_b, s.gain_a, s.gain_b,
               duration_a, duration_b)
    if not all(math.isfinite(v) for v in numeric):
        raise MixError("All timing and gain settings must be finite.")
    if any(not 20 <= bpm <= 300 for bpm in (s.bpm_a, s.bpm_b, s.target_bpm)):
        raise MixError("BPM values must be between 20 and 300.")
    if not 1 <= s.bars <= 64 or not 0 <= s.context_bars <= 32:
        raise MixError("Use 1–64 transition bars and 0–32 context bars.")
    if min(s.cue_a, s.cue_b) < 0 or max(abs(s.shift_a), abs(s.shift_b)) > 12:
        raise MixError("Cues must be nonnegative and pitch shifts between -12 and +12 semitones.")
    if max(abs(s.gain_a), abs(s.gain_b)) > 24:
        raise MixError("Gain trims must be between -24 and +24 dB.")
    # Cues are in the original files. A is cut after the overlap; B plays from its cue.
    overlap_a, overlap_b = s.bars * 240 / s.bpm_a, s.bars * 240 / s.bpm_b
    if s.cue_a + overlap_a > duration_a + 1 / SR:
        raise MixError(f"Track A has insufficient audio after its cue for {s.bars} bars.")
    if s.cue_b + overlap_b > duration_b + 1 / SR:
        raise MixError(f"Track B has insufficient audio after its cue for {s.bars} bars.")
    start_a = 0.0 if s.full else max(0.0, s.cue_a - s.context_bars * 240 / s.bpm_a)
    end_b = duration_b if s.full else min(duration_b, s.cue_b + overlap_b + s.context_bars * 240 / s.bpm_b)
    overlap_frames = round(s.bars * 240 / s.target_bpm * SR)
    before_frames = round((s.cue_a - start_a) * s.bpm_a / s.target_bpm * SR)
    after_frames = round((end_b - s.cue_b - overlap_b) * s.bpm_b / s.target_bpm * SR)
    return {
        "a": {"start": start_a, "end": s.cue_a + overlap_a,
              "frames": before_frames + overlap_frames},
        "b": {"start": s.cue_b, "end": end_b, "frames": overlap_frames + after_frames},
        "overlap_frames": overlap_frames, "before_frames": before_frames,
        "after_frames": after_frames, "total_frames": before_frames + overlap_frames + after_frames,
        "overlap_start_seconds": before_frames / SR,
        "overlap_end_seconds": (before_frames + overlap_frames) / SR,
        "duration_seconds": (before_frames + overlap_frames + after_frames) / SR,
    }


def transform(source: Path, output: Path, bpm: float, target_bpm: float,
              shift: int, formants: bool = True) -> None:
    if bpm == target_bpm and shift == 0:
        shutil.copyfile(source, output)
        return
    args = ["rubberband", "--fine", "--centre-focus", "--quiet", "--tempo",
            f"{bpm:.12g}:{target_bpm:.12g}", "--pitch", str(shift)]
    if formants and shift:
        args.append("--formant")
    run([*args, str(source), str(output)])


def raw_audio(path: Path) -> np.memmap:
    byte_count = path.stat().st_size
    if byte_count == 0 or byte_count % (4 * CHANNELS):
        raise MixError(f"Invalid stereo float audio buffer: {path.name}")
    return np.memmap(path, dtype="<f4", mode="r", shape=(byte_count // (4 * CHANNELS), CHANNELS))


def prepare(source: Path, selection: dict, duration: float, bpm: float, target: float,
            shift: int, formants: bool, work: Path, label: str, gain_db: float) -> Path:
    # Render with context on both sides so the actual cue is inside the analysis window.
    padded_start = max(0, selection["start"] - 2)
    padded_end = min(duration, selection["end"] + 2)
    decoded, stretched = work / f"{label}-decode.wav", work / f"{label}-stretch.wav"
    # Processing headroom prevents Rubber Band's automatic clipping-recovery gain change.
    ffmpeg("-i", str(source), "-ss", f"{padded_start:.12f}", "-t", f"{padded_end-padded_start:.12f}",
           "-map", "0:a:0", "-ar", str(SR), "-ac", "2", "-af", "volume=-12dB",
           "-c:a", "pcm_f32le", str(decoded))
    # Fixed-tempo offline processing preserves transient timing better than redundant
    # explicit keyframe maps in our Rubber Band 4.0 regression fixture.
    ratio = bpm / target
    transform(decoded, stretched, bpm, target, shift, formants)
    start_sample = round((selection["start"] - padded_start) * ratio * SR)
    end_sample = start_sample + selection["frames"]
    raw = work / f"{label}.raw"
    ffmpeg("-i", str(stretched), "-af",
           f"atrim=start_sample={start_sample}:end_sample={end_sample},volume={gain_db+12:.12f}dB",
           "-ar", str(SR), "-ac", "2", "-f", "f32le", str(raw))
    found = raw.stat().st_size // 8
    # Allow at most two frames of sample-rounding discrepancy, never hide missing audio.
    if abs(found - selection["frames"]) > 2:
        raise MixError(f"Track {label} render length mismatch: expected {selection['frames']}, got {found} frames.")
    if found < selection["frames"]:
        with raw.open("ab") as handle:
            handle.write(b"\0" * (8 * (selection["frames"] - found)))
    elif found > selection["frames"]:
        with raw.open("r+b") as handle:
            handle.truncate(selection["frames"] * 8)
    # Original excerpts get the same static track gain for fair listening comparisons.
    original_float = work / f"{label}-original.wav"
    ffmpeg("-i", str(source), "-ss", f"{selection['start']:.12f}", "-t",
           f"{selection['end']-selection['start']:.12f}", "-map", "0:a:0",
           "-ar", str(SR), "-ac", "2", "-af", f"volume={gain_db:.12f}dB",
           "-c:a", "pcm_f32le", str(original_float))
    return raw


def split_bands(raw: Path, work: Path, label: str) -> tuple[np.memmap, np.memmap]:
    low, high = work / f"{label}-low.raw", work / f"{label}-high.raw"
    ffmpeg("-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(raw),
           "-filter_complex", "[0:a]acrossover=split=180:order=4th:precision=double[low][high]",
           "-map", "[low]", "-f", "f32le", str(low), "-map", "[high]", "-f", "f32le", str(high))
    return raw_audio(low), raw_audio(high)


def mix_buffers(a_raw: Path, b_raw: Path, p: dict, s: Settings, work: Path) -> Path:
    a, b = raw_audio(a_raw), raw_audio(b_raw)
    if s.bass_swap:
        a_low, a_high = split_bands(a_raw, work, "a")
        b_low, b_high = split_bands(b_raw, work, "b")
    destination = work / "mix.raw"
    before, overlap, total = p["before_frames"], p["overlap_frames"], p["total_frames"]
    swap_frames = min(overlap, round(240 / s.target_bpm * SR))  # One bar at the midpoint.
    swap_start = before + (overlap - swap_frames) / 2
    with destination.open("wb") as handle:
        for offset in range(0, total, BLOCK):
            n = np.arange(offset, min(offset + BLOCK, total))
            progress = np.clip((n - before) / max(1, overlap - 1), 0, 1)
            weight_a = np.cos(progress * np.pi / 2)
            weight_b = np.sin(progress * np.pi / 2)
            weight_a[n >= before + overlap] = 0
            weight_b[n < before] = 0
            chunk = np.zeros((len(n), CHANNELS), dtype=np.float64)
            mask_a, mask_b = n < len(a), (n >= before) & (n - before < len(b))
            if s.bass_swap:
                swap = np.clip((n - swap_start) / max(1, swap_frames - 1), 0, 1)
                bass_b = (1 - np.cos(swap * np.pi)) / 2
                bass_a = 1 - bass_b
                chunk[mask_a] += (a_low[n[mask_a]] * bass_a[mask_a, None]
                                 + a_high[n[mask_a]] * weight_a[mask_a, None])
                indices_b = n[mask_b] - before
                chunk[mask_b] += (b_low[indices_b] * bass_b[mask_b, None]
                                 + b_high[indices_b] * weight_b[mask_b, None])
            else:
                chunk[mask_a] += a[n[mask_a]] * weight_a[mask_a, None]
                chunk[mask_b] += b[n[mask_b] - before] * weight_b[mask_b, None]
            # Five-millisecond safety fades at file boundaries, not at internal cue points.
            edge = np.minimum(np.clip(n / (0.005 * SR), 0, 1),
                              np.clip((total - 1 - n) / (0.005 * SR), 0, 1))
            chunk *= edge[:, None]
            if not np.isfinite(chunk).all():
                raise MixError("Non-finite samples detected while mixing.")
            handle.write(chunk.astype("<f4").tobytes())
    float_mix = work / "mix-float.wav"
    ffmpeg("-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(destination),
           "-c:a", "pcm_f32le", str(float_mix))
    return float_mix


def export_wav(source: Path, output: Path, gain_db: float = 0, raw: bool = False) -> None:
    args = ["-f", "f32le", "-ar", str(SR), "-ac", "2"] if raw else []
    ffmpeg(*args, "-i", str(source), "-af", f"volume={gain_db:.12f}dB",
           "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s24le", str(output))


def report_html(report: dict, out: Path) -> None:
    escape = lambda value: html.escape(str(value), quote=True)
    p, s, harmony = report["timeline"], report["settings"], report["harmony"]
    demo = report.get("demo", False)
    title = "Synthetic engine demonstration" if demo else ("Your two-track mix" if s['full'] else "Your transition preview")
    notice = ("These are generated test tracks, not your songs. This checks the processing pipeline; "
              "it does not establish quality on commercial recordings." if demo else
              "BPM, keys, and cue points are supplied by you. Audition alignment, phrasing, and harmonic fit.")
    if s["estimated_inputs"]:
        notice = "Draft using locally estimated BPM, keys, and cue points. Beat phase, phrasing, and harmonic fit still need your listening check."
    suggestions = "; ".join(f"B {x['semitones']:+d} semitones → {x['resulting_key']} ({x['reason']})"
                            for x in harmony["suggested_b_shifts"]) or "No small-shift suggestion available."
    rows = []
    for label in ("a", "b"):
        track = report["tracks"][label]
        rows.append(f"<tr><th>{label.upper()}</th><td>{escape(Path(track['path']).name)}</td>"
                    f"<td>{s['bpm_'+label]:g} → {s['target_bpm']:g}</td>"
                    f"<td>{escape(harmony['source_'+label])} → {escape(harmony['processed_'+label])}</td>"
                    f"<td>{s['shift_'+label]:+d}</td><td>{s['cue_'+label]:.3f}s</td></tr>")
    cards = "".join(f'<section><h3>Track {label.upper()} — {kind}</h3>'
                    f'<audio controls preload="metadata" src="{label}-{kind}.wav"></audio>'
                    f'<p><a href="{label}-{kind}.wav" download>Download WAV</a></p></section>'
                    for label in ("a", "b") for kind in ("original", "processed"))
    warnings = "".join(f"<li>{escape(w)}</li>" for w in report["notes"])
    document = f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>DJ — {escape(title)}</title>
<style>
body{{font:16px/1.6 system-ui,sans-serif;max-width:960px;margin:48px auto;padding:0 24px;color:#202523;background:#f5f4ef}}
h1{{font-size:36px;line-height:1.15;letter-spacing:-1px}} h2{{font-size:22px;margin-top:36px}}
audio{{width:100%}} table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{text-align:left;border-bottom:1px solid #d6d8d0;padding:12px 10px}} a{{color:#17613e}}
.notice{{border-left:3px solid #c4822e;padding:8px 16px}} .grid{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}
.muted{{color:#5c635e}} .scroll{{overflow-x:auto}} @media(max-width:650px){{.grid{{grid-template-columns:1fr}}}}
</style>
<main><p class="muted">DJ / AUDIO PROTOTYPE</p><h1>{escape(title)}</h1>
<p class="notice">{escape(notice)}</p>
<h2>Listen to the blend</h2><audio controls preload="metadata" src="mix.wav"></audio>
<p>Overlap: <strong>{p['overlap_start_seconds']:.2f}–{p['overlap_end_seconds']:.2f}s</strong> ·
{s['bars']} bars in 4/4 · {s['target_bpm']:g} BPM · {report['output']['true_peak_db']:.2f} dBTP</p>
<p><a href="mix.wav" download>Download mix WAV</a> · <a href="report.json">Processing details</a></p>
<h2>Selected settings</h2><div class="scroll"><table><thead><tr><th>Track</th><th>File</th><th>BPM</th><th>Key</th><th>Shift</th><th>Source cue</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<h2>Harmonic guidance</h2><p>{escape(harmony['assessment'])}</p>
<p>{escape(harmony.get('caution', ''))}</p><p>{escape(suggestions)}</p>
<p class="muted">Suggestions are never applied automatically. Formant preservation: {'on' if s['formants'] else 'off'}.</p>
<h2>Compare the source and processed excerpts</h2><p>Originals retain their original tempo and pitch. Processed excerpts match the mix settings. Both use the same static track gain and comparison headroom.</p>
<div class="grid">{cards}</div><h2>Listening notes</h2><ul>{warnings}</ul>
<p class="muted">Rendered locally with Rubber Band R3 and FFmpeg. Stereo, 48 kHz, 24-bit WAV.</p></main>
<script>document.querySelectorAll('audio').forEach(player=>player.addEventListener('play',()=>document.querySelectorAll('audio').forEach(other=>{{if(other!==player)other.pause()}})));</script>
</html>'''
    (out / "report.html").write_text(document)


def render(a: Path, b: Path, out: Path, s: Settings, demo: bool = False) -> dict:
    require_tools()
    a, b, out = a.resolve(), b.resolve(), out.resolve()
    info_a, info_b = probe(a), probe(b)
    p = plan(s, info_a["duration"], info_b["duration"])
    harmony = harmonic_advice(s.key_a, s.key_b, s.shift_a, s.shift_b, s.texture_a, s.texture_b)
    if s.estimated_inputs:
        harmony["basis"] = "Provisional local analysis estimates, not human-verified BPM, keys, or downbeats."
    if out.exists():
        raise MixError(f"Output folder already exists: {out}. Choose a new folder to preserve previous renders.")
    print("Measuring source loudness…", flush=True)
    meters = {"a": meter(a), "b": meter(b)}
    gains = {label: min(12, -18 - m["lufs"]) + getattr(s, "gain_" + label)
             for label, m in meters.items()}
    out.mkdir(parents=True)
    notes = [
        "Cues must mark the intended downbeats in the original files. This prototype does not detect beats, phrases, vocals, or keys.",
        "Constant-tempo 4/4 tracks only. Tempo drift, wrong BPM values, and incorrect cues require correction by the user.",
        "Listen for doubled kicks, smeared transients, vocal texture changes, and melodies that compete.",
        "Track gains are constant, based on whole-source integrated loudness. Export headroom uses static attenuation, not compression.",
    ]
    if s.estimated_inputs:
        notes.insert(0, "This draft uses estimated inputs. A stable detected beat grid does not prove the tracker found the correct beats or phrase starts.")
    if s.bass_swap:
        notes.append("Bass below the 180 Hz crossover changes hands over one bar at the overlap midpoint; upper bands crossfade across the whole overlap.")
    for label in ("a", "b"):
        change = (s.target_bpm / getattr(s, "bpm_" + label) - 1) * 100
        if abs(change) > 6 or abs(getattr(s, "shift_" + label)) > 2:
            notes.append(f"Track {label.upper()} uses a {change:+.1f}% tempo change and {getattr(s, 'shift_'+label):+d} semitone shift: audition this larger adjustment carefully.")
    with tempfile.TemporaryDirectory(prefix="djmix-") as temporary:
        work = Path(temporary)
        prepared = {}
        for label, source, info in (("a", a, info_a), ("b", b, info_b)):
            print(f"Processing track {label.upper()} with Rubber Band R3…", flush=True)
            prepared[label] = prepare(source, p[label], info["duration"], getattr(s, "bpm_" + label),
                                      s.target_bpm, getattr(s, "shift_" + label), s.formants,
                                      work, label, gains[label])
        print("Blending and checking true-peak headroom…", flush=True)
        float_mix = mix_buffers(prepared["a"], prepared["b"], p, s, work)
        mix_measurement = meter(float_mix)
        mix_attenuation = min(0, -1.1 - mix_measurement["true_peak_db"])
        export_wav(float_mix, out / "mix.wav", mix_attenuation)
        output_meter = meter(out / "mix.wav")
        if output_meter["true_peak_db"] > -0.99:
            raise MixError("Rendered output did not pass the -1 dBTP headroom check.")
        # Keep each source/processed pair equally attenuated, accounting for its own peaks.
        audition_gain = {}
        for label in ("a", "b"):
            processed_float = work / f"{label}-processed.wav"
            ffmpeg("-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(prepared[label]),
                   "-c:a", "pcm_f32le", str(processed_float))
            original_float = work / f"{label}-original.wav"
            peak = max(meter(processed_float)["true_peak_db"], meter(original_float)["true_peak_db"])
            audition_gain[label] = min(mix_attenuation, -1.1 - peak)
            export_wav(processed_float, out / f"{label}-processed.wav", audition_gain[label])
            export_wav(original_float, out / f"{label}-original.wav", audition_gain[label])
        output_info = probe(out / "mix.wav")
        if abs(output_info["duration"] - p["duration_seconds"]) > 2 / SR:
            raise MixError("Rendered mix duration did not match the planned sample count.")
        report = {"demo": demo, "settings": asdict(s), "timeline": p, "harmony": harmony,
                  "tracks": {"a": info_a, "b": info_b}, "source_measurements": meters,
                  "track_gain_db": gains, "mix_attenuation_db": mix_attenuation,
                  "audition_attenuation_db": audition_gain, "output": {**output_info, **output_meter},
                  "notes": notes}
    (out / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    report_html(report, out)
    print(f"Done: {out / 'mix.wav'}\nListening report: {out / 'report.html'}", flush=True)
    return report


def synth_track(output: Path, bpm: float, root: int, bars: int = 16, variant: int = 0) -> None:
    """A deliberately simple original EDM-like fixture, not a quality benchmark."""
    beat = 60 / bpm
    count = round(bars * 4 * beat * SR)
    t = np.arange(count) / SR
    beat_phase = (t / beat) % 1
    rng = np.random.default_rng(42 + variant)
    mono = np.zeros(count, dtype=np.float64)
    # Kick on each beat, snare on 2 and 4, short hats on eighth-notes.
    kick_t = (t % beat)
    kick_phase = 2 * np.pi * (48 * kick_t + 72 * 0.025 * (1 - np.exp(-kick_t / 0.025)))
    mono += 0.43 * np.sin(kick_phase) * np.exp(-kick_t / 0.055)
    noise = rng.standard_normal(count)
    bright = np.concatenate(([0.0], np.diff(noise)))
    hat_t = t % (beat / 2)
    mono += 0.027 * bright * np.exp(-hat_t / 0.013)
    snare_t = (t - beat) % (2 * beat)
    mono += 0.065 * noise * np.exp(-snare_t / 0.04)
    # Minor harmony and an offbeat bass. Root is a MIDI note (C3=48).
    frequency = lambda midi: 440 * 2 ** ((midi - 69) / 12)
    bass_env = np.maximum(0, np.sin(np.pi * beat_phase)) ** 2
    mono += 0.19 * np.sin(2 * np.pi * frequency(root - 12) * t) * bass_env
    melody = np.zeros(count)
    for interval in (0, 3, 7):
        freq = frequency(root + interval + (12 if variant else 0))
        melody += 0.028 * np.sin(2 * np.pi * freq * t)
        melody += 0.009 * np.sin(2 * np.pi * freq * 2 * t)
    pump = 0.2 + 0.8 * (1 - np.exp(-kick_t / 0.12))
    stereo = np.column_stack((mono + melody * pump, mono + np.roll(melody, 137) * pump))
    edge = np.minimum(np.clip(t / 0.005, 0, 1), np.clip((count / SR - t) / 0.015, 0, 1))
    stereo *= edge[:, None]
    with tempfile.TemporaryDirectory(prefix="dj-synth-") as temporary:
        raw = Path(temporary) / "synth.raw"
        stereo.astype("<f4").tofile(raw)
        export_wav(raw, output, raw=True)


def demo(out: Path) -> dict:
    require_tools()
    if out.exists():
        raise MixError(f"Demo folder already exists: {out}. Choose a new --out folder.")
    inputs = out / "sources"
    inputs.mkdir(parents=True)
    a, b = inputs / "A-C-minor-120.wav", inputs / "B-D-minor-124.wav"
    print("Generating original synthetic test tracks…", flush=True)
    synth_track(a, 120, 48)
    synth_track(b, 124, 50, variant=1)
    settings = Settings(bpm_a=120, bpm_b=124, target_bpm=122, cue_a=16, cue_b=0,
                        bars=8, context_bars=4, key_a="Cm", key_b="Dm", shift_b=-2)
    return render(a, b, out / "preview", settings, demo=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demonstration = commands.add_parser("demo", help="Generate and mix two original synthetic test tracks.")
    demonstration.add_argument("--out", type=Path, default=Path("outputs") / f"demo-{datetime.now():%Y%m%d-%H%M%S}")
    mixing = commands.add_parser("mix", help="Preview or export a two-track transition using explicit cues/BPMs.")
    mixing.add_argument("track_a", type=Path)
    mixing.add_argument("track_b", type=Path)
    mixing.add_argument("--bpm-a", type=number, required=True)
    mixing.add_argument("--bpm-b", type=number, required=True)
    mixing.add_argument("--target-bpm", type=number, required=True)
    mixing.add_argument("--cue-a", type=seconds, required=True, help="Overlap start in ORIGINAL track A, seconds or MM:SS.")
    mixing.add_argument("--cue-b", type=seconds, default=0, help="Incoming downbeat in ORIGINAL track B.")
    mixing.add_argument("--bars", type=int, default=16, help="Overlap in 4/4 bars (default 16).")
    mixing.add_argument("--context-bars", type=int, default=4, help="Preview bars before and after overlap.")
    mixing.add_argument("--key-a", help="Key of the overlapping section, e.g. 'D minor' or 'Dm'.")
    mixing.add_argument("--key-b")
    mixing.add_argument("--shift-a", type=int, default=0, help="Optional pitch shift for A in semitones. Default 0 keeps its original key.")
    mixing.add_argument("--shift-b", type=int, default=0, help="Optional pitch shift for B in semitones. Default 0 keeps its original key.")
    mixing.add_argument("--texture-a", choices=("musical", "percussive"), default="musical")
    mixing.add_argument("--texture-b", choices=("musical", "percussive"), default="musical")
    mixing.add_argument("--gain-a", type=number, default=0, help="Additional constant gain trim in dB.")
    mixing.add_argument("--gain-b", type=number, default=0)
    mixing.add_argument("--full", action="store_true", help="Include all of A before its cue and all of B after its cue.")
    mixing.add_argument("--estimated-inputs", action="store_true", help="Label BPM/key/cue settings as provisional local estimates in the listening report.")
    mixing.add_argument("--no-formants", action="store_true", help="Disable formant preservation for pitch shifting.")
    mixing.add_argument("--no-bass-swap", action="store_true", help="Use a full-band equal-power crossfade.")
    mixing.add_argument("--out", type=Path, required=True, help="A NEW output folder; existing folders are never overwritten.")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            demo(args.out)
        else:
            fields = {field: getattr(args, field) for field in Settings.__dataclass_fields__
                      if hasattr(args, field)}
            fields.update(formants=not args.no_formants, bass_swap=not args.no_bass_swap)
            render(args.track_a, args.track_b, args.out, Settings(**fields))
    except (MixError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
