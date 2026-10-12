import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import djmix
from studio_beat_grid import grid_settings
from studio_drum_loops import JOIN_SECONDS, loop_choices, prepare_loop, repeat_audio
from studio_engine import Track
from studio_process import cancel_on
from studio_storage import StorageManager


class RepeatedAudioTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(7)
        self.audio = rng.uniform(-.7, .7, (3000, 2)).astype(np.float32)

    def test_exact_length_attacks_context_and_stereo_survive(self):
        a, b, fade, repeats = 600, 1400, 120, 3
        raw, smooth, joins = repeat_audio(self.audio, a, b, repeats, fade)
        self.assertEqual(len(raw), len(self.audio) + (b - a) * repeats)
        np.testing.assert_array_equal(raw, np.concatenate([self.audio[:b], *([self.audio[a:b]] * repeats), self.audio[b:]]))
        self.assertEqual(joins, [1400, 2200, 3000])
        untouched = np.ones(len(raw), dtype=bool)
        for join in joins:
            untouched[join - fade:join] = False
            np.testing.assert_array_equal(smooth[join:join + 20], self.audio[a:a + 20])
            np.testing.assert_array_equal(smooth[join - 1], self.audio[a - 1])
        np.testing.assert_array_equal(smooth[untouched], raw[untouched])
        np.testing.assert_array_equal(smooth[-(len(self.audio) - b):], self.audio[b:])
        self.assertLessEqual(np.max(np.abs(smooth)), np.max(np.abs(self.audio)))

    def test_join_step_is_replaced_by_natural_source_step(self):
        x = np.linspace(-.8, .8, 3000, dtype=np.float32)
        audio = np.column_stack([x, -x])
        raw, smooth, joins = repeat_audio(audio, 600, 1400, 2, 120)
        for join in joins:
            self.assertGreater(abs(raw[join, 0] - raw[join - 1, 0]), .1)
            self.assertAlmostEqual(float(smooth[join, 0] - smooth[join - 1, 0]), float(x[600] - x[599]))
        np.testing.assert_allclose(smooth[:, 0], -smooth[:, 1])

    def test_invalid_or_silent_inputs_rejected(self):
        for start, end, repeats, fade in [(0, 1400, 1, 120), (600, 700, 1, 120), (600, 1400, 0, 120), (600, 1400, 9, 120)]:
            with self.assertRaises(djmix.MixError): repeat_audio(self.audio, start, end, repeats, fade)
        for audio in (np.zeros_like(self.audio), self.audio[:, :1], self.audio * np.nan):
            with self.assertRaises(djmix.MixError): repeat_audio(audio, 600, 1400, 1, 120)


class LoopPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.source = self.root / 'drums.wav'
        # Test rhythm at 120 BPM, with opposite-polarity stereo to detect mono cancellation.
        t = np.arange(48 * djmix.SR) / djmix.SR
        phase = t % .5
        x = (.3 * np.sin(2 * np.pi * 90 * phase) * np.exp(-phase * 30)).astype(np.float32)
        sf.write(self.source, np.column_stack([x, -x]), djmix.SR, subtype='FLOAT')
        self.track = Track('drums', str(self.source), 'Test drums', 48., bpm=120.)
        self.track.beat_grid = grid_settings(self.track, 120., 0.)
        self.cache = self.root / 'audio-cache'

    def tearDown(self): self.temp.cleanup()

    def choice(self): return {**loop_choices(self.track, 4)[0], 'side': 'intro'}

    def test_only_complete_edge_bars_with_current_source(self):
        choices = loop_choices(self.track, 4)
        self.assertTrue(choices)
        self.assertTrue(all(c['end'] - c['start'] == 8 and c['end'] <= 14.4 for c in choices))
        self.assertTrue(all(c['start'] >= 33.6 for c in loop_choices(self.track, 4, 'outro')))
        os.utime(self.source, ns=(self.source.stat().st_atime_ns, self.source.stat().st_mtime_ns + 1_000_000))
        self.assertEqual(loop_choices(self.track), [])

    def test_discontinuous_or_missing_grid_is_not_invented(self):
        with patch('studio_drum_loops.display_grid', return_value={'bars': [
            {'number': 0, 'start': 1., 'end': 3.}, {'number': 1, 'start': 4., 'end': 6.}]}):
            self.assertEqual(loop_choices(self.track, 2), [])
        with patch('studio_drum_loops.display_grid', return_value={}):
            self.assertEqual(loop_choices(self.track), [])

    @unittest.skipUnless(djmix.tool_path('ffmpeg'), 'FFmpeg required')
    def test_actual_render_reuse_and_corrupt_audio_repair(self):
        before = self.source.read_bytes(); metadata = copy.deepcopy(self.track)
        report = prepare_loop(self.track, self.choice(), 2, self.cache)
        folder = Path(report['folder'])
        self.assertEqual(report['frames'], report['source_frames'] + 16 * djmix.SR)
        self.assertEqual(report['added_seconds'], 16.)
        self.assertEqual(len(report['join_times']), 2)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.track, metadata)
        messages = []
        with patch('studio_drum_loops.djmix.ffmpeg', side_effect=AssertionError('Unnecessary processing')):
            self.assertEqual(prepare_loop(self.track, self.choice(), 2, self.cache, messages.append), report)
        self.assertIn('Reusing this exact loop trial', messages)
        (folder / 'draft.wav').write_bytes(b'broken')
        repaired = prepare_loop(self.track, self.choice(), 2, self.cache)
        self.assertEqual(sf.info(folder / 'draft.wav').frames, repaired['frames'])

    def test_cancellation_and_stale_choice_publish_nothing(self):
        cancelled = threading.Event(); cancelled.set()
        with cancel_on(cancelled), self.assertRaisesRegex(djmix.MixError, 'cancelled'):
            prepare_loop(self.track, self.choice(), 2, self.cache)
        bad = {**self.choice(), 'start': 2.123}
        with self.assertRaisesRegex(djmix.MixError, 'no longer matches'):
            prepare_loop(self.track, bad, 2, self.cache)
        self.assertEqual(list(self.cache.rglob('report.json')), [])

    @unittest.skipUnless(djmix.tool_path('ffmpeg'), 'FFmpeg required')
    def test_generated_trials_obey_storage_budget_and_live_pins(self):
        report = prepare_loop(self.track, self.choice(), 1, self.cache)
        storage = StorageManager(self.root)
        self.assertGreater(storage.inspect()['bytes'], 0)
        storage.pin([report['folder']])
        with patch.object(storage, 'limit', return_value=0):
            self.assertEqual(storage.prune()['count'], 0)
            storage.close()
            self.assertEqual(storage.prune()['count'], 1)
        self.assertFalse(Path(report['folder']).exists())
        self.assertTrue(self.source.exists())

    @unittest.skipUnless(djmix.tool_path('ffmpeg'), 'FFmpeg required')
    def test_source_change_during_preparation_is_rejected(self):
        def progress(message):
            if message.startswith('Repeating'):
                os.utime(self.source, ns=(self.source.stat().st_atime_ns, self.source.stat().st_mtime_ns + 1_000_000))
        with self.assertRaisesRegex(djmix.MixError, 'song changed'):
            prepare_loop(self.track, self.choice(), 2, self.cache, progress)
        self.assertEqual(list(self.cache.rglob('report.json')), [])


if __name__ == '__main__': unittest.main()
