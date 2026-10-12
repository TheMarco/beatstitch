"""Preserve real waveform values while bounding decoded/filter working buffers."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from scipy.signal import butter, sosfilt
import soundfile as sf

import djmix
import studio_waveform as wave
from studio_process import cancel_on


class WaveformStreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'source.wav'
        rate = 32000; t = np.arange(round(13.071 * rate)) / rate
        audio = .2 * np.sin(2 * np.pi * 61 * t) + .09 * np.sin(2 * np.pi * 900 * t) + .03 * np.sin(2 * np.pi * 6200 * t)
        audio *= .2 + .8 * (t % 1 < .25)
        sf.write(self.source, np.column_stack((audio, .8 * audio)), rate, subtype='PCM_24')
        self.cache = self.root / 'library-cache'

    def test_values_match_previous_full_song_filter_and_cache_is_reused(self):
        raw = djmix.ffmpeg('-i', str(self.source), '-map', '0:a:0', '-ar', str(wave.RATE), '-ac', '1', '-f', 'f32le', '-').stdout
        audio = np.frombuffer(raw, dtype='<f4'); count = len(audio) // wave.HOP
        filters = (butter(4, wave.LOW_CUT, 'lowpass', fs=wave.RATE, output='sos'),
                   butter(2, [wave.LOW_CUT, wave.HIGH_CUT], 'bandpass', fs=wave.RATE, output='sos'),
                   butter(4, wave.HIGH_CUT, 'highpass', fs=wave.RATE, output='sos'))
        expected = np.empty((count, 3), np.float32)
        for i, f in enumerate(filters):
            filtered = sosfilt(f, audio)[:count * wave.HOP].reshape(count, wave.HOP)
            rms = np.sqrt(np.mean(filtered * filtered, axis=1))
            expected[:, i] = np.clip(rms / max(float(np.percentile(rms, 99.5)), 1e-6), 0, 1) ** .85
        sizes = []
        def observed_filter(sos, values, **kwargs):
            sizes.append(len(values)); return sosfilt(sos, values, **kwargs)
        with patch.object(wave, 'CHUNK_BINS', 17), patch('scipy.signal.sosfilt', side_effect=observed_filter):
            actual = wave.band_envelope(self.source, self.cache, 'song')
        np.testing.assert_allclose(actual, expected, atol=2e-7)
        self.assertLessEqual(max(sizes), wave.HOP * 17)
        with patch('djmix.stream_ffmpeg', side_effect=AssertionError('Cached waveform must not decode')):
            cached = wave.band_envelope(self.source, self.cache, 'song')
        np.testing.assert_array_equal(cached, actual.astype(np.float16).astype(np.float32))
        self.assertFalse(list(self.cache.glob('.*.npz')))
        event = threading.Event(); event.set()
        with cancel_on(event), self.assertRaisesRegex(djmix.MixError, 'cancelled'):
            wave.band_envelope(self.source, self.cache, 'song')

    def test_fragmented_blocks_silence_and_final_partial_bin(self):
        chunks = [np.zeros(n, dtype='<f4').tobytes() for n in (17, 333, 550, 53)]
        with patch('djmix.stream_ffmpeg', return_value=(chunk for chunk in chunks)):
            result = wave.band_envelope(self.source, None, 'song')
        self.assertEqual(result.shape, (953 // wave.HOP, 3)); self.assertFalse(result.any())

    def test_cancel_failure_and_source_change_never_publish_partial_cache(self):
        self.cache.mkdir(); cached = self.cache / 'song.bands.npz'; cached.write_bytes(b'previous cache')
        event = threading.Event(); closed = []
        def cancelled(*args, **kwargs):
            try:
                yield np.zeros(wave.HOP * 4, dtype='<f4').tobytes()
                event.set()
                yield np.zeros(wave.HOP, dtype='<f4').tobytes()
            finally: closed.append(True)
        with patch('djmix.stream_ffmpeg', side_effect=cancelled), cancel_on(event):
            with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                wave.band_envelope(self.source, self.cache, 'song')
        self.assertEqual(closed, [True]); self.assertEqual(cached.read_bytes(), b'previous cache')
        def changed(*args, **kwargs):
            yield np.zeros(wave.HOP * 4, dtype='<f4').tobytes()
            self.source.touch()
        with patch('djmix.stream_ffmpeg', side_effect=changed):
            with self.assertRaisesRegex(djmix.MixError, 'source changed'):
                wave.band_envelope(self.source, self.cache, 'song')
        self.assertEqual(cached.read_bytes(), b'previous cache')
        with patch.object(wave, 'MAX_BINS', 1):
            with self.assertRaisesRegex(djmix.MixError, 'too long'):
                wave.band_envelope(self.source, self.cache, 'song')
        self.assertEqual(cached.read_bytes(), b'previous cache')
        self.assertFalse(list(self.cache.glob('.*.npz')))


if __name__ == '__main__': unittest.main()
