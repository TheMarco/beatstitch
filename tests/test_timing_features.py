"""Chunk boundaries must not alter the rhythm used to correct a song."""
from collections import namedtuple
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from scipy.ndimage import gaussian_filter1d, uniform_filter1d
from scipy.signal import butter, sosfiltfilt
import soundfile as sf

import djmix
import studio_timing_features as feature
from studio_process import cancel_on


def reference(path):
    raw = djmix.ffmpeg('-i', str(path), '-map', '0:a:0', '-ar', '12000', '-ac', '1', '-f', 'f32le', '-').stdout
    audio = np.frombuffer(raw, dtype='<f4')
    if len(audio) < 12000:
        return np.zeros(0)
    filtered = sosfiltfilt(butter(3, [35, 180], btype='bandpass', fs=12000, output='sos'), audio)
    energy = np.sqrt(np.maximum(0, uniform_filter1d(filtered * filtered, 60)))[::12]
    return gaussian_filter1d(np.maximum(0, energy - np.r_[np.zeros(10), energy[:-10]]), 2)


class TimingFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'song.wav'
        self.cache = self.root / 'timing-cache'
        rate = 32000; t = np.arange(round(19.0713 * rate)) / rate
        signal = .07 * np.sin(2 * np.pi * 73 * t) + .03 * np.sin(2 * np.pi * 960 * t)
        signal *= (.15 + (t % .5 < .08)); signal[t > 17] = 0
        signal[0] = .4; signal[-1] = -.4
        sf.write(self.source, np.column_stack((signal, .8 * signal)), rate, subtype='FLOAT')

    def test_matches_full_filter_across_irregular_blocks_and_source_endpoints(self):
        expected = reference(self.source)
        sizes = []; original = feature.sosfilt
        def observed(sos, audio, **kwargs):
            sizes.append(len(audio)); return original(sos, audio, **kwargs)
        with patch.object(feature, 'BLOCK', 7013), patch.object(feature, 'sosfilt', side_effect=observed):
            actual = feature.extract(self.source, self.cache)
        # Centered rolling sums restart at block boundaries. The rounding noise
        # near silence is below 1e-8; IIR samples themselves are unchanged.
        np.testing.assert_allclose(actual, expected, rtol=1e-8, atol=1e-8)
        self.assertLessEqual(max(sizes), 7013)
        self.assertFalse(list(self.cache.glob('.pending-*')))

    def test_silence_and_subsecond_source_remain_empty_or_zero(self):
        for duration in (.01, .999, 1.011):
            with self.subTest(duration=duration):
                sf.write(self.source, np.zeros(round(duration * 12000)), 12000)
                actual = feature.extract(self.source, self.cache)
                np.testing.assert_array_equal(actual, reference(self.source))

    def test_cancel_at_each_disk_pass_cleans_files_and_does_not_modify_source(self):
        original = self.source.read_bytes()
        for stage in ('Reading song timing', 'Measuring rhythm', 'Finding beat attacks'):
            with self.subTest(stage=stage):
                event = threading.Event(); reached = []
                def progress(message):
                    if message.startswith(stage):
                        reached.append(True); event.set()
                with cancel_on(event), self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                    feature.extract(self.source, self.cache, progress)
                self.assertTrue(reached)
                self.assertFalse(list(self.cache.glob('.pending-*')))
                self.assertEqual(self.source.read_bytes(), original)

    def test_low_space_and_duration_limit_fail_before_decoding(self):
        usage = namedtuple('Usage', 'total used free')(1000, 999, 1)
        with patch('studio_storage.shutil.disk_usage', return_value=usage), \
             patch('djmix.stream_ffmpeg', side_effect=AssertionError('Must not decode')):
            with self.assertRaisesRegex(djmix.MixError, 'Not enough free space'):
                feature.extract(self.source, self.cache)
        with patch.object(feature, 'MAX_SECONDS', 1), \
             patch('djmix.stream_ffmpeg', side_effect=AssertionError('Must not decode')):
            with self.assertRaisesRegex(djmix.MixError, 'four hours'):
                feature.extract(self.source, self.cache)
        self.assertFalse(list(self.cache.glob('.pending-*')))

    def test_detected_drift_map_and_existing_cache_stay_identical(self):
        import studio_timing
        rate = 12000; duration = 125
        t = np.arange(duration * rate) / rate
        audio = .015 * np.sin(2 * np.pi * 440 * t)
        numbers = np.arange(240); beats = 2 + numbers * .5 + .08 * np.sin(numbers / 24)
        pulse = np.arange(round(.05 * rate)) / rate
        kick = .3 * np.sin(2 * np.pi * 80 * pulse) * np.exp(-pulse * 80)
        for beat in beats:
            start = round(beat * rate); audio[start:start + len(kick)] += kick
        sf.write(self.source, audio, rate, subtype='FLOAT')
        with patch('studio_timing.features', return_value=reference(self.source)):
            expected = studio_timing.analyze(self.source, 120, duration)
        self.assertEqual(expected['state'], 'drift')
        actual = studio_timing.analyze(self.source, 120, duration, self.cache)
        self.assertEqual(actual, expected)
        with patch('studio_timing.features', side_effect=AssertionError('Must reuse compatible analysis')):
            self.assertEqual(studio_timing.analyze(self.source, 120, duration, self.cache), expected)
        self.assertFalse(list(self.cache.glob('.pending-*')))

    def test_cancel_during_publication_preserves_prior_cache_and_removes_temporary_json(self):
        import studio_timing
        from studio_cache import digest
        key = [studio_timing.VERSION, studio_timing.signature(self.source), 120., 19.0713]
        self.cache.mkdir()
        destination = self.cache / (digest(key) + '.json')
        destination.write_text('prior cache')
        event = threading.Event(); write = Path.write_text
        def interrupted(path, *args, **kwargs):
            result = write(path, *args, **kwargs)
            if path.parent == self.cache and path.suffix == '.tmp': event.set()
            return result
        with patch.object(Path, 'write_text', interrupted), cancel_on(event):
            with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                studio_timing.analyze(self.source, 120., 19.0713, self.cache)
        self.assertTrue(event.is_set())
        self.assertEqual(destination.read_text(), 'prior cache')
        self.assertFalse(list(self.cache.glob('*.tmp')))
        self.assertFalse(list(self.cache.glob('.pending-*')))


if __name__ == '__main__': unittest.main()
