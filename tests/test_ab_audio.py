"""Transport invariants: no skipped/repeated frames or device restarts on A/B."""
import unittest

import numpy as np
from PySide6.QtCore import QCoreApplication
from PySide6.QtMultimedia import QtAudio

from prototype_ab_audio import ABMixer, ABTransport, FADE_FRAMES, FRAME_BYTES, RATE


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


class ABAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_crossfade_is_short_continuous_and_reversible(self):
        a = np.full((1000, 2), .2, dtype=np.float32)
        b = np.full_like(a, .8)
        mixer = ABMixer(a, b, np.zeros_like(a))
        mixer.select("straightened")
        first = np.frombuffer(mixer.read(80), dtype="<f4").reshape(-1, 2)
        mixer.select("fixed")  # Retarget before the first fade has finished.
        second = np.frombuffer(mixer.read(300), dtype="<f4").reshape(-1, 2)
        output = np.r_[first[:, 0], second[:, 0]]
        self.assertLessEqual(float(np.max(abs(np.diff(output)))), .6 / FADE_FRAMES + 1e-6)
        self.assertGreaterEqual(float(output.min()), .199999)
        self.assertAlmostEqual(float(output[-1]), .2, places=6)
        self.assertEqual(mixer.frame, 380)

    def test_click_uses_same_clock_and_survives_ab_switch(self):
        a = np.full((1000, 2), .2, dtype=np.float32)
        click = np.full_like(a, .05)
        mixer = ABMixer(a, a, click)
        mixer.set_click(True)
        mixer.read(FADE_FRAMES)
        mixer.select("straightened")
        audio = np.frombuffer(mixer.read(500), dtype="<f4")
        np.testing.assert_allclose(audio, .25, atol=1e-7)
        mixer.seek(10)
        self.assertEqual(mixer.frame, 10)
        np.testing.assert_allclose(np.frombuffer(mixer.read(20), dtype="<f4"), .25, atol=1e-7)

    def test_repeated_switches_partial_writes_and_pause_do_not_skip_frames(self):
        a = np.column_stack([np.arange(6000), -np.arange(6000)]).astype(np.float32) / 12000
        player = ABTransport(sink_factory=Sink)
        player.mixer = ABMixer(a, a, np.zeros_like(a))
        player.set_device(Device())
        player.play()
        sink = player.sink
        starts, resets = player.start_count, player.reset_count
        last_position = 0
        for index in range(500):
            if index % 3 == 0:
                player.select("fixed" if index % 2 else "straightened")
                player.set_click(index % 2 == 0)
                self.assertEqual(player.start_count, starts)
                self.assertEqual(player.reset_count, resets)
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
        self.assertFalse(player.playing, "Playback must finish after draining the final samples.")
        actual = np.frombuffer(sink.history, dtype="<f4").reshape(-1, 2)
        np.testing.assert_allclose(actual, a, atol=1e-7)
        self.assertEqual(sink.starts, 1)
        # An intentional seek is allowed to reset; subsequent A/B must not.
        player.seek(30)
        self.assertEqual(player.mixer.frame, 1440)
        player.play()
        identity = player.stream
        player.select("fixed")
        self.assertIs(player.stream, identity)
        player.close()


if __name__ == "__main__":
    unittest.main()
