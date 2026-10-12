"""Relative levels, safe normalization and uninterrupted stem monitoring."""
import unittest

import numpy as np
from PySide6.QtWidgets import QApplication

from prepare_separation_trial import listening_versions, normalization, STEMS
from studio_ab_audio import FADE_FRAMES
from studio_separation_audio import SeparationMixer, SeparationTransport, VARIANTS
from tests.test_ab_audio import Device, Sink


class SeparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_shared_gain_preserves_stem_sum_and_relative_levels(self):
        stems = {name: np.full((800, 2), (i + 1) * .12, dtype=np.float32) for i, name in enumerate(STEMS)}
        original = sum(stems.values())
        versions, gain = listening_versions(original, stems)
        self.assertLess(gain, 1.)
        np.testing.assert_allclose(versions['original'], versions['recombined'], atol=2e-7)
        np.testing.assert_allclose(versions['instrumental'] + versions['vocals'], versions['recombined'], atol=2e-7)
        np.testing.assert_allclose(versions['vocals'], versions['drums'] * 4, atol=2e-7)
        self.assertLessEqual(max(float(np.abs(a).max()) for a in versions.values()), 10 ** (-2 / 20) + 1e-7)

    def test_normalization_handles_silence_and_cancelled_mono(self):
        mono = np.sin(np.arange(800, dtype=np.float32))
        for audio in (np.zeros((800, 2), dtype=np.float32), np.column_stack((mono, -mono))):
            mean, scale = normalization(audio)
            normalized = (audio - mean) / scale
            self.assertTrue(np.isfinite(normalized).all())
            self.assertLessEqual(float(np.abs(normalized).max()), 15.)

    def test_retargeting_during_fade_preserves_unity_gain_and_clock(self):
        audio = np.full((10000, 2), .25, dtype=np.float32)
        mixer = SeparationMixer(*(audio.copy() for _ in VARIANTS))
        total = 0
        for name in list(VARIANTS) * 5:
            mixer.select(name)
            for size in (1, 7, 43):
                rendered = np.frombuffer(mixer.read(size), dtype='<f4')
                np.testing.assert_allclose(rendered, .25, atol=1e-7)
                total += size
        self.assertEqual(mixer.frame, total)

    def test_switch_uses_selected_samples_after_exact_fade(self):
        audio = [np.full((1000, 2), i / 10, dtype=np.float32) for i in range(5)]
        mixer = SeparationMixer(*audio)
        mixer.select('vocals')
        parts = [np.frombuffer(mixer.read(n), dtype='<f4').reshape(-1, 2) for n in (3, 61, 100, 76, 10)]
        rendered = np.concatenate(parts)
        self.assertLess(float(rendered[FADE_FRAMES - 2, 0]), .3)
        np.testing.assert_allclose(rendered[FADE_FRAMES - 1:], .3, atol=1e-7)
        self.assertLessEqual(float(np.max(np.abs(np.diff(rendered[:, 0])))), .3 / FADE_FRAMES + 1e-7)

    def test_selecting_parts_does_not_restart_or_reset_device(self):
        audio = np.column_stack((np.arange(12000), -np.arange(12000))).astype(np.float32) / 24000
        player = SeparationTransport(sink_factory=Sink)
        self.addCleanup(player.close)
        player.mixer = SeparationMixer(*(audio.copy() for _ in VARIANTS))
        player.set_device(Device()); player.play()
        counts, stream = (player.start_count, player.reset_count), player.stream
        for i in range(100):
            player.select(VARIANTS[i % 5]); player.sink.consume(48); player._pump()
            self.assertEqual((player.start_count, player.reset_count), counts)
            self.assertIs(player.stream, stream)
            self.assertTrue(player.playing)
        actual = np.frombuffer(player.sink.history, dtype='<f4').reshape(-1, 2)
        np.testing.assert_allclose(actual, audio[:len(actual)], atol=1e-7)

    def test_invalid_stem_shapes_and_nonfinite_audio_are_rejected(self):
        audio = np.zeros((100, 2), dtype=np.float32)
        stems = {name: audio.copy() for name in STEMS}
        stems['drums'][0, 0] = np.nan
        with self.assertRaises(ValueError): listening_versions(audio, stems)
        with self.assertRaises(ValueError): SeparationMixer(audio, audio, audio[:-1], audio, audio)


if __name__ == '__main__': unittest.main()
