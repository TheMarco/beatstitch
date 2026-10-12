"""Transition effects must stay local and agree across export, crops and solos."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import djmix
from studio_engine import Clip, Session, layout, render_session
from studio_render_cache import audio_recipe
from studio_transition_audio import pair_range, prepare_blend
from studio_transition_effects import write_effect
from tests.test_finishing import project


class EffectAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.session = project(self.root)
        # The outgoing example has both low and upper musical content.
        path = Path(self.session.tracks['a'].path); audio, rate = sf.read(path, always_2d=True)
        audio += (.04 * np.sin(np.arange(len(audio)) * 2 * np.pi * 960 / rate))[:, None]
        sf.write(path, audio, rate, subtype='PCM_24')
        self.cache = self.root / 'cache'

    def test_filter_agrees_with_crop_and_reuses_sources_and_unaffected_assembly(self):
        session = self.session
        dry = render_session(session, self.root / 'dry.wav', cache_dir=self.cache)
        session.clips[1].transition_effect = 'filter'
        full = render_session(session, self.root / 'filter.wav', cache_dir=self.cache)
        self.assertEqual((full['processed_tracks'], full['reused_tracks']), (0, 2))
        self.assertEqual((full['effects_processed'], full['effects_reused']), (1, 0))
        self.assertGreater(full['reused_mix_frames'], 0); self.assertGreater(full['mixed_mix_frames'], 0)
        positions = layout(session); start, end = positions[1]['start'], positions[0]['end']
        a, _ = sf.read(dry['path'], always_2d=True); b, _ = sf.read(full['path'], always_2d=True)
        self.assertEqual(full['attenuation_db'], dry['attenuation_db'])
        np.testing.assert_allclose(a[:start], b[:start], atol=2e-7)
        np.testing.assert_allclose(a[end:], b[end:], atol=2e-7)
        self.assertGreater(np.max(np.abs(a[start:end] - b[start:end])), .005)
        region = (start + djmix.SR, end - djmix.SR // 4)
        crop = render_session(session, self.root / 'crop.wav', region=region, cache_dir=self.cache)
        c, _ = sf.read(crop['path'], always_2d=True); pad = round(.006 * djmix.SR)
        np.testing.assert_allclose(c[pad:-pad], b[region[0] + pad:region[1] - pad], atol=3e-7)
        self.assertEqual((crop['effects_processed'], crop['effects_reused']), (0, 1))
        self.assertEqual(crop['processed_tracks'], 0)

    def test_echo_has_four_decaying_repeats_inside_overlap_and_matches_crop(self):
        session = self.session; session.clips[1].transition_effect = 'echo'
        positions = layout(session); start, end = positions[1]['start'], positions[0]['end']
        full = render_session(session, self.root / 'echo.wav', cache_dir=self.cache,
                              stems={0: self.root / 'outgoing.wav'})
        outgoing, _ = sf.read(self.root / 'outgoing.wav', always_2d=True)
        delay = round(60 / session.target_bpm * djmix.SR)
        taps = outgoing[end - 4 * delay:end].reshape(4, delay, 2)
        self.assertGreater(np.max(np.abs(taps[0])), .0001)
        for i in range(1, 4): np.testing.assert_allclose(taps[i], taps[0] / 2 ** i, atol=3e-7)
        self.assertLess(np.max(np.abs(outgoing[end:])), 1e-7)
        self.assertLess(np.max(np.abs(taps[:, [0, -1]])), 1e-7)
        region = (end - 3 * delay, end + delay)
        crop = render_session(session, self.root / 'crop.wav', region=region, cache_dir=self.cache,
                              stems={0: self.root / 'crop-outgoing.wav'})
        a, _ = sf.read(full['path'], always_2d=True); b, _ = sf.read(crop['path'], always_2d=True)
        pad = round(.006 * djmix.SR)
        np.testing.assert_allclose(b[pad:-pad], a[region[0] + pad:region[1] - pad], atol=3e-7)
        self.assertEqual(crop['effects_reused'], 1)
        self.assertEqual(full['duration'], positions[-1]['end'] / djmix.SR)
        self.assertLessEqual(full['true_peak_db'], -1)

    def test_recipe_is_local_and_old_projects_default_to_no_effect(self):
        session = self.session; session.clips.append(Clip('b', 2, 18, blend_bars=2))
        first = pair_range(session, 1)
        before = audio_recipe(session, first)['fingerprint']; whole = audio_recipe(session)['fingerprint']
        session.clips[2].transition_effect = 'echo'
        self.assertEqual(audio_recipe(session, first)['fingerprint'], before)
        self.assertNotEqual(audio_recipe(session)['fingerprint'], whole)
        saved = session.to_dict(); self.assertEqual(Session.from_dict(saved).to_dict(), saved)
        legacy = copy.deepcopy(saved); legacy['version'] = 8
        for clip in legacy['clips']: clip.pop('transition_effect')
        self.assertTrue(all(c.transition_effect == 'none' for c in Session.from_dict(legacy).clips))
        saved['clips'][1]['transition_effect'] = 'surprise'
        with self.assertRaisesRegex(djmix.MixError, 'Choose No extra effect'): Session.from_dict(saved)

    def test_pair_cache_changes_with_effect_and_reuses_prior_choice(self):
        def render_fake(session, destination, region=None, stems=None, **_):
            audio = np.zeros((region[1] - region[0], 2))
            for path in [destination, *stems.values()]: sf.write(path, audio, djmix.SR)
            return {'level_balance': []}
        with patch('studio_transition_audio.render_session', side_effect=render_fake) as renderer:
            dry = prepare_blend(self.session, 1, self.cache)
            self.session.clips[1].transition_effect = 'echo'
            echo = prepare_blend(self.session, 1, self.cache)
            self.assertNotEqual(dry['folder'], echo['folder'])
            self.assertTrue(prepare_blend(self.session, 1, self.cache)['cached'])
            self.session.clips[1].transition_effect = 'none'
            restored = prepare_blend(self.session, 1, self.cache)
            self.assertEqual(restored['folder'], dry['folder'])
            self.assertTrue(restored['cached'])
            self.assertEqual(renderer.call_count, 2)

    def test_smallest_overlap_and_cancellation_are_bounded(self):
        # One bar at the maximum project tempo is still click-free and finite.
        frames = round(240 / 300 * djmix.SR)
        n = np.arange(frames); high = np.column_stack((np.sin(n * .1), np.sin(n * .1))) * .1
        path = self.root / 'short.raw'
        write_effect(path, np.zeros_like(high), high, 0, frames, 'echo', True, 300, lambda _: None)
        audio = djmix.raw_audio(path)
        try:
            self.assertTrue(np.isfinite(audio).all()); self.assertEqual(len(audio), frames)
            self.assertLess(np.max(np.abs(audio[-1])), 1e-8)
        finally: audio._mmap.close()
        self.session.clips[1].transition_effect = 'echo'
        def cancel(message):
            if message == 'Adding quiet echo repeats': raise djmix.MixError('Operation cancelled.')
        with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
            render_session(self.session, self.root / 'cancel.wav', cache_dir=self.cache, progress=cancel)
        self.assertFalse((self.root / 'cancel.wav').exists())
        self.assertEqual(list((self.cache / 'effects').glob('*')), [])


if __name__ == '__main__': unittest.main()
