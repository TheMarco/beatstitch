"""Mastering A/B audio: one PCM cursor, three aligned variants.

Current is A. B is ``matched`` (mastered attenuated to current loudness)
when loudness matching is on, otherwise the louder ``mastered`` file.
Switching A/B or the match checkbox only retargets a short linear
crossfade in the mixer; it never reloads files, seeks, or restarts the sink.
Passage selection and looping operate on in-memory slices of the full arrays.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from studio_ab_audio import ABTransport, FADE_FRAMES, FRAME_BYTES, RATE

VARIANTS = ("current", "mastered", "matched")
FILES = {"current": "current.wav", "mastered": "mastered.wav", "matched": "matched.wav"}


def resolve_b_variant(match_loudness: bool) -> str:
    """B is the loudness-matched master when matching, else the raw master."""
    return "matched" if match_loudness else "mastered"


def effective_variant(selection: str, match_loudness: bool) -> str:
    """Map UI selection (``A``/``B`` or a variant name) to a variant name."""
    if selection in ("A", "current"):
        return "current"
    if selection in ("B", "mastered", "matched"):
        return resolve_b_variant(match_loudness)
    raise ValueError(f"Unknown selection: {selection!r}")


@dataclass
class Segment:
    name: str
    start: float
    end: float


@dataclass
class LevelInfo:
    lufs: float
    true_peak_db: float


@dataclass
class MasteringReport:
    name: str
    duration: float
    segments: list
    current: LevelInfo
    mastered: LevelInfo
    matched: LevelInfo
    matched_gain_db: float
    processing_summary: list
    folder: Path = None
    frames: int = 0
    current_gain_db: float = 0.0


@dataclass
class MasteringData:
    report: MasteringReport
    current: np.ndarray
    mastered: np.ndarray
    matched: np.ndarray

    def array_for(self, variant: str) -> np.ndarray:
        if variant not in VARIANTS:
            raise ValueError(f"Unknown variant: {variant!r}")
        return getattr(self, variant)

    def close(self):
        for variant in VARIANTS:
            audio = self.array_for(variant)
            if isinstance(audio, PCMView): audio.file.close()


class PCMView:
    """A stereo file passage with NumPy-style bounded reads, never a full decode."""
    ndim = 2

    def __init__(self, file, start=0, end=None):
        self.file, self.start = file, int(start)
        self.end = len(file) if end is None else int(end)
        self.shape = (self.end - self.start, 2)

    def __len__(self): return self.shape[0]

    def read_range(self, start, count):
        from studio_process import check_cancelled
        check_cancelled()
        if not 0 <= start <= start + count <= len(self):
            raise ValueError('Playback read is outside the selected passage.')
        self.file.seek(self.start + int(start))
        audio = self.file.read(int(count), dtype='float32', always_2d=True)
        if len(audio) != count or not np.isfinite(audio).all():
            raise ValueError('The prepared comparison audio is missing or invalid.')
        return audio

    def __getitem__(self, selection):
        if isinstance(selection, slice):
            start, end, step = selection.indices(len(self))
            if step != 1: raise ValueError('Audio passages need a continuous range.')
            return PCMView(self.file, self.start + start, self.start + end)
        indices = np.asarray(selection, dtype=np.int64)
        if indices.ndim != 1: raise ValueError('Playback requires one-dimensional sample positions.')
        if not len(indices): return np.empty((0, 2), dtype=np.float32)
        breaks = np.r_[0, np.flatnonzero(np.diff(indices) != 1) + 1, len(indices)]
        chunks = [self.read_range(int(indices[left]), int(right - left))
                  for left, right in zip(breaks[:-1], breaks[1:])]
        return chunks[0] if len(chunks) == 1 else np.concatenate(chunks)

    def peaks(self, buckets=1024):
        count = min(int(buckets), len(self))
        result = np.zeros(count, dtype=np.float32)
        # Map chunks to fixed waveform bins without decoding a full passage.
        for start in range(0, len(self), 65536):
            audio = self.read_range(start, min(65536, len(self) - start))
            power = np.max(np.abs(audio), axis=1)
            bins = np.minimum(count - 1, np.arange(start, start + len(audio)) * count // len(self))
            np.maximum.at(result, bins, power)
        return result


def _finite_number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"report.json: {what} must be a number.")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"report.json: {what} must be finite.")
    return out


def _level_info(raw, what: str) -> LevelInfo:
    if not isinstance(raw, dict):
        raise ValueError(f"report.json: {what} must be an object with lufs/true_peak_db.")
    return LevelInfo(_finite_number(raw.get("lufs"), f"{what}.lufs"),
                     _finite_number(raw.get("true_peak_db"), f"{what}.true_peak_db"))


def parse_report(raw, folder: Path = None) -> MasteringReport:
    if not isinstance(raw, dict):
        raise ValueError("report.json must contain a JSON object.")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("report.json: name must be a non-empty string.")
    duration = _finite_number(raw.get("duration"), "duration")
    if duration <= 0:
        raise ValueError("report.json: duration must be positive.")
    segments_raw = raw.get("segments")
    if not isinstance(segments_raw, list):
        raise ValueError("report.json: segments must be a list.")
    segments = []
    for i, item in enumerate(segments_raw):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError(f"report.json: segments[{i}].name must be a non-empty string.")
        start = _finite_number(item.get("start"), f"segments[{i}].start")
        end = _finite_number(item.get("end"), f"segments[{i}].end")
        if not (0 <= start < end <= duration + 1e-6):
            raise ValueError(f"report.json: segments[{i}] must satisfy 0 <= start < end <= duration.")
        segments.append(Segment(item["name"], start, end))
    summary = raw.get("processing_summary")
    if not isinstance(summary, list) or not all(isinstance(s, str) for s in summary):
        raise ValueError("report.json: processing_summary must be a list of strings.")
    return MasteringReport(
        name=name, duration=duration, segments=segments,
        current=_level_info(raw.get("current"), "current"),
        mastered=_level_info(raw.get("mastered"), "mastered"),
        matched=_level_info(raw.get("matched"), "matched"),
        matched_gain_db=_finite_number(raw.get("matched_gain_db"), "matched_gain_db"),
        processing_summary=list(summary), folder=folder,
        current_gain_db=_finite_number(raw.get('current_gain_db', 0), 'current_gain_db'))


def load_report(folder) -> MasteringReport:
    folder = Path(folder)
    path = folder / "report.json"
    if not folder.is_dir():
        raise ValueError(f"Mastering folder not found: {folder}")
    if not path.is_file():
        raise ValueError(f"Missing report.json in {folder}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Could not read report.json: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"report.json is not valid JSON: {exc}") from exc
    return parse_report(raw, folder)


def load_mastering_folder(folder) -> MasteringData:
    folder = Path(folder)
    report = load_report(folder)
    arrays = {}
    try:
        for variant, filename in FILES.items():
            path = folder / filename
            if not path.is_file(): raise ValueError(f'Missing {filename} in {folder}')
            file = sf.SoundFile(path)
            arrays[variant] = PCMView(file)
            if file.samplerate != RATE or file.channels != 2 or not len(file):
                raise ValueError(f'{filename} must be non-empty stereo audio at 48 kHz.')
        if len({len(a) for a in arrays.values()}) != 1:
            raise ValueError('All comparison files must have identical frame counts.')
        frames = len(arrays['current'])
        if abs(frames / RATE - report.duration) > 1 / RATE + 1e-6:
            raise ValueError('report.json duration does not match the WAV files.')
        report.frames = frames
        return MasteringData(report, arrays['current'], arrays['mastered'], arrays['matched'])
    except BaseException:
        for audio in arrays.values(): audio.file.close()
        raise


def prepare_playback(folder, progress=lambda message: None):
    """Validate and cache waveform peaks in the worker, before the audio sink starts."""
    data = load_mastering_folder(folder)
    try:
        passages = [(0, len(data.current))] + [(round(s.start * RATE), round(s.end * RATE))
                                             for s in data.report.segments]
        peaks = {}
        for start, end in passages:
            progress('Preparing the comparison waveforms; audio is streamed from disk')
            peaks[(start, end)] = {v: data.array_for(v)[start:end].peaks() for v in VARIANTS}
        return data, peaks
    except BaseException:
        data.close(); raise


class MasteringMixer:
    """Three-way mixer on one monotonic cursor with short linear fades."""

    ORDER = VARIANTS

    def __init__(self, current, mastered, matched, loop=False):
        arrays = [a if isinstance(a, PCMView) else np.asarray(a) for a in (current, mastered, matched)]
        first = arrays[0]
        if first.ndim != 2 or first.shape[1] != 2 or not len(first):
            raise ValueError("Mastering variants must be non-empty stereo arrays.")
        for a in arrays:
            if a.shape != first.shape or (not isinstance(a, PCMView) and not np.isfinite(a).all()):
                raise ValueError("Mastering variants must have matching finite stereo frames.")
        self.arrays = dict(zip(self.ORDER, arrays))
        self.length = len(first)
        self.frame = 0
        self.loop = bool(loop)
        self.stop_at = self.length
        self.weights = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        self.target = self.weights.copy()
        self.variant = "current"
        self.remaining = 0

    @property
    def frames(self):
        return math.inf if self.loop else self.stop_at

    def _one_hot(self, variant):
        if variant not in self.ORDER:
            raise ValueError(f"Unknown variant: {variant!r}")
        out = np.zeros(3, dtype=np.float64)
        out[self.ORDER.index(variant)] = 1.0
        return out

    def select(self, variant):
        target = self._one_hot(variant)
        if not np.array_equal(target, self.target):
            self.target = target
            self.remaining = FADE_FRAMES
        self.variant = variant

    def set_loop(self, enabled):
        self.loop = bool(enabled)
        if not self.loop:
            self.stop_at = max(1, math.ceil(self.frame / self.length)) * self.length

    def seek(self, frame):
        self.frame = max(0, int(frame))
        if self.frame == 0:
            self.stop_at = self.length
        if not self.loop:
            self.frame = min(self.frame, self.stop_at)
        self.weights = self.target.copy()
        self.remaining = 0

    def read(self, frames):
        count = int(min(int(frames), self.frames - self.frame))
        if count <= 0:
            return b""
        indices = np.arange(self.frame, self.frame + count) % self.length
        # One common ramp keeps weights summing to one even when the user
        # switches among all three variants before a fade has completed.
        amount = np.minimum(np.arange(1, count + 1) / max(1, self.remaining), 1)
        ramps = self.weights[:, None] + (self.target - self.weights)[:, None] * amount
        output = sum(self.arrays[name][indices] * ramps[k][:, None]
                     for k, name in enumerate(self.ORDER))
        self.weights = ramps[:, -1]
        self.remaining = max(0, self.remaining - count)
        self.frame += count
        return np.asarray(output, dtype="<f4").tobytes()


class MasteringTransport(ABTransport):
    """ABTransport with mastering variants, match toggle, passages and loop."""

    def __init__(self, parent=None, sink_factory=None, **kwargs):
        if sink_factory is not None:
            kwargs["sink_factory"] = sink_factory
        super().__init__(parent, **kwargs)
        self.variant = "current"
        self.selection = "A"
        self.match_loudness = True
        self.loop = False
        self.data: MasteringData = None
        self.report: MasteringReport = None
        self.passage_name = "Whole mix"
        self._passage = (0, 0)  # (start_frame, end_frame) into full arrays

    @property
    def b_variant(self) -> str:
        return resolve_b_variant(self.match_loudness)

    @property
    def effective(self) -> str:
        return effective_variant(self.selection, self.match_loudness)

    # -- loading ---------------------------------------------------------
    def load(self, folder):
        data = load_mastering_folder(folder)
        self.load_data(data)

    def load_data(self, data: MasteringData):
        self._set_playing(False)
        self._reset()
        self.mixer = None
        if self.data is not None and self.data is not data: self.data.close()
        self.data = data
        self.report = data.report
        self._passage = (0, len(data.current))
        self.passage_name = "Whole mix"
        self._build_mixer()
        self.origin, self.last_reported = 0, -100
        self.positionChanged.emit(0)

    def _slice(self, variant):
        start, end = self._passage
        return self.data.array_for(variant)[start:end]

    def _build_mixer(self):
        self.mixer = MasteringMixer(self._slice("current"), self._slice("mastered"),
                                    self._slice("matched"), loop=self.loop)
        self.mixer.select(self.effective)
        self.mixer.seek(0)

    def set_passage(self, start_frame: int, end_frame: int, name: str = ""):
        """Explicit action: move to an in-memory slice; may reset the sink."""
        if self.data is None:
            return
        total = len(self.data.current)
        start = max(0, min(int(start_frame), total - 1))
        end = max(start + 1, min(int(end_frame), total))
        was_playing = self.playing
        self._set_playing(False)
        self._reset()
        self._passage = (start, end)
        self.passage_name = name or self.passage_name
        self._build_mixer()
        self.origin, self.last_reported = 0, -100
        self.positionChanged.emit(0)
        if was_playing:
            self.play()

    # -- variant switching (never touches the sink) ----------------------
    def select(self, variant):
        if variant == "current":
            self.selection, self.match_loudness = "A", self.match_loudness
        elif variant == "mastered":
            self.selection, self.match_loudness = "B", False
        elif variant == "matched":
            self.selection, self.match_loudness = "B", True
        elif variant in ("A", "B"):
            self.selection = variant
        else:
            raise ValueError(f"Unknown variant: {variant!r}")
        self.variant = self.effective
        if self.mixer is not None:
            self.mixer.select(self.variant)

    def set_selection(self, selection: str):
        if selection not in ("A", "B"):
            raise ValueError(f"Unknown selection: {selection!r}")
        self.selection = selection
        self.variant = self.effective
        if self.mixer is not None:
            self.mixer.select(self.variant)

    def set_match_loudness(self, enabled: bool):
        self.match_loudness = bool(enabled)
        self.variant = self.effective
        if self.mixer is not None:
            self.mixer.select(self.variant)

    def set_loop(self, enabled):
        self.loop = bool(enabled)
        if self.mixer is not None:
            self.mixer.set_loop(enabled)

    # -- position ----------------------------------------------------------
    def position(self):
        if self.mixer is None:
            return 0
        frame = self.position_frame()
        local = (self.mixer.length if not self.mixer.loop and frame >= self.mixer.stop_at
                 else frame % self.mixer.length)
        return round(local * 1000 / RATE)

    def seek(self, milliseconds):
        if self.mixer is not None:
            last_frame = self.mixer.length - (1 if self.mixer.loop else 0)
            milliseconds = max(0, min(milliseconds, last_frame * 1000 / RATE))
            self.mixer.stop_at = self.mixer.length
        super().seek(milliseconds)

    def _pump(self):
        try:
            super()._pump()
        except (OSError, RuntimeError, ValueError) as exc:
            self._fail(f'Comparison playback stopped: {exc}')

    def close(self):
        super().close()
        if self.data is not None: self.data.close()
        self.data = None
