"""Project model and offline multitrack rendering for the desktop application."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import os
import re
from pathlib import Path
import tempfile
from typing import Callable
import uuid

import numpy as np

import djmix


@dataclass
class Track:
    id: str
    path: str
    name: str
    duration: float
    bpm: float | None = None
    key: str = ""
    cue_in: float = 0
    cue_out: float = 0
    estimated: bool = True
    waveform: list[float] = field(default_factory=list)
    status: str = "Pending analysis"
    timing: dict = field(default_factory=dict)


@dataclass
class Clip:
    track_id: str
    cue_in: float
    cue_out: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    blend_bars: int = 8
    transpose: bool = False
    semitones: int = 0
    bass_swap: bool = True
    intro_percussive: bool = False
    outro_percussive: bool = False
    stabilize: bool = False

    @property
    def shift(self):
        return self.semitones if self.transpose else 0


@dataclass
class Session:
    name: str = "Untitled mix"
    target_bpm: float = 128
    tracks: dict[str, Track] = field(default_factory=dict)
    clips: list[Clip] = field(default_factory=list)

    def to_dict(self):
        return {"version": 2, **asdict(self)}

    @classmethod
    def from_dict(cls, value):
        if value.get("version") not in (1, 2):
            raise djmix.MixError("Unsupported project version.")
        result = cls(name=str(value["name"]), target_bpm=float(value["target_bpm"]),
                     tracks={k: Track(**v) for k, v in value["tracks"].items()},
                     clips=[Clip(**v) for v in value["clips"]])
        for track in result.tracks.values():
            if track.key:
                track.key = djmix.key_name(djmix.parse_key(track.key))
        # The project may intentionally contain missing files; rendering checks those.
        layout(result)
        return result

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)


def track_id(path: Path):
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]


def load_track(path: Path, cache: Path) -> Track:
    info = djmix.probe(path)
    identity = track_id(path)
    signature = [str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns]
    cached = cache / f"{identity}.json"
    if cached.is_file():
        try:
            data = json.loads(cached.read_text())
            if data["signature"] == signature:
                return Track(**data["track"])
        except (ValueError, KeyError, TypeError):
            pass
    raw = djmix.ffmpeg("-i", str(path), "-map", "0:a:0", "-ar", "8000", "-ac", "1", "-f", "f32le", "-").stdout
    samples = np.abs(np.frombuffer(raw, dtype="<f4"))
    size = 1400
    step = max(1, math.ceil(len(samples) / size))
    padded = np.pad(samples, (0, (-len(samples)) % step))
    peaks = padded.reshape(-1, step).max(axis=1)
    norm = max(float(np.percentile(peaks, 98)), 0.001)
    name = re.sub(r"^\d+[-_ ]+", "", path.stem).replace("_", " ").replace("-", " ")
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    track = Track(identity, str(path.resolve()), name, info["duration"], cue_out=info["duration"],
                  waveform=np.clip(peaks / norm, 0, 1).round(4).tolist())
    return track


def cache_track(track: Track, cache: Path):
    path = Path(track.path)
    cache.mkdir(parents=True, exist_ok=True)
    data = {"signature": [str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns], "track": asdict(track)}
    (cache / f"{track.id}.json").write_text(json.dumps(data, allow_nan=False))


def estimate_track(track: Track, timing_cache=None, progress=lambda message: None) -> Track:
    from analyze_audio import analyze
    duration = min(60.0, track.duration)
    result = analyze(Path(track.path), 0, duration, bars=8 if duration >= 25 else 4)
    windows = result["candidate_windows"]
    if not windows:
        raise djmix.MixError("No stable candidate grid. Enter BPM and cue points manually.")
    # Prefer an early stable passage. This is a suggestion, not phrase detection.
    stable = [w for w in windows if w["grid_residual_p95_ms"] < 25] or windows
    chosen = min(stable[:8], key=lambda w: w["grid_residual_p95_ms"])
    track.bpm = chosen["bpm"]
    from studio_timing import analyze as analyze_timing
    track.timing = analyze_timing(Path(track.path), track.bpm, track.duration, timing_cache, progress)
    if track.timing['bpm'] is not None:
        track.bpm = track.timing['bpm']
    candidates = result["section_key_candidates"]
    track.key = candidates[0]["key"] if candidates else ""
    track.cue_in = chosen["cue_seconds"]
    bar = 240 / track.bpm
    track.cue_out = min(track.duration, track.cue_in + math.floor((track.duration - track.cue_in) / bar) * bar)
    track.status = "Estimated across song · review cues" if track.timing['bpm'] else "Timing uncertain · review BPM and cues"
    return track


def layout(session: Session) -> list[dict]:
    if not math.isfinite(session.target_bpm) or not 20 <= session.target_bpm <= 300:
        raise djmix.MixError("Set a project tempo between 20 and 300 BPM.")
    result = []
    ids = set()
    for index, clip in enumerate(session.clips):
        if clip.id in ids:
            raise djmix.MixError("The project contains duplicate clip identities.")
        ids.add(clip.id)
        track = session.tracks.get(clip.track_id)
        if track is None or track.bpm is None or not math.isfinite(track.bpm) or not 20 <= track.bpm <= 300:
            raise djmix.MixError("Analyze the song or enter its BPM before adding it to the arrangement.")
        if not all(math.isfinite(v) for v in (track.duration, clip.cue_in, clip.cue_out)):
            raise djmix.MixError("Track and cue durations must be finite.")
        if not 0 <= clip.cue_in < clip.cue_out <= track.duration + 1 / djmix.SR:
            raise djmix.MixError(f"Invalid in/out points for {track.name}.")
        if not isinstance(clip.blend_bars, int) or not 1 <= clip.blend_bars <= 32:
            raise djmix.MixError("Blend length must be between 1 and 32 bars.")
        if not isinstance(clip.semitones, int) or not -12 <= clip.semitones <= 12:
            raise djmix.MixError("Pitch adjustment must be between -12 and +12 semitones.")
        from studio_timing import corrected_time
        length = round((corrected_time(track, clip, clip.cue_out) - corrected_time(track, clip, clip.cue_in))
                       * track.bpm / session.target_bpm * djmix.SR)
        overlap = round(clip.blend_bars * 240 / session.target_bpm * djmix.SR) if index else 0
        if index and (overlap >= length or overlap + result[-1]["incoming"] >= result[-1]["length"]):
            raise djmix.MixError("A blend is longer than the available audio. Shorten it or widen the clip's in/out points.")
        start = result[-1]["end"] - overlap if index else 0
        result.append({"clip_id": clip.id, "start": start, "end": start + length,
                       "length": length, "incoming": overlap})
    return result


def preview_range(session: Session, index: int):
    positions = layout(session)
    if index < 1 or index >= len(positions):
        raise djmix.MixError("Select an incoming track to preview its blend.")
    position = positions[index]
    context = round(4 * 240 / session.target_bpm * djmix.SR)
    return max(0, position["start"] - context), min(positions[-1]["end"], position["start"] + position["incoming"] + context)


def blend_advice(session: Session, outgoing: Clip, incoming: Clip):
    return djmix.harmonic_advice(session.tracks[outgoing.track_id].key,
                                session.tracks[incoming.track_id].key,
                                outgoing.shift, incoming.shift,
                                "percussive" if outgoing.outro_percussive else "musical",
                                "percussive" if incoming.intro_percussive else "musical")


def suggest_order(session: Session) -> list[Clip]:
    """Keep the opening song; greedily prefer compatible keys and nearby source BPMs.

    A proposal only: no mutation, cue changes, or automatic transposition.
    """
    if len(session.clips) < 3:
        return list(session.clips)
    ordered, remaining = [session.clips[0]], list(session.clips[1:])
    while remaining:
        previous = ordered[-1]
        a = session.tracks[previous.track_id]
        def score(clip):
            b = session.tracks[clip.track_id]
            ka = djmix.transpose(djmix.parse_key(a.key), previous.shift)
            kb = djmix.transpose(djmix.parse_key(b.key), clip.shift)
            compatible = previous.outro_percussive or clip.intro_percussive or (ka and kb and djmix.relationship(ka, kb))
            penalty = 0 if compatible else (1 if not ka or not kb else 2)
            return penalty, abs(math.log2(b.bpm / a.bpm))
        chosen = min(remaining, key=score)
        ordered.append(chosen); remaining.remove(chosen)
    return ordered


def _weights(n: np.ndarray, start: int, length: int, incoming: bool, bass_swap: bool, target: float):
    progress = np.clip((n - start) / max(1, length - 1), 0, 1)
    high = np.sin(progress * np.pi / 2) if incoming else np.cos(progress * np.pi / 2)
    if not bass_swap:
        return high, high
    width = min(length, round(240 / target * djmix.SR))
    p = np.clip((n - start - (length - width) / 2) / max(1, width - 1), 0, 1)
    low = (1 - np.cos(p * np.pi)) / 2
    return (low if incoming else 1 - low), high


def render_session(session: Session, destination: Path, *, region=None,
                   progress: Callable[[str], None] = lambda message: None, cache_dir: Path | None = None) -> dict:
    """Render from original sources once. No recursively reprocessed intermediate mixes."""
    djmix.require_tools()
    positions = layout(session)
    if not positions:
        raise djmix.MixError("Add songs to the timeline first.")
    start, end = region if region else (0, positions[-1]["end"])
    if not 0 <= start < end <= positions[-1]["end"]:
        raise djmix.MixError("Invalid preview range.")
    if destination.resolve() in {Path(t.path).resolve() for t in session.tracks.values()}:
        raise djmix.MixError("Choose an export filename different from your source songs.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    from studio_cache import AudioCache
    cache = AudioCache(cache_dir) if cache_dir else None
    reused, processed = 0, 0
    # Stage on the destination volume so final replacement is atomic.
    with tempfile.TemporaryDirectory(prefix="dj-render-", dir=destination.parent) as directory:
        work = Path(directory)
        prepared, arrays = [], []
        try:
            for index, (clip, position) in enumerate(zip(session.clips, positions)):
                left, right = max(start, position["start"]), min(end, position["end"])
                if left >= right:
                    continue
                track = session.tracks[clip.track_id]
                progress(f"Checking {track.name} ({index + 1}/{len(session.clips)})")
                actual = djmix.probe(Path(track.path))
                if abs(actual["duration"] - track.duration) > 0.1:
                    raise djmix.MixError(f"{track.name} changed on disk. Re-import it before rendering.")
                ratio = track.bpm / session.target_bpm
                selection = {"start": clip.cue_in + (left - position["start"]) / djmix.SR / ratio,
                             "end": clip.cue_in + (right - position["start"]) / djmix.SR / ratio,
                             "frames": right - left}
                measurement = cache.measurement(Path(track.path)) if cache else djmix.meter(Path(track.path))
                gain = min(12, -18 - measurement["lufs"])
                local_start, local_end = left-position["start"], right-position["start"]
                from studio_timing import mapping, prepare as prepare_timing, signature
                warped = mapping(track, clip) is not None
                if clip.stabilize and track.timing.get('source') != signature(track.path):
                    raise djmix.MixError(f"{track.name} changed on disk. Analyze its timing again.")
                family = cache.family(track, clip, session.target_bpm, gain) if cache else None
                cached = cache.get(family, local_start, local_end) if cache else None
                if cached:
                    progress(f"Reusing cached audio · {track.name}")
                    low, high = djmix.raw_audio(cached[0]), djmix.raw_audio(cached[1])
                    cache_offset = cached[2]; reused += 1
                else:
                    progress(f"Processing {track.name} ({index + 1}/{len(session.clips)})")
                    if warped:
                        progress(f"Stabilizing {track.name} · preparing the clip for reuse")
                        raw = prepare_timing(track, clip, session.target_bpm, work, str(index), gain)
                    else:
                        raw = djmix.prepare(Path(track.path), selection, track.duration, track.bpm,
                                            session.target_bpm, clip.shift, True, work, str(index), gain)
                    low, high = djmix.split_bands(raw, work, str(index))
                    cache_offset = local_start if warped else 0; processed += 1
                arrays.extend([low, high])
                if not cached:
                    if cache:
                        cache.put(family, 0 if warped else local_start,
                                  position['length'] if warped else local_end, Path(low.filename), Path(high.filename))
                prepared.append((index, left, right, low, high, cache_offset))
            progress("Blending transitions")
            raw_mix = work / "arrangement.raw"
            with raw_mix.open("wb") as handle:
                for offset in range(start, end, djmix.BLOCK):
                    n = np.arange(offset, min(offset + djmix.BLOCK, end))
                    chunk = np.zeros((len(n), 2), dtype=np.float64)
                    for index, left, right, low, high, cache_offset in prepared:
                        mask = (n >= left) & (n < right)
                        if not mask.any():
                            continue
                        active = n[mask]
                        pos, clip = positions[index], session.clips[index]
                        lo, hi = np.ones(len(active)), np.ones(len(active))
                        if index:
                            a, b = _weights(active, pos["start"], pos["incoming"], True, clip.bass_swap, session.target_bpm)
                            lo *= a; hi *= b
                        if index + 1 < len(positions):
                            nxt, next_clip = positions[index + 1], session.clips[index + 1]
                            a, b = _weights(active, nxt["start"], nxt["incoming"], False, next_clip.bass_swap, session.target_bpm)
                            lo *= a; hi *= b
                        chunk[mask] += low[active-left+cache_offset] * lo[:, None] + high[active-left+cache_offset] * hi[:, None]
                    edge = np.minimum(np.clip((n-start) / (0.005*djmix.SR), 0, 1),
                                      np.clip((end-1-n) / (0.005*djmix.SR), 0, 1))
                    chunk *= edge[:, None]
                    if not np.isfinite(chunk).all():
                        raise djmix.MixError("Invalid samples in rendered mix.")
                    handle.write(chunk.astype("<f4").tobytes())
            progress("Checking export headroom")
            floating = work / "mix-float.wav"
            djmix.ffmpeg("-f", "f32le", "-ar", str(djmix.SR), "-ac", "2", "-i", str(raw_mix), "-c:a", "pcm_f32le", str(floating))
            measurement = djmix.meter(floating)
            attenuation = min(0, -1.1 - measurement["true_peak_db"])
            final = work / "final.wav"
            djmix.export_wav(floating, final, attenuation)
            check = djmix.meter(final)
            actual_duration = djmix.probe(final)["duration"]
            if check["true_peak_db"] > -0.99 or abs(actual_duration - (end-start)/djmix.SR) > 2/djmix.SR:
                raise djmix.MixError("Export failed its duration or headroom check.")
            os.replace(final, destination)
            return {"path": str(destination), "start": start/djmix.SR, "duration": actual_duration,
                    "reused_tracks": reused, "processed_tracks": processed, **check}
        finally:
            # Explicit closure is necessary for Windows to remove mapped temporary files.
            for array in arrays:
                array._mmap.close()
            if cache:
                cache.prune()
