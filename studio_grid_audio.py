"""Beat-grid auditioning against the original song on one PCM clock.

Decoding reserves click headroom with a single fixed gain per song. GridMixer
adds grid clicks to the source inside the same read() call that feeds the
single QAudioSink, so audio and clicks can never drift apart. Grid/click
changes never seek, pause, or restart the sink.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtMultimedia import QAudioSink

import djmix
from studio_ab_audio import ABMixer, ABTransport, RATE
from studio_cache import digest, source_signature
from studio_storage import managed_cache


GRID_PIPELINE_VERSION = 1  # Bump when decode, gain, or cache layout semantics change.
HEADROOM_PEAK = 0.65
CLICK_PEAK = 0.20
CLICK_FRAMES = round(0.020 * RATE)  # Each click is exactly 20 ms.
BEAT_FREQ = 2000.0
DOWNBEAT_FREQ = 3200.0  # Brighter accent tone for downbeats.
_CLICK_DECAY = 0.0035


def _prototype(freq):
    t = np.arange(CLICK_FRAMES, dtype=np.float64) / RATE
    return (CLICK_PEAK * np.sin(2 * np.pi * freq * t) * np.exp(-t / _CLICK_DECAY)).astype(np.float32)


_BEAT_CLICK = _prototype(BEAT_FREQ)
_DOWNBEAT_CLICK = _prototype(DOWNBEAT_FREQ)


def _read_cached(entry: Path, signature):
    """Return the cached report dict, or None when missing or stale."""
    try:
        report = json.loads((entry / "report.json").read_text())
        audio = entry / "audio.wav"
        if (report.get("signature") != signature
                or report.get("pipeline") != GRID_PIPELINE_VERSION
                or int(report.get("frames", 0)) <= 0
                or not audio.is_file()):
            return None
        info = sf.info(str(audio))
        if info.samplerate != RATE or info.channels != 2 or info.frames != report["frames"]:
            return None
        return {"path": str(audio), "frames": int(report["frames"]), "gain": float(report["gain"])}
    except Exception:
        return None


@managed_cache
def prepare_source(path, cache_dir, progress=lambda message: None):
    """Decode a song to 48 kHz stereo float WAV with click headroom.

    Returns {'path', 'frames', 'gain'} where gain is the one fixed gain for the
    whole song (1.0 when the source already peaks at or below .65). The gain is
    baked into the cached file, so click toggles and grid edits cannot change
    it. progress is called with "decode", "headroom", then "done" ("done" only
    on a cache hit). A progress failure cancels preparation and publishes
    nothing. Streams in blocks; never holds two complete float copies.
    """
    source = Path(path)
    if not source.is_file():
        raise djmix.MixError(f"Audio file not found: {source}")
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    signature = source_signature(source)
    entry = cache / digest([signature, GRID_PIPELINE_VERSION, RATE])
    cached = _read_cached(entry, signature)
    if cached is not None:
        progress("done")
        return cached
    staging = cache / (".pending-" + uuid.uuid4().hex)
    try:
        staging.mkdir(parents=True, exist_ok=False)
        from studio_storage import require_space
        require_space(staging, djmix.probe(source)['duration'] * RATE * 16, 'prepare beat-check audio')
        decoded = staging / "decoded.wav"
        progress("decode")
        # Plain decode only: no pitch, tempo, or timing changes.
        djmix.ffmpeg("-i", str(source), "-map", "0:a:0", "-ar", str(RATE), "-ac", "2",
                     "-c:a", "pcm_f32le", str(decoded))
        progress("headroom")
        with sf.SoundFile(str(decoded)) as handle:
            if handle.samplerate != RATE or handle.channels != 2 or len(handle) <= 0:
                raise djmix.MixError(f"Could not decode audible stereo audio from {source.name}")
            frames = len(handle)
            peak = 0.0
            for block in handle.blocks(blocksize=65536, dtype="float32", always_2d=True):
                if not np.isfinite(block).all():
                    raise djmix.MixError(f"Non-finite samples decoded from {source.name}")
                if block.size:
                    peak = max(peak, float(np.max(np.abs(block))))
        gain = 1.0 if peak <= HEADROOM_PEAK or peak == 0.0 else HEADROOM_PEAK / peak
        final = staging / "audio.wav"
        if gain == 1.0:
            os.replace(decoded, final)
        else:
            with sf.SoundFile(str(decoded)) as src, sf.SoundFile(
                    str(final), "w", samplerate=RATE, channels=2, subtype="FLOAT") as dst:
                for block in src.blocks(blocksize=65536, dtype="float32", always_2d=True):
                    dst.write(block * gain)
            decoded.unlink(missing_ok=True)
        if sf.info(str(final)).frames != frames:
            raise djmix.MixError(f"Headroom render length mismatch for {source.name}")
        (staging / "report.json").write_text(json.dumps(
            {"frames": frames, "gain": gain, "signature": signature,
             "pipeline": GRID_PIPELINE_VERSION}, allow_nan=False))
        if source_signature(source) != signature:
            raise djmix.MixError("The source changed during preparation; nothing was cached.")
        progress("done")  # Check cancellation before publishing the completed cache.
        if entry.exists():
            rival = _read_cached(entry, source_signature(source))
            if rival is not None:
                return rival
            shutil.rmtree(entry, ignore_errors=True)
        os.replace(staging, entry)  # Atomically publish only the complete entry.
        return {"path": str(entry / "audio.wav"), "frames": frames, "gain": gain}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


class GridMixer:
    """Source audio plus grid clicks mixed on one sample cursor.

    audio is a finite Nx2 float32 array (a memmap is fine; it is never copied).
    Clicks are prerendered once per set_grid into a single mono track at exact
    absolute sample positions, so every read() costs O(count) and tails survive
    chunk splits and seeks. set_grid/set_click never move .frame.
    """

    def __init__(self, audio):
        if not isinstance(audio, np.ndarray):  # A memmap stays a memmap; never copied.
            audio = np.asarray(audio)
        if audio.ndim != 2 or audio.shape[1] != 2 or len(audio) == 0:
            raise ValueError("Grid audio must be a non-empty stereo frame array.")
        if not np.isfinite(audio).all():
            raise ValueError("Grid audio must be finite.")
        self.audio = audio
        self.frame = 0
        self.click_mix = self.target_click = 0.0
        self.beats = ()
        self.downbeats = ()
        self._click = np.zeros(len(audio), dtype=np.float32)

    @property
    def frames(self):
        return len(self.audio)

    def _as_samples(self, moments, name):
        try:
            values = [float(v) for v in moments]
        except TypeError:
            raise ValueError(f"{name} must be a sequence of seconds.")
        for value in values:
            if not math.isfinite(value):
                raise ValueError(f"{name} must contain only finite seconds.")
        for previous, current in zip(values, values[1:]):
            if current < previous:
                raise ValueError(f"{name} must be sorted ascending.")
        samples = []
        for value in values:
            index = int(round(value * RATE)) if value >= 0 else -1
            if index < 0 or index >= self.frames:
                raise ValueError(f"{name} moment {value!r} is outside the audio.")
            samples.append(index)
        return values, samples

    def set_grid(self, beats, downbeats):
        beat_values, beat_idx = self._as_samples(beats, "beats")
        down_values, down_idx = self._as_samples(downbeats, "downbeats")
        if tuple(beat_values) == self.beats and tuple(down_values) == self.downbeats:
            return
        events = sorted([(i, False) for i in beat_idx] + [(i, True) for i in down_idx])
        merged = []
        for index, accent in events:
            if merged and index - merged[-1][0] < CLICK_FRAMES:
                merged[-1][1] = merged[-1][1] or accent  # One click; downbeat wins.
            else:
                merged.append([index, accent])
        track = np.zeros(self.frames, dtype=np.float32)
        for index, accent in merged:
            proto = _DOWNBEAT_CLICK if accent else _BEAT_CLICK
            n = min(CLICK_FRAMES, self.frames - index)
            track[index:index + n] += proto[:n]
        np.clip(track, -CLICK_PEAK, CLICK_PEAK, out=track)
        self._click = track
        self.beats, self.downbeats = tuple(beat_values), tuple(down_values)

    def set_click(self, enabled):
        self.target_click = float(bool(enabled))

    def seek(self, frame):
        self.frame = max(0, min(int(frame), self.frames))
        self.click_mix = self.target_click

    def read(self, count):
        end = min(self.frames, self.frame + int(count))
        n = end - self.frame
        if n <= 0:
            return b""
        ramp = ABMixer.ramp(self.click_mix, self.target_click, n)
        output = (self.audio[self.frame:end].astype(np.float32, copy=False)
                  + (self._click[self.frame:end] * ramp)[:, None])
        self.frame = end
        self.click_mix = float(ramp[-1])
        return np.asarray(output, dtype="<f4").tobytes()


class GridTransport(ABTransport):
    """ABTransport clock playing one prepared song plus its grid clicks."""

    def __init__(self, parent=None, sink_factory=QAudioSink):
        super().__init__(parent, sink_factory=sink_factory)
        self._audio = None

    def load(self, report):
        """Load a prepare_source() report. Releases any previous source.

        Limit: the whole song is read into one RAM array (~8 bytes/frame, so a
        ten-minute song needs ~230 MB). Memmapping the WAV header was judged
        too fragile for this bounded component. There are no open file handles
        afterwards, so the cached file can be deleted right after close(), even
        on Windows.
        """
        self._set_playing(False)
        self._reset()
        self._release_source()
        self.mixer = None
        audio, rate = sf.read(str(report["path"]), dtype="float32", always_2d=True)
        if rate != RATE or audio.shape[1] != 2 or len(audio) == 0 or not np.isfinite(audio).all():
            raise ValueError("Grid sources must contain finite stereo audio at 48 kHz.")
        if len(audio) != int(report["frames"]):
            raise ValueError("The grid report does not match its decoded audio.")
        self._audio = audio
        self.mixer = GridMixer(audio)
        self.mixer.set_click(self.click)
        self.mixer.seek(0)
        self.origin = 0
        self.last_reported = 0
        self.positionChanged.emit(0)

    def set_grid(self, beats, downbeats):
        if self.mixer is None:
            raise RuntimeError("Load a grid source before setting the grid.")
        self.mixer.set_grid(beats, downbeats)

    def close(self):
        super().close()
        self.mixer = None
        self._release_source()

    def _release_source(self):
        self._audio = None
