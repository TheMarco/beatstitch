"""Mastering transport invariants: one cursor, no device restart on switch."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication
from PySide6.QtMultimedia import QtAudio

from prototype_mastering_audio import (
    FADE_FRAMES, FRAME_BYTES, RATE, MasteringData, MasteringMixer,
    MasteringTransport, LevelInfo, MasteringReport, effective_variant,
    load_mastering_folder, resolve_b_variant,
)
from studio_ab_audio import ABTransport  # noqa: F401  (inherited transport)


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
        self.partial = 509
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


def make_data(n=6000, a=None):
    if a is None:
        a = np.column_stack([np.arange(n), -np.arange(n)]).astype(np.float32) / 12000
    report = MasteringReport("T", n / RATE, [], LevelInfo(-14.0, -1.0),
                             LevelInfo(-9.0, -0.3), LevelInfo(-14.0, -5.3), -5.0, ["test"])
    report.frames = n
    return MasteringData(report, a, a.copy(), a.copy())


def write_folder(folder: Path, n=4800, omit=None, report_override=None):
    folder.mkdir(parents=True, exist_ok=True)
    report = {"name": "Demo", "duration": n / RATE,
              "segments": [{"name": "Chorus", "start": 0.0, "end": n / RATE}],
              "current": {"lufs": -14.0, "true_peak_db": -1.0},
              "mastered": {"lufs": -9.0, "true_peak_db": -0.3},
              "matched": {"lufs": -14.0, "true_peak_db": -5.3},
              "matched_gain_db": -5.0, "processing_summary": ["Gentle limiting"]}
    if report_override:
        report.update(report_override)
    (folder / "report.json").write_text(json.dumps(report))
    for name, value in (("current", 0.2), ("mastered", 0.8), ("matched", 0.4)):
        if name == omit:
            continue
        sf.write(folder / f"{name}.wav", np.full((n, 2), value, dtype=np.float32), RATE)


class MasteringAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_b_variant_mapping(self):
        self.assertEqual(resolve_b_variant(True), "matched")
        self.assertEqual(resolve_b_variant(False), "mastered")
        self.assertEqual(effective_variant("A", True), "current")
        self.assertEqual(effective_variant("B", True), "matched")
        self.assertEqual(effective_variant("B", False), "mastered")

    def test_crossfade_is_short_continuous_and_reversible(self):
        shape = (1000, 2)
        mixer = MasteringMixer(np.full(shape, .2, dtype=np.float32),
                               np.full(shape, .8, dtype=np.float32),
                               np.full(shape, .5, dtype=np.float32))
        mixer.select("matched")
        first = np.frombuffer(mixer.read(80), dtype="<f4").reshape(-1, 2)
        mixer.select("current")
        second = np.frombuffer(mixer.read(300), dtype="<f4").reshape(-1, 2)
        output = np.r_[first[:, 0], second[:, 0]]
        self.assertLessEqual(float(np.max(abs(np.diff(output)))), .6 / FADE_FRAMES + 1e-6)
        self.assertAlmostEqual(float(output[-1]), .2, places=6)
        self.assertEqual(mixer.frame, 380)

    def test_loop_wraps_on_same_cursor(self):
        a = (np.arange(100, dtype=np.float32) / 100)[:, None].repeat(2, axis=1)
        mixer = MasteringMixer(a, a, a, loop=True)
        out = np.frombuffer(mixer.read(250), dtype="<f4").reshape(-1, 2)[:, 0]
        np.testing.assert_allclose(out, np.tile(a[:, 0], 3)[:250], atol=1e-7)
        self.assertEqual(mixer.frame, 250)

    def test_loop_can_be_disabled_after_multiple_laps(self):
        a = (np.arange(100, dtype=np.float32) / 100)[:, None].repeat(2, axis=1)
        mixer = MasteringMixer(a, a, a, loop=True)
        mixer.read(250)
        mixer.set_loop(False)
        out = np.frombuffer(mixer.read(200), dtype='<f4').reshape(-1, 2)
        np.testing.assert_allclose(out, a[50:], atol=1e-7)
        self.assertEqual(mixer.frame, 300)
        self.assertEqual(mixer.read(1), b'')

    def test_three_way_retarget_preserves_unity_gain(self):
        a = np.full((1000, 2), .3, dtype=np.float32)
        mixer = MasteringMixer(a, a, a)
        mixer.select('matched')
        mixer.read(80)
        mixer.select('mastered')
        out = np.frombuffer(mixer.read(300), dtype='<f4')
        np.testing.assert_allclose(out, .3, atol=1e-7)

    def test_switches_and_match_toggle_do_not_restart_sink_or_skip(self):
        data = make_data()
        player = MasteringTransport(sink_factory=Sink)
        player.load_data(data)
        player.set_device(Device())
        player.play()
        sink = player.sink
        starts, resets = player.start_count, player.reset_count
        stream = player.stream
        for i in range(300):
            if i % 3 == 0:
                player.set_selection("B" if i % 2 else "A")
                player.set_match_loudness(i % 2 == 0)
                self.assertEqual((player.start_count, player.reset_count), (starts, resets))
                self.assertIs(player.stream, stream)
            sink.consume(48)
            player._pump()
            if not player.playing:
                break
        self.assertFalse(player.playing)
        actual = np.frombuffer(sink.history, dtype="<f4").reshape(-1, 2)
        np.testing.assert_allclose(actual, data.current, atol=1e-7)
        self.assertEqual(sink.starts, 1)
        player.close()

    def test_match_toggle_changes_effective_variant_live(self):
        player = MasteringTransport(sink_factory=Sink)
        player.load_data(make_data(1000, np.full((1000, 2), .2, dtype=np.float32)))
        player.set_selection("B")
        self.assertEqual(player.effective, "matched")
        self.assertEqual(player.mixer.variant, "matched")
        player.set_match_loudness(False)
        self.assertEqual(player.effective, "mastered")
        player.set_selection("A")
        self.assertEqual(player.effective, "current")

    def test_load_folder_and_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "ok"
            write_folder(folder)
            data = load_mastering_folder(folder)
            self.assertEqual(data.report.name, "Demo")
            self.assertEqual(len(data.current), 4800)
            self.assertEqual(data.report.segments[0].name, "Chorus")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "missing"
            write_folder(folder, omit="matched")
            with self.assertRaisesRegex(ValueError, "matched\\.wav"):
                load_mastering_folder(folder)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "bad"
            write_folder(folder, report_override={"duration": 99.0})
            with self.assertRaises(ValueError):
                load_mastering_folder(folder)
        with self.assertRaisesRegex(ValueError, "report\\.json|not found"):
            load_mastering_folder(Path(tempfile.gettempdir()) / "no-such-mastering-folder-xyz")


if __name__ == "__main__":
    unittest.main()
