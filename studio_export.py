"""Purpose-based offline export helpers, separate from Qt."""

from __future__ import annotations

import csv
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Callable

import djmix


PRESETS: dict[str, dict[str, str]] = {
    "wav": {
        "label": "High-quality master",
        "extension": ".wav",
        "description": "Stereo 48 kHz 24-bit",
    },
    "flac": {
        "label": "Lossless, smaller file",
        "extension": ".flac",
        "description": "48 kHz 24-bit",
    },
    "mp3": {
        "label": "Easy sharing",
        "extension": ".mp3",
        "description": "320 kbps stereo",
    },
}

_WAV_BYTES_PER_SECOND = 48000 * 2 * 3
_MP3_BYTES_PER_SECOND = 320_000 // 8
_FLAC_FRACTION = 0.6
_MAX_ATTEMPTS = 4
_MAX_ATTENUATION_DB = -6.0


def _noop(message: str) -> None:
    return None


def estimate_bytes(duration_seconds: float, preset_id: str) -> int:
    """Estimate the exported file size in bytes.

    WAV assumes PCM 24-bit stereo at 48 kHz (288000 bytes/s, header excluded).
    MP3 assumes 320 kbps constant bit rate (40000 bytes/s).
    The FLAC estimate is approximate: lossless compression depends on the
    audio content, so the real file may be notably smaller or larger.
    """
    if preset_id not in PRESETS:
        raise djmix.MixError(f"Unknown export preset {preset_id!r}. Choose one of: {', '.join(sorted(PRESETS))}.")
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise djmix.MixError("Duration must be a finite number of seconds.") from exc
    if not math.isfinite(duration) or duration < 0:
        raise djmix.MixError("Duration must be a finite, nonnegative number of seconds.")
    if preset_id == "wav":
        return int(math.ceil(duration * _WAV_BYTES_PER_SECOND))
    if preset_id == "mp3":
        return int(math.ceil(duration * _MP3_BYTES_PER_SECOND))
    return int(math.ceil(duration * _WAV_BYTES_PER_SECOND * _FLAC_FRACTION))


def same_target(a: Path, b: Path) -> bool:
    try:
        if a.resolve() == b.resolve():
            return True
    except OSError:
        pass
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False


def _is_canonical_wav(path: Path) -> bool:
    try:
        data = json.loads(djmix.run([
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels,bits_per_sample",
            "-of", "json", str(path),
        ]).stdout)
    except djmix.MixError:
        return False
    streams = data.get("streams", [])
    if not streams:
        return False
    stream = streams[0]
    try:
        rate = int(stream.get("sample_rate", 0))
        channels = int(stream.get("channels", 0))
    except (TypeError, ValueError):
        return False
    codec = str(stream.get("codec_name", "")).lower()
    if codec != "pcm_s24le" or rate != 48000 or channels != 2:
        return False
    bits = stream.get("bits_per_sample")
    if bits is not None:
        try:
            if int(bits) != 24:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _check_duration(master_duration: float, staged_duration: float, preset_id: str) -> None:
    if preset_id == "mp3":
        if abs(staged_duration - master_duration) > 0.11:
            raise djmix.MixError(
                f"Exported duration {staged_duration:.3f}s does not match the master "
                f"{master_duration:.3f}s (lossy padding allowance 0.1s)."
            )
        return
    if abs(staged_duration - master_duration) > 2.5 / djmix.SR:
        raise djmix.MixError(
            f"Exported duration {staged_duration:.6f}s does not match the master "
            f"{master_duration:.6f}s at sample level."
        )


def _encode_once(master: Path, staged: Path, preset_id: str, title: str, attenuation_db: float) -> None:
    metadata_args: list[str] = []
    if title:
        metadata_args = ["-metadata", f"title={title}"]
    attenuate = attenuation_db != 0
    volume = f"volume={attenuation_db:.12f}dB"
    if preset_id == "wav":
        if not attenuate and _is_canonical_wav(master):
            djmix.ffmpeg("-i", str(master), "-map", "0:a:0", "-c:a", "copy",
                         *metadata_args, str(staged))
            return
        args = ["-i", str(master), "-map", "0:a:0"]
        if attenuate:
            args += ["-af", volume]
        args += ["-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le",
                 *metadata_args, str(staged)]
        djmix.ffmpeg(*args)
        return
    if preset_id == "flac":
        args = ["-i", str(master), "-map", "0:a:0"]
        if attenuate:
            args += ["-af", volume]
        args += ["-ar", "48000", "-ac", "2", "-sample_fmt", "s32",
                 "-c:a", "flac", "-bits_per_raw_sample", "24",
                 *metadata_args, str(staged)]
        djmix.ffmpeg(*args)
        return
    args = ["-i", str(master), "-map", "0:a:0"]
    if attenuate:
        args += ["-af", volume]
    args += ["-ar", "48000", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "320k",
             *metadata_args, str(staged)]
    djmix.ffmpeg(*args)


def export_audio(
    master: Path,
    destination: Path,
    preset_id: str,
    *,
    title: str = "",
    protected_sources: list[Path] = (),
    progress: Callable[[str], None] = _noop,
) -> dict:
    """Transcode an already-rendered production master to a purpose preset.

    Never overwrites ``master`` or any path in ``protected_sources`` (compared
    by resolved path, plus hardlink/samefile comparison when both sides exist).
    Stages under ``destination.parent`` and atomically replaces ``destination``
    only after the staged file passes playable-audio, duration, and
    <= -0.99 dBTP checks. ``progress`` is called with status messages; if it
    returns False the export is cancelled. On any failure or cancellation the
    master and any pre-existing destination are left untouched. No automatic
    sidecar files are written.
    """
    if preset_id not in PRESETS:
        raise djmix.MixError(f"Unknown export preset {preset_id!r}. Choose one of: {', '.join(sorted(PRESETS))}.")
    if not isinstance(title, str):
        raise djmix.MixError("Title metadata must be a string.")
    if not callable(progress):
        raise djmix.MixError("Progress must be a callable.")
    master = Path(master)
    destination = Path(destination)
    expected_ext = PRESETS[preset_id]["extension"]
    if destination.suffix.lower() != expected_ext:
        raise djmix.MixError(f"Preset {preset_id!r} requires a {expected_ext} destination, got {destination.suffix!r}.")
    if destination.exists() and destination.is_dir():
        raise djmix.MixError(f"Destination is a directory: {destination}")
    if not master.is_file():
        raise djmix.MixError(f"Master file not found: {master}")

    def notify(message: str) -> None:
        outcome = progress(message)
        if outcome is False:
            raise djmix.MixError("Export cancelled.")

    protected = list(protected_sources) if protected_sources else []
    for candidate in [master, *protected]:
        other = Path(candidate)
        if same_target(destination, other):
            raise djmix.MixError(f"Refusing to overwrite protected source audio: {other}")

    missing = [name for name in ("ffmpeg", "ffprobe") if not djmix.tool_path(name)]
    if missing:
        raise djmix.MixError("Missing tools: " + ", ".join(missing) + ". See README.md for setup.")

    notify("Checking master audio")
    master_info = djmix.probe(master)
    master_duration = master_info["duration"]

    destination.parent.mkdir(parents=True, exist_ok=True)
    from studio_storage import require_space
    require_space(destination.parent, master_duration * djmix.SR * 16, 'export this mix')
    with tempfile.TemporaryDirectory(prefix="dj-export-", dir=destination.parent) as directory:
        work = Path(directory)
        staged = work / f"staged{expected_ext}"
        attenuation_db = 0.0
        final_meter: dict | None = None
        final_duration = 0.0
        for _attempt in range(_MAX_ATTEMPTS):
            notify(f"Exporting {PRESETS[preset_id]['label']}")
            if staged.exists():
                staged.unlink()
            _encode_once(master, staged, preset_id, title, attenuation_db)
            notify("Verifying export")
            staged_info = djmix.probe(staged)
            staged_meter = djmix.meter(staged)
            _check_duration(master_duration, staged_info["duration"], preset_id)
            peak = staged_meter["true_peak_db"]
            if peak <= -1.0:
                final_meter = staged_meter
                final_duration = staged_info["duration"]
                break
            needed = -1.1 - peak
            attenuation_db += needed
            if attenuation_db < _MAX_ATTENUATION_DB:
                raise djmix.MixError(
                    f"Export needs {attenuation_db:.1f} dB attenuation to meet -1 dBTP; refusing further quality change."
                )
            if _attempt == _MAX_ATTEMPTS - 1:
                raise djmix.MixError(
                    f"Export failed its true-peak check ({peak:.2f} dBTP after bounded attenuation)."
                )
        assert final_meter is not None
        if final_meter["true_peak_db"] > -0.99:
            raise djmix.MixError("Export failed its duration or headroom check.")
        notify("Publishing export")
        os.replace(staged, destination)
        return {
            "path": str(destination),
            "duration": final_duration,
            "lufs": final_meter["lufs"],
            "true_peak_db": final_meter["true_peak_db"],
        }


def tracklist_rows(session) -> list[dict]:
    """Return per-clip tracklist rows without file access or mutation."""
    import studio_engine

    positions = studio_engine.layout(session)
    from studio_tempo_curve import session_curve, output_positions
    positions = output_positions(session_curve(session), positions)
    rows: list[dict] = []
    for index, (clip, position) in enumerate(zip(session.clips, positions)):
        track = session.tracks[clip.track_id]
        rows.append({
            "number": index + 1,
            "title": track.name,
            "start_seconds": position["start"] / djmix.SR,
            "end_seconds": position["end"] / djmix.SR,
        })
    return rows


def _format_timestamp(seconds: float) -> str:
    total_ms = int(round(float(seconds) * 1000))
    if total_ms < 0:
        total_ms = 0
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours}:{minutes:02d}:{secs:02d}.{millis:03d}"


def write_tracklist(session, destination: Path) -> Path:
    """Write the session tracklist as UTF-8 CSV and return the destination."""
    import studio_engine  # noqa: F401  (layout validation happens via tracklist_rows)

    destination = Path(destination)
    for track in session.tracks.values():
        if same_target(destination, Path(track.path)):
            raise djmix.MixError("Choose a tracklist filename different from your source songs.")
    rows = tracklist_rows(session)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dj-tracklist-", dir=destination.parent) as directory:
        staged = Path(directory) / "tracklist.csv"
        with staged.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["number", "title", "start", "end"])
            for row in rows:
                writer.writerow([
                    row["number"],
                    row["title"],
                    _format_timestamp(row["start_seconds"]),
                    _format_timestamp(row["end_seconds"]),
                ])
        os.replace(staged, destination)
    return destination
