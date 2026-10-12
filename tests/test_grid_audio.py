"""Grid auditioning: one PCM clock for song + clicks, cached headroom gain."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication
from PySide6.QtMultimedia import QtAudio

from studio_ab_audio import ABMixer, FADE_FRAMES, FRAME_BYTES, RATE
from studio_grid_audio import (
    CLICK_FRAMES, GridMixer, GridTransport, prepare_source,
)


# Sink fixture mirrored from tests/test_ab_audio.py: partial writes that are
# deliberately not whole frames, plus processed-time position tracking.
class Device:
    def id(self): return b"test-output"
    def isNull(self): return False
    def isFormatSupported(self, fmt): return True


class Sink:
    def __init__(self, *args):
        self.buffer = bytearray()
        self.history = bytearray()
        self.processed = 0
        self.limit = 15360
        self.partial = 509  # Deliberately not a complete sample frame.
        self.suspended = False
        self.starts = self.resets = 0

    def setVolume(self, volume): pass
    def setBufferSize(self, size): self.limit = size
    def deleteLater(self): pass
    def error(self): return QtAudio.Error.NoError
    def bytesFree(self): return self.limit - len(self.buffer)
    def processedUSecs(self): return round(self.processed * 1_000_000 / RATE)
    def state(self):
        if self.suspended: return QtAudio.State.SuspendedState
        return QtAudio.State.ActiveState if self.buffer else QtAudio.State.IdleState

    def start(self):
        self.starts += 1
        self.processed = 0
        self.buffer.clear()
        self.suspended = False
        return self

    def reset(self):
        self.resets += 1
        self.buffer.clear()

    def write(self, data):
        n = min(self.partial, self.bytesFree(), len(data))
        self.buffer.extend(data[:n])
        return n

    def suspend(self): self.suspended = True
    def resume(self): self.suspended = False

    def consume(self, frames):
        if self.suspended: return
        size = min(len(self.buffer) // FRAME_BYTES, frames) * FRAME_BYTES
        self.history.extend(self.buffer[:size])
        del self.buffer[:size]
        self.processed += size // FRAME_BYTES


def make_source(path, data, rate=RATE):
    sf.write(str(path), np.asarray(data, dtype=np.float32), rate, subtype="FLOAT")
    return path


class PrepareSourceTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="grid-audio-"))
        self.cache = self.work / "cache"

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_loud_source_gets_headroom_gain_and_progress(self):
        make_source(self.work / "loud.wav", np.full((4800, 2), 0.9, dtype=np.float32))
        messages = []
        report = prepare_source(self.work / "loud.wav", self.cache, progress=messages.append)
        self.assertEqual(messages, ["decode", "headroom", "done"])
        self.assertEqual(report["frames"], 4800)
        self.assertAlmostEqual(report["gain"], 0.65 / 0.9, places=6)
        audio, rate = sf.read(report["path"], dtype="float32", always_2d=True)
        self.assertEqual(rate, RATE)
        self.assertLessEqual(float(np.abs(audio).max()), 0.65 + 1e-6)

    def test_quiet_source_keeps_unity_gain(self):
        data = (0.1 * np.sin(2 * np.pi * 440 * np.arange(4800) / RATE)
                ).astype(np.float32)
        make_source(self.work / "quiet.wav", np.column_stack([data, -data]))
        report = prepare_source(self.work / "quiet.wav", self.cache)
        self.assertEqual(report["gain"], 1.0)
        audio, _ = sf.read(report["path"], dtype="float32", always_2d=True)
        np.testing.assert_allclose(audio[:, 0], data, atol=1e-6)

    def test_cache_reuse_returns_identical_report(self):
        make_source(self.work / "song.wav", np.full((2400, 2), 0.2, dtype=np.float32))
        first = prepare_source(self.work / "song.wav", self.cache)
        stamp = Path(first["path"]).stat().st_mtime_ns
        messages = []
        second = prepare_source(self.work / "song.wav", self.cache, progress=messages.append)
        self.assertEqual(first, second)
        self.assertEqual(messages, ["done"])
        self.assertEqual(Path(second["path"]).stat().st_mtime_ns, stamp)

    def test_cancel_leaves_no_half_published_cache(self):
        make_source(self.work / "song.wav", np.full((2400, 2), 0.2, dtype=np.float32))

        for stage in ('headroom', 'done'):
            def cancel(message):
                if message == stage:
                    raise RuntimeError("cancelled")
            with self.assertRaises(RuntimeError):
                prepare_source(self.work / "song.wav", self.cache, progress=cancel)
            leftovers = [p for p in self.cache.iterdir() if p.name != '.cache-work.lock'] if self.cache.exists() else []
            self.assertEqual(leftovers, [])

    def test_changed_source_is_not_published(self):
        src = make_source(self.work / "song.wav", np.full((2400, 2), 0.2, dtype=np.float32))

        def mutate(message):
            if message == "headroom":
                make_source(src, np.full((4800, 2), 0.4, dtype=np.float32))
        with self.assertRaises(ValueError):
            prepare_source(src, self.cache, progress=mutate)
        self.assertEqual([p for p in self.cache.iterdir() if p.name != '.cache-work.lock'], [])

    def test_converts_rate_and_channels(self):
        tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(44100) / 44100)).astype(np.float32)
        sf.write(str(self.work / "mono.wav"), tone, 44100, subtype="PCM_16")
        report = prepare_source(self.work / "mono.wav", self.cache)
        info = sf.info(report["path"])
        self.assertEqual((info.samplerate, info.channels), (RATE, 2))
        self.assertLess(abs(report["frames"] - RATE), 64)

    def test_missing_source_raises(self):
        with self.assertRaises(ValueError):
            prepare_source(self.work / "absent.wav", self.cache)


class GridMixerTests(unittest.TestCase):
    def test_click_alignment_accent_and_silence(self):
        mixer = GridMixer(np.zeros((RATE, 2), dtype=np.float32))
        mixer.set_grid([0.5], [0.25])
        mixer.set_click(True)
        out = np.frombuffer(mixer.read(RATE), dtype="<f4").reshape(-1, 2)
        for onset in (int(0.25 * RATE), int(0.5 * RATE)):
            self.assertAlmostEqual(float(out[onset - 1, 0]), 0.0, places=6)
            self.assertGreater(float(np.abs(out[onset:onset + CLICK_FRAMES, 0]).max()), 0.1)
        self.assertLessEqual(float(np.abs(out).max()), 0.20 + 1e-7)
        accent = out[int(0.25 * RATE):int(0.25 * RATE) + CLICK_FRAMES, 0]
        normal = out[int(0.5 * RATE):int(0.5 * RATE) + CLICK_FRAMES, 0]
        self.assertFalse(np.allclose(accent, normal, atol=1e-4))
        silent = GridMixer(np.zeros((RATE, 2), dtype=np.float32))
        silent.set_grid([], [])
        silent.set_click(True)
        np.testing.assert_array_equal(
            np.frombuffer(silent.read(RATE), dtype="<f4"), np.zeros(RATE * 2, dtype="<f4"))

    def test_chunk_splits_and_seek_match_single_read(self):
        rng = np.random.default_rng(7)
        audio = (0.5 * rng.standard_normal((RATE, 2))).astype(np.float32)
        mixer = GridMixer(audio)
        mixer.set_grid([0.1, 0.20001, 0.5], [0.0])
        mixer.set_click(True)
        reference = mixer.read(RATE)
        chunked = GridMixer(audio)  # Fresh ramp state, like the reference run.
        chunked.set_grid([0.1, 0.20001, 0.5], [0.0])
        chunked.set_click(True)
        chunks, sizes = bytearray(), [1, 509, CLICK_FRAMES, 7, 4000, 960, 12345]
        for size in sizes:
            chunks.extend(chunked.read(size))
        chunks.extend(chunked.read(RATE))
        # Chunk splits only re-associate the toggle ramp's float rounding.
        np.testing.assert_allclose(
            np.frombuffer(bytes(chunks), dtype="<f4"),
            np.frombuffer(reference, dtype="<f4"), atol=1e-6)
        chunked.seek(int(0.1 * RATE) + 100)  # Mid-click tail.
        self.assertEqual(chunked.read(500),
                         reference[(int(0.1 * RATE) + 100) * FRAME_BYTES:
                                   (int(0.1 * RATE) + 600) * FRAME_BYTES])

    def test_near_equal_beats_deduplicate_to_one_accent(self):
        audio = np.zeros((RATE, 2), dtype=np.float32)
        mixer = GridMixer(audio)
        mixer.set_grid([0.5], [0.505])  # 5 ms apart: a single accent click.
        mixer.set_click(True)
        mixer.seek(0)
        expected = GridMixer(audio)
        expected.set_grid([], [0.5])
        expected.set_click(True)
        self.assertEqual(mixer.read(RATE), expected.read(RATE))
        duo = GridMixer(audio)
        duo.set_grid([0.5, 0.51], [])  # 10 ms apart: still one click.
        duo.set_click(True)
        solo = GridMixer(audio)
        solo.set_grid([0.5], [])
        solo.set_click(True)
        self.assertEqual(duo.read(RATE), solo.read(RATE))

    def test_rejects_invalid_grid(self):
        mixer = GridMixer(np.zeros((RATE, 2), dtype=np.float32))
        with self.assertRaises(ValueError):
            mixer.set_grid([0.5, 0.4], [])
        with self.assertRaises(ValueError):
            mixer.set_grid([-0.1], [])
        with self.assertRaises(ValueError):
            mixer.set_grid([10.0], [])
        with self.assertRaises(ValueError):
            mixer.set_grid([float("nan")], [])
        with self.assertRaises(ValueError):
            mixer.set_grid([], [0.3, 0.2])
        with self.assertRaises(ValueError):
            mixer.set_grid([], [float("inf")])

    def test_click_toggle_ramps_and_grid_changes_keep_frame(self):
        mixer = GridMixer(np.zeros((RATE, 2), dtype=np.float32))
        mixer.set_grid([], [0.0])
        mixer.set_click(True)
        self.assertEqual(mixer.frame, 0)
        mixer.set_grid([0.5], [0.0])
        mixer.set_click(False)
        mixer.set_click(True)
        self.assertEqual(mixer.frame, 0)
        out = np.frombuffer(mixer.read(FADE_FRAMES), dtype="<f4").reshape(-1, 2)
        prototype = GridMixer(np.zeros((RATE, 2), dtype=np.float32))
        prototype.set_grid([], [0.0])
        prototype.set_click(True)
        prototype.seek(0)  # Snap to full click to expose the raw tail.
        raw = np.frombuffer(prototype.read(FADE_FRAMES), dtype="<f4").reshape(-1, 2)
        np.testing.assert_allclose(
            out[:, 0], raw[:, 0] * ABMixer.ramp(0.0, 1.0, FADE_FRAMES), atol=1e-7)
        mixer.seek(0)  # Seeking snaps the toggle ramp to its target.
        snapped = np.frombuffer(mixer.read(10), dtype="<f4").reshape(-1, 2)
        np.testing.assert_allclose(snapped, raw[:10], atol=1e-7)

    def test_accepts_memmap_without_copying(self):
        with tempfile.TemporaryDirectory(prefix="grid-memmap-") as tmp:
            backing = Path(tmp) / "audio.raw"
            writable = np.memmap(backing, dtype="<f4", mode="w+", shape=(1024, 2))
            writable[:] = 0.1
            del writable
            mapped = np.memmap(backing, dtype="<f4", mode="r", shape=(1024, 2))
            mixer = GridMixer(mapped)
            self.assertIs(mixer.audio, mapped)
            mixer.set_grid([0.001], [])
            mixer.set_click(True)
            mixer.seek(0)
            out = np.frombuffer(mixer.read(1024), dtype="<f4").reshape(-1, 2)
            self.assertAlmostEqual(float(out[0, 0]), 0.1, places=6)
            del mixer, mapped

    def test_source_plus_click_cannot_clip(self):
        rng = np.random.default_rng(3)
        audio = np.full((RATE, 2), 0.65, dtype=np.float32)
        audio[::7] = (0.65 * rng.standard_normal((len(audio[::7]), 2))).astype(np.float32)
        audio = np.clip(audio, -0.65, 0.65).astype(np.float32)
        mixer = GridMixer(audio)
        mixer.set_grid([n * 0.025 for n in range(40)], [0.0])
        mixer.set_click(True)
        out = np.frombuffer(mixer.read(RATE), dtype="<f4")
        self.assertLess(float(np.abs(out).max()), 1.0)
        self.assertLessEqual(float(np.abs(out).max()), 0.85 + 1e-6)


class GridTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="grid-transport-"))
        tone = (0.5 * np.sin(2 * np.pi * 440 * np.arange(RATE // 2) / RATE)).astype(np.float32)
        make_source(self.work / "song.wav", np.column_stack([tone, -tone]))
        self.report = prepare_source(self.work / "song.wav", self.work / "cache")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_grid_and_click_changes_do_not_restart_sink(self):
        player = GridTransport(sink_factory=Sink)
        player.load(self.report)
        player.set_grid([0.1, 0.2, 0.3], [0.0])
        player.set_click(True)
        player.set_device(Device())
        player.play()
        sink = player.sink
        starts, resets = player.start_count, player.reset_count
        stream = player.stream
        last_position = 0
        for index in range(800):  # 24000 frames drain at 48 frames per pump.
            if index % 5 == 0:
                player.set_grid(sorted(0.05 * (1 + (index + k) % 8) for k in range(3)), [0.0])
                player.set_click(index % 2 == 0)
                self.assertEqual(player.start_count, starts)
                self.assertEqual(player.reset_count, resets)
                self.assertIs(player.stream, stream)
            if index == 20:
                player.pause()
                frozen = player.position()
                sink.consume(500)
                self.assertEqual(player.position(), frozen)
                player.play()
                self.assertEqual(player.start_count, starts)
            sink.consume(48)
            player._pump()
            position = player.position()
            self.assertGreaterEqual(position, last_position)
            last_position = position
            if not player.playing:
                break
        self.assertFalse(player.playing, "Playback must finish after draining the samples.")
        self.assertEqual(len(sink.history) // FRAME_BYTES, player.mixer.frames)
        self.assertEqual(sink.starts, 1)
        player.seek(100)
        self.assertEqual(player.mixer.frame, 4800)
        player.play()
        identity = player.stream
        player.set_grid([0.15], [])
        self.assertIs(player.stream, identity)
        player.close()

    def test_set_grid_retains_position_and_sink(self):
        player = GridTransport(sink_factory=Sink)
        player.load(self.report)
        player.set_grid([0.1], [0.0])
        player.set_device(Device())
        player.play()
        for _ in range(10):
            player.sink.consume(48)
            player._pump()
        frame, position, stream, sink = (player.mixer.frame, player.position_frame(),
                                        player.stream, player.sink)
        player.set_grid([0.2, 0.3], [0.1])
        self.assertEqual(player.mixer.frame, frame)
        self.assertEqual(player.position_frame(), position)
        self.assertIs(player.stream, stream)
        self.assertIs(player.sink, sink)
        player.close()

    def test_load_replaces_source_and_close_allows_deletion(self):
        make_source(self.work / "other.wav", np.full((2400, 2), 0.1, dtype=np.float32))
        other = prepare_source(self.work / "other.wav", self.work / "cache")
        player = GridTransport(sink_factory=Sink)
        player.load(self.report)
        player.set_device(Device())
        player.play()
        player.load(other)  # Must release the old source handle.
        self.assertEqual(player.mixer.frames, 2400)
        self.assertEqual(player.origin, 0)
        os.remove(self.report["path"])
        player.close()
        self.assertIsNone(player.mixer)
        self.assertIsNone(player._audio)
        os.remove(other["path"])  # No open handles may survive close, even on Windows.

    def test_full_playback_renders_source_plus_clicks(self):
        player = GridTransport(sink_factory=Sink)
        player.load(self.report)
        player.set_device(Device())  # Device setup seeks, so arm the grid after it.
        player.set_grid([], [0.0])
        player.set_click(True)
        player.play()
        sink = player.sink
        while player.playing:
            sink.consume(240)
            player._pump()
        actual = np.frombuffer(sink.history, dtype="<f4").reshape(-1, 2)
        expected = GridMixer(player.mixer.audio)
        expected.set_grid([], [0.0])
        expected.set_click(True)
        np.testing.assert_allclose(
            actual, np.frombuffer(expected.read(expected.frames), dtype="<f4").reshape(-1, 2),
            atol=1e-6)
        player.close()


if __name__ == "__main__":
    unittest.main()
