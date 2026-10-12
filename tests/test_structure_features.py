"""Streaming must retain global frame centers, source edges and model inputs."""
from collections import namedtuple
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
import torch
from beat_this.inference import LogMelSpect

import djmix
import studio_structure_features as feature
from studio_process import cancel_on


class StructureFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.threads = torch.get_num_threads(); torch.set_num_threads(2)
        cls.frontend = LogMelSpect(device='cpu')

    @classmethod
    def tearDownClass(cls): torch.set_num_threads(cls.threads)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'source.wav'
        t = np.arange(round(19.0713 * 32000)) / 32000
        audio = .2 * np.sin(2 * np.pi * 65 * t) + .1 * np.sin(2 * np.pi * 950 * t)
        audio *= (.1 + (t % .5 < .08)); audio[t > 17] = 0
        audio[0], audio[-1] = .4, -.4
        sf.write(self.source, np.column_stack((audio, audio * .8)), 32000, subtype='FLOAT')

    def test_frontend_and_rms_preserve_block_boundaries_and_both_source_edges(self):
        for partial in (True, False):
            with self.subTest(partial_frame=partial):
                if not partial:
                    sf.write(self.source, .2 * np.sin(2 * np.pi * 75 * np.arange(441 * 800) / feature.RATE), feature.RATE)
                raw = djmix.ffmpeg('-i', str(self.source), '-ar', str(feature.RATE), '-ac', '1', '-f', 'f32le', '-').stdout
                audio = np.frombuffer(raw, dtype='<f4')
                expected = self.frontend(torch.tensor(audio)).numpy()
                centers = np.arange(len(expected)) * 441
                a, b = np.clip(centers - 220, 0, len(audio)), np.clip(centers + 221, 0, len(audio))
                cumulative = np.r_[0., np.cumsum(audio.astype(float) ** 2)]
                expected_rms = np.sqrt((cumulative[b] - cumulative[a]) / np.maximum(1, b - a))
                sizes = []
                def transform(values):
                    sizes.append(len(values)); return self.frontend(torch.tensor(values))
                with feature.decoded_audio(self.source, len(audio) / feature.RATE, self.root, lambda _: None) as (path, count):
                    with patch.object(feature, 'BLOCK_FRAMES', 137):
                        mel, rms = feature.extract(path, count, transform, lambda _: None)
                np.testing.assert_allclose(mel, expected, atol=2e-6, rtol=1e-6)
                np.testing.assert_allclose(rms, expected_rms, atol=2e-8, rtol=1e-7)
                self.assertLessEqual(max(sizes), 141 * feature.HOP)
                self.assertFalse(list(self.root.glob('.pending-*')))

    def test_decode_and_frontend_cancellation_release_all_temporary_files(self):
        original = self.source.read_bytes()
        for stage in ('Reading musical cues', 'Measuring the sound'):
            event = threading.Event(); reached = []
            def progress(message):
                if message.startswith(stage): reached.append(True); event.set()
            with cancel_on(event), self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                with feature.decoded_audio(self.source, 19.0713, self.root, progress) as (path, count):
                    feature.extract(path, count, lambda a: self.frontend(torch.tensor(a)), progress)
            self.assertTrue(reached)
            self.assertFalse(list(self.root.glob('.pending-*')))
            self.assertEqual(self.source.read_bytes(), original)

    def test_quiet_and_short_sources_disk_budget_and_duration_limit_are_explicit(self):
        usage = namedtuple('Usage', 'total used free')(1000, 999, 1)
        with patch('studio_storage.shutil.disk_usage', return_value=usage), \
             patch('djmix.stream_ffmpeg', side_effect=AssertionError('Cannot decode without space')):
            with self.assertRaisesRegex(djmix.MixError, 'Not enough free space'):
                with feature.decoded_audio(self.source, 19.0713, self.root, lambda _: None): pass
        with self.assertRaisesRegex(djmix.MixError, 'four hours'):
            with feature.decoded_audio(self.source, feature.MAX_SECONDS + 1, self.root, lambda _: None): pass
        for seconds in (1, 9):
            sf.write(self.source, np.zeros(feature.RATE * seconds), feature.RATE)
            with self.assertRaisesRegex(djmix.MixError, 'eight seconds of audible audio'):
                with feature.decoded_audio(self.source, seconds, self.root, lambda _: None): pass
            self.assertFalse(list(self.root.glob('.pending-*')))

    def test_real_model_cancellation_preserves_cache_and_restores_threads(self):
        from studio_structure import _infer
        source = Path(__file__).resolve().parents[1] / 'assets/demo/media/01-night-drive.flac'
        duration = sf.info(source).duration
        destination = self.root / 'observations.npz'; destination.write_bytes(b'keep previous cache')
        event = threading.Event(); before = torch.get_num_threads()
        def progress(message):
            if message.startswith('Finding beats'): event.set()
        with cancel_on(event), self.assertRaisesRegex(djmix.MixError, 'cancelled'):
            _infer(source, duration, destination, progress)
        self.assertTrue(event.is_set())
        self.assertEqual(torch.get_num_threads(), before)
        self.assertEqual(destination.read_bytes(), b'keep previous cache')
        self.assertFalse(list(self.root.glob('.pending-*')))

    def test_failed_observation_write_keeps_previous_cache_and_cleans_staging(self):
        from studio_structure import _infer
        source = Path(__file__).resolve().parents[1] / 'assets/demo/media/01-night-drive.flac'
        destination = self.root / 'observations.npz'; destination.write_bytes(b'keep previous cache')
        def fail(handle, **arrays):
            handle.write(b'incomplete observation cache')
            raise OSError('disk write failed')
        with patch('studio_structure.np.savez_compressed', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'disk write failed'):
                _infer(source, sf.info(source).duration, destination, lambda _: None)
        self.assertEqual(destination.read_bytes(), b'keep previous cache')
        self.assertFalse(list(self.root.glob('.pending-*')))


if __name__ == '__main__': unittest.main()
