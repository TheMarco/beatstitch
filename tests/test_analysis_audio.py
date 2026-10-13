"""Cancellation-safe analysis must preserve normal signals and sample clocks."""
from pathlib import Path
import tempfile
import threading
import unittest

import numpy as np
import soundfile as sf

import djmix
from studio_analysis_audio import mono_stream, mono_samples
from studio_process import cancel_on


class AnalysisAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root / 'source.wav'
        self.rate = 32000
        t = np.arange(round(11.137 * self.rate)) / self.rate
        self.audio = (.2 * np.sin(2 * np.pi * 85 * t) + .1 * np.sin(2 * np.pi * 440 * t)).astype('float32')

    def expected_mono(self, path):
        return np.frombuffer(djmix.ffmpeg('-i', str(path), '-map', '0:a:0', '-ar', '22050',
                                         '-ac', '1', '-f', 'f32le', '-').stdout, dtype='<f4')

    def read(self, details=None, progress=lambda _: None):
        return np.frombuffer(b''.join(mono_stream(self.path, 22050, cache_dir=self.root,
                             chunk_samples=1703, details=details, progress=progress)), dtype='<f4')

    def test_normal_mix_and_mono_are_bit_identical_to_existing_decode(self):
        for samples in (self.audio, np.column_stack((self.audio, self.audio * .7)),
                        np.column_stack((self.audio, np.zeros_like(self.audio)))):
            sf.write(self.path, samples, self.rate, subtype='FLOAT')
            details = {}; actual = self.read(details)
            np.testing.assert_array_equal(actual, self.expected_mono(self.path))
            self.assertEqual(details['analysis_channel'], 'mono_mix')
            self.assertFalse(list(self.root.glob('.pending-*')))

    def test_cancelled_channels_keep_one_fixed_audible_channel(self):
        for right_gain in (-1., -1.1):
            sf.write(self.path, np.column_stack((self.audio, self.audio * right_gain)), self.rate, subtype='FLOAT')
            before = self.path.read_bytes(); details = {}; actual = self.read(details)
            stereo = np.frombuffer(djmix.ffmpeg('-i', str(self.path), '-map', '0:a:0', '-ar', '22050',
                                              '-ac', '2', '-f', 'f32le', '-').stdout, dtype='<f4').reshape(-1, 2)
            chosen = 0 if right_gain == -1. else 1
            np.testing.assert_array_equal(actual, stereo[:, chosen])
            self.assertEqual(details['analysis_channel'], 'stronger_left' if chosen == 0 else 'stronger_right')
            self.assertGreater(float(np.max(abs(actual))), .1)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertFalse(list(self.root.glob('.pending-*')))

    def test_selected_excerpt_uses_its_own_channel_choice(self):
        sf.write(self.path, np.column_stack((self.audio, -self.audio)), self.rate, subtype='FLOAT')
        details = {}; actual = mono_samples(self.path, 22050, start=2., duration=3., details=details)
        self.assertEqual(len(actual), 3 * 22050)
        self.assertEqual(details['analysis_channel'], 'stronger_left')
        self.assertGreater(float(np.max(abs(actual))), .1)

    def test_cancel_in_stereo_pass_cleans_up_and_preserves_source(self):
        sf.write(self.path, np.column_stack((self.audio, -self.audio)), self.rate, subtype='FLOAT')
        before = self.path.read_bytes(); event = threading.Event()
        def progress(message): event.set()
        with cancel_on(event), self.assertRaisesRegex(djmix.MixError, 'cancelled'):
            self.read(progress=progress)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.root.glob('.pending-*')))


if __name__ == '__main__': unittest.main()
