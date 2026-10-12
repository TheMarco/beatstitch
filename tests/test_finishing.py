"""Audible mix-edge changes must agree across previews, export, caching and Undo."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication

import djmix
from desktop_app import Studio
from studio_engine import Clip, Session, Track, layout, render_session
from studio_finishing import envelope, limits, preview_region
from studio_render_cache import audio_recipe
from studio_transition_audio import prepare_blend


def project(root):
    tracks = {}
    for identity, hz in [('a', 110), ('b', 165)]:
        path = root / f'{identity}.wav'
        t = np.arange(24 * djmix.SR) / djmix.SR
        # A quiet decaying musical tail after the chosen End, not trailing silence.
        gain = np.where(t < 18, 1., np.exp(-(t - 18) / 3))
        mono = .12 * (np.sin(2 * np.pi * hz * t) + .3 * np.sin(2 * np.pi * hz * 2 * t)) * gain
        sf.write(path, np.column_stack((mono, mono * .95)), djmix.SR, subtype='PCM_24')
        tracks[identity] = Track(identity, str(path), f'Song {identity}', 24, 120, 'A minor',
                                 cue_in=2, cue_out=18, waveform=[.4, .6] * 500)
    return Session(target_bpm=120, tracks=tracks, clips=[Clip(k, 2, 18, blend_bars=2) for k in tracks])


class FinishingAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.session = project(self.root)

    def test_fades_match_full_export_crop_and_stems_while_reusing_audio(self):
        session = self.session; cache = self.root / 'cache'
        dry = render_session(session, self.root / 'dry.wav', cache_dir=cache)
        session.fade_in_bars, session.fade_out_bars = 2, 2
        faded = render_session(session, self.root / 'faded.wav', cache_dir=cache)
        self.assertEqual((faded['processed_tracks'], faded['reused_tracks']), (0, 2))
        self.assertEqual(faded['mixed_mix_frames'], 0)
        self.assertEqual(faded['reused_mix_frames'], layout(session)[-1]['end'])
        a, _ = sf.read(dry['path'], dtype='float64', always_2d=True)
        b, _ = sf.read(faded['path'], dtype='float64', always_2d=True)
        gain = envelope(session, layout(session), np.arange(len(a)))
        self.assertEqual(dry['attenuation_db'], faded['attenuation_db'])
        np.testing.assert_allclose(b, a * gain[:, None], atol=3e-7)
        start = round(2 * djmix.SR); end = round(5 * djmix.SR)
        crop = render_session(session, self.root / 'crop.wav', region=(start, end), cache_dir=cache,
                              stems={0: self.root / 'solo.wav'})
        c, _ = sf.read(crop['path'], always_2d=True)
        solo, _ = sf.read(self.root / 'solo.wav', always_2d=True)
        # Preview boundaries have their own 5 ms anti-click envelope.
        pad = round(.006 * djmix.SR)
        np.testing.assert_allclose(c[pad:-pad], b[start + pad:end - pad], atol=3e-7)
        np.testing.assert_allclose(c, solo, atol=3e-7)
        self.assertAlmostEqual(faded['duration'], 28, places=4)
        self.assertLessEqual(faded['true_peak_db'], -1)

    def test_finish_recipe_changes_only_affected_previews_and_pair_context(self):
        session = self.session; cache = self.root / 'cache'
        middle = (10 * djmix.SR, 18 * djmix.SR)
        before = audio_recipe(session, middle)['fingerprint']; full = audio_recipe(session)['fingerprint']
        session.fade_in_bars = session.fade_out_bars = 1
        self.assertEqual(before, audio_recipe(session, middle)['fingerprint'])
        self.assertNotEqual(full, audio_recipe(session)['fingerprint'])
        def render_fake(s, destination, region=None, stems=None, **_):
            frames = region[1] - region[0]
            for path in [destination, *stems.values()]: sf.write(path, np.zeros((frames, 2)), djmix.SR)
            return {'level_balance': []}
        with patch('studio_transition_audio.render_session', side_effect=render_fake):
            first = prepare_blend(session, 1, cache)
            session.fade_in_bars = 4  # Reaches into the preview context before the blend.
            second = prepare_blend(session, 1, cache)
            self.assertNotEqual(first['folder'], second['folder'])
            self.assertTrue(prepare_blend(session, 1, cache)['cached'])

    def test_limits_keep_fades_outside_blends_and_protected_passages(self):
        session = self.session; positions = layout(session)
        self.assertEqual(limits(session, positions)['in'][0], 6)
        session.fade_in_bars = 7
        with self.assertRaisesRegex(djmix.MixError, 'at most 6 bars'): layout(session)
        session.fade_in_bars = 2
        session.clips[0].protected_moments = [{'start': 4, 'end': 6, 'label': 'Opening vocal', 'kind': 'vocal'}]
        with self.assertRaisesRegex(djmix.MixError, 'Opening vocal'): layout(session)
        session.fade_in_bars = 1; layout(session)
        for bad in (True, 1.5, -1, 33):
            session.fade_in_bars = bad
            with self.assertRaises(djmix.MixError): layout(session)
        session.fade_in_bars = 0; session.clips = session.clips[:1]
        session.clips[0].protected_moments = []; session.fade_in_bars = session.fade_out_bars = 5
        with self.assertRaisesRegex(djmix.MixError, 'fades overlap'): layout(session)

    def test_recorded_tail_is_retained_with_exact_duration(self):
        session = self.session; prior = layout(session)[-1]['end']
        session.clips[-1].cue_out = session.tracks['b'].duration
        region = preview_region(session, 'out')
        self.assertEqual(layout(session)[-1]['end'] - prior, 6 * djmix.SR)
        result = render_session(session, self.root / 'tail.wav', region=region, cache_dir=self.root / 'cache')
        audio, _ = sf.read(result['path'], always_2d=True)
        self.assertGreater(np.sqrt(np.mean(audio[-2 * djmix.SR:-djmix.SR] ** 2)), .001)
        self.assertAlmostEqual(result['duration'], (region[1] - region[0]) / djmix.SR, places=4)
        value = session.to_dict(); self.assertEqual(Session.from_dict(value).to_dict(), value)
        value['version'] = 7; value.pop('fade_in_bars'); value.pop('fade_out_bars')
        self.assertEqual(Session.from_dict(value).fade_out_bars, 0)


class FinishingWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(self.root); self.w.session = project(self.root); self.w.refresh()
        self.addCleanup(self.close)

    def close(self):
        if self.w.finishing_dialog: self.w.finishing_dialog.reject()
        self.w.close(); self.app.processEvents()

    def test_cancel_and_apply_with_tail_are_drafts_and_one_undo(self):
        w = self.w; before = copy.deepcopy(w.session.to_dict())
        w.show_finishing(); d = w.finishing_dialog
        d.spins['in'].setValue(1); d.spins['out'].setValue(2); d.keep['out'].setChecked(True)
        self.assertEqual(w.session.to_dict(), before)
        self.assertEqual(d.draft.clips[-1].cue_out, 24)
        d.reject(); self.app.processEvents(); self.assertEqual(w.session.to_dict(), before)
        w.show_finishing(); d = w.finishing_dialog
        d.spins['in'].setValue(1); d.spins['out'].setValue(2); d.keep['out'].setChecked(True)
        d.apply_changes(); self.app.processEvents()
        self.assertEqual((w.session.fade_in_bars, w.session.fade_out_bars, w.session.clips[-1].cue_out), (1, 2, 24))
        self.assertEqual(len(w.history), 1)
        w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_late_cancel_cannot_autoplay_or_apply_a_draft(self):
        w = self.w; before = copy.deepcopy(w.session.to_dict()); w.show_finishing(); d = w.finishing_dialog
        d.available = True
        cancel = threading.Event()
        with patch.object(w, 'job', return_value=cancel) as job: d.listen_edge()
        self.assertTrue(d.busy); self.assertTrue(job.call_args.kwargs['cancellable'])
        d.reject(); self.assertTrue(cancel.is_set()); self.assertTrue(d.closing)
        with patch.object(d.player, 'play') as play:
            job.call_args.args[1]({'path': 'unused'})
            play.assert_not_called()
        self.app.processEvents(); self.assertIsNone(w.finishing_dialog)
        self.assertEqual(w.session.to_dict(), before)

    def test_preview_path_is_pinned_and_released_on_close(self):
        w = self.w; w.show_finishing(); d = w.finishing_dialog
        d.preview_path = str(self.root / 'renders' / 'preview.wav'); w.update_storage_pins()
        self.assertIn(str(Path(d.preview_path).resolve()), json.loads(w.storage.pin_path.read_text()))
        d.reject(); self.app.processEvents()
        self.assertNotIn(str((self.root / 'renders' / 'preview.wav').resolve()), json.loads(w.storage.pin_path.read_text()))


if __name__ == '__main__': unittest.main()
