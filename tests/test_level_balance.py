"""Overlap-only dynamics, partial-render consistency, and reusable processing."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import djmix
from studio_blend_choices import prepare_choice_comparison
from studio_engine import Clip, Session, Track, layout, render_session
from studio_level_balance import decision, envelope
from studio_render_cache import audio_recipe
from studio_transition_audio import prepare_blend


class LevelBalanceTests(unittest.TestCase):
    def test_limits_silence_dynamics_ramps_and_legacy_project(self):
        self.assertEqual(decision(-18, -18.5, 10)['state'], 'unchanged')
        for values in ((None, -18, 10), (-60, -18, 10), (-18, -10, .8)):
            self.assertEqual(decision(*values)['state'], 'uncertain')
        result = decision(-12, -21, 10)
        self.assertEqual((result['outgoing_db'], result['incoming_db']), (-3, 0))
        self.assertEqual(decision(-21, -12, 10)['incoming_db'], -3)
        n = np.arange(10000)
        gains = envelope(n, [(1000, 9000, 2000, -3)])
        np.testing.assert_array_equal(gains[:1000], 1)
        np.testing.assert_array_equal(gains[9000:], 1)
        np.testing.assert_allclose(gains[3000:6999], 10 ** (-3 / 20))
        self.assertLess(np.max(np.abs(np.diff(gains))), .0003)
        # During the flat correction, the original loud/soft contrast is preserved.
        audio = np.array([.05, .20])
        adjusted = audio * gains[4000:4002]
        self.assertAlmostEqual(adjusted[1] / adjusted[0], 4)
        session = Session(tracks={'a': Track('a', '/missing.wav', 'A', 12, 120)}, clips=[Clip('a', 0, 12)])
        old = session.to_dict(); old['version'] = 5; old['clips'][0].pop('balance_levels')
        self.assertFalse(Session.from_dict(old).clips[0].balance_levels)
        session.clips[0].balance_levels = True
        self.assertTrue(Session.from_dict(session.to_dict()).clips[0].balance_levels)
        invalid = session.to_dict(); invalid['clips'][0]['balance_levels'] = 'yes'
        with self.assertRaisesRegex(djmix.MixError, 'on or off'):
            Session.from_dict(invalid)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'audio-cache'

    def fixture(self):
        rate = djmix.SR
        t = np.arange(16 * rate) / rate
        tracks = {}
        for i in range(2):
            # Both tracks vary naturally; whole-song matching cannot balance this overlap.
            strength = np.where(t < (12 if i == 0 else 4), .035, .14)
            tone = np.sin(2 * np.pi * (240 + i * 100) * t) + .3 * np.sin(2 * np.pi * 1300 * t)
            audio = strength * tone * (1 + .1 * np.sin(2 * np.pi * 2 * t))
            path = self.root / f'{i}.wav'
            sf.write(path, np.column_stack((audio, audio)), rate, subtype='FLOAT')
            tracks[str(i)] = Track(str(i), str(path), f'Song {i}', 16, 120, 'C minor')
        return Session(target_bpm=120, tracks=tracks, clips=[Clip(str(i), 0, 16, blend_bars=2) for i in range(2)])

    def test_audio_only_changes_in_overlap_reuses_sources_and_matches_partial_preview(self):
        session = self.fixture()
        before = copy.deepcopy(session.to_dict())
        off = render_session(session, self.root / 'off.wav', cache_dir=self.cache)
        original_recipe = audio_recipe(session)['fingerprint']
        solo = (0, 4 * djmix.SR)
        solo_recipe = audio_recipe(session, solo)
        session.clips[1].balance_levels = True
        self.assertNotEqual(original_recipe, audio_recipe(session)['fingerprint'])
        # A solo-region recipe must survive a blend-only edit.
        self.assertEqual(solo_recipe, audio_recipe(session, solo))
        with patch('djmix.prepare', side_effect=AssertionError('Processed songs must be reused')):
            on = render_session(session, self.root / 'on.wav', cache_dir=self.cache)
        self.assertEqual((on['processed_tracks'], on['reused_tracks']), (0, 2))
        self.assertEqual(on['mixed_mix_frames'], 4 * djmix.SR)
        self.assertEqual(on['reused_mix_frames'], 24 * djmix.SR)
        result = on['level_balance'][0]
        self.assertEqual(result['state'], 'balanced')
        self.assertEqual(result['outgoing_db'], -3)
        self.assertEqual(result['incoming_db'], 0)
        rate = djmix.SR
        a, _ = sf.read(self.root / 'off.wav', dtype='float64')
        b, _ = sf.read(self.root / 'on.wav', dtype='float64')
        # Remove shared export attenuation before checking the musical envelope.
        a /= 10 ** (off['attenuation_db'] / 20)
        b /= 10 ** (on['attenuation_db'] / 20)
        np.testing.assert_allclose(a[:12 * rate], b[:12 * rate], atol=4e-7)
        np.testing.assert_allclose(a[16 * rate:], b[16 * rate:], atol=4e-7)
        self.assertGreater(np.max(abs(a[13 * rate:15 * rate] - b[13 * rate:15 * rate])), .001)
        self.assertLessEqual(on['true_peak_db'], -1)
        meter = djmix.meter
        def cached_levels(path, **kwargs):
            self.assertFalse(path.name.startswith('overlap-'), 'Must reuse overlap measurements')
            return meter(path, **kwargs)
        # Render only the middle of the overlap. Its measurement and gain must stay identical.
        with patch('djmix.meter', side_effect=cached_levels):
            short = render_session(session, self.root / 'short.wav', cache_dir=self.cache, region=(13 * rate, 15 * rate))
        self.assertEqual(short['level_balance'][0]['outgoing_db'], result['outgoing_db'])
        segment, _ = sf.read(self.root / 'short.wav')
        segment /= 10 ** (short['attenuation_db'] / 20)
        np.testing.assert_allclose(segment[480:-480], b[13 * rate + 480:15 * rate - 480], atol=5e-7)
        self.assertEqual(Session.from_dict(before).clips[1].balance_levels, False)

    def test_pair_and_comparison_invalidate_then_reuse_balanced_audio(self):
        session = self.fixture()
        current = prepare_blend(session, 1, self.cache)
        draft = copy.deepcopy(session.clips[1]); draft.balance_levels = True
        comparison = prepare_choice_comparison(session, 1, draft, self.cache)
        self.assertEqual(comparison['draft_level_balance'][0]['state'], 'balanced')
        folder = Path(comparison['folder'])
        measured = [djmix.meter(folder / f'{name}.wav') for name in ('current', 'draft')]
        self.assertLess(abs(measured[0]['lufs'] - measured[1]['lufs']), .2)
        session.clips[1] = draft
        balanced = prepare_blend(session, 1, self.cache)
        self.assertNotEqual(current['folder'], balanced['folder'])
        parts = [sf.read(Path(balanced['folder']) / f'{name}.wav')[0] for name in ('outgoing', 'incoming', 'blend')]
        np.testing.assert_allclose(parts[0] + parts[1], parts[2], atol=4e-7)
        with patch('studio_transition_audio.render_session', side_effect=AssertionError('Must reuse balanced pair')):
            again = prepare_blend(session, 1, self.cache)
        self.assertTrue(again['cached'])
        self.assertEqual(again['level_balance'], balanced['level_balance'])
        # Move this unchanged pair later in a set; evidence must name its new pair index.
        first = copy.deepcopy(session.clips[0]); first.id = 'opening'; first.balance_levels = False
        session.clips.insert(0, first)
        with patch('studio_transition_audio.render_session', side_effect=AssertionError('Must reuse moved pair')):
            moved = prepare_blend(session, 2, self.cache)
        self.assertEqual(moved['folder'], balanced['folder'])
        self.assertEqual(moved['level_balance'][0]['index'], 2)
        session.clips[2].balance_levels = False
        moved_draft = copy.deepcopy(session.clips[2]); moved_draft.balance_levels = True
        with patch('studio_blend_choices.prepare_listening', side_effect=AssertionError('Must reuse moved comparison')):
            moved_comparison = prepare_choice_comparison(session, 2, moved_draft, self.cache)
        self.assertTrue(moved_comparison['cached'])
        self.assertEqual(moved_comparison['draft_level_balance'][0]['index'], 2)

    def test_cancellation_in_level_analysis_keeps_destination_and_removes_scratch(self):
        session = self.fixture(); session.clips[1].balance_levels = True
        target = self.root / 'kept.wav'; target.write_bytes(b'original destination')
        def cancel(message):
            if message.startswith('Measuring the audio inside'):
                raise djmix.MixError('Operation cancelled.')
        with self.assertRaisesRegex(djmix.MixError, 'Operation cancelled'):
            render_session(session, target, cache_dir=self.cache, progress=cancel)
        self.assertEqual(target.read_bytes(), b'original destination')
        self.assertEqual(list(self.root.glob('dj-render-*')), [])


if __name__ == '__main__':
    unittest.main()
