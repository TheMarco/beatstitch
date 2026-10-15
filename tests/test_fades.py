"""Fade sound, persistence, cache locality and a continuous-mix drag contract."""
import copy
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import soundfile as sf
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import djmix
from desktop_app import Studio
from studio_engine import Clip, Session, _weights, layout, render_session
from studio_fades import FIELDS, gain, token, set_duration
from studio_render_cache import audio_recipe
from studio_transition_audio import prepare_blend
from tests.test_transitions import project
from tests.test_finishing import project as audio_project


class FadeMathTests(unittest.TestCase):
    def test_old_projects_preserve_exact_default_envelopes_and_round_trip(self):
        s = project(); data = s.to_dict(); data['version'] = 16
        for c in data['clips']:
            for field in FIELDS: c.pop(field)
        loaded = Session.from_dict(data)
        n = np.arange(-7, 1007)
        for incoming in (False, True):
            for bass in (False, True):
                old = _weights(n, 0, 1000, incoming, bass, 120)
                new = _weights(n, 0, 1000, incoming, bass, 120, loaded.clips[1])
                for a, b in zip(old, new): np.testing.assert_array_equal(a, b)
        c = loaded.clips[1]; c.fades_linked = False; c.incoming_fade_bars = .5
        c.outgoing_fade_curve = 'slow'
        self.assertEqual(Session.from_dict(loaded.to_dict()).to_dict(), loaded.to_dict())

    def test_curve_shapes_and_independent_fade_anchors(self):
        clip = Clip('x', 0, 60, blend_bars=8, fades_linked=False)
        for shape, in_mid, out_mid in (('smooth', math.sqrt(.5), math.sqrt(.5)),
                ('linear', .5, .5), ('fast', 1-1/(math.exp(2)+1), 1/(math.exp(2)+1)),
                ('slow', 1/(math.exp(2)+1), 1-1/(math.exp(2)+1))):
            clip.incoming_fade_curve = clip.outgoing_fade_curve = shape
            self.assertAlmostEqual(float(gain(.5, True, clip)), in_mid)
            self.assertAlmostEqual(float(gain(.5, False, clip)), out_mid)
            p = np.linspace(0, 1, 1001)
            self.assertTrue(np.all(np.diff(gain(p, True, clip)) >= -1e-12))
            self.assertTrue(np.all(np.diff(gain(p, False, clip)) <= 1e-12))
        clip.incoming_fade_curve = clip.outgoing_fade_curve = 'linear'
        clip.incoming_fade_bars = 2; clip.outgoing_fade_bars = 4
        np.testing.assert_allclose(gain(np.array([0, .125, .25, .5, 1]), True, clip), [0, .5, 1, 1, 1])
        np.testing.assert_allclose(gain(np.array([0, .5, .75, 1]), False, clip), [1, 1, .5, 0])

    def test_linking_is_not_an_audio_edit_until_effective_settings_change(self):
        c = Clip('x', 0, 20, blend_bars=8)
        self.assertIsNone(token(c)); c.fades_linked = False; self.assertIsNone(token(c))
        set_duration(c, True, 2)
        self.assertIsNotNone(token(c)); self.assertIsNone(token(c, False))
        c.blend_bars = 1
        self.assertIsNone(token(c))  # Both effective fades now fill the smaller overlap.

    def test_invalid_saved_fades_rejected(self):
        for field, value in (('fades_linked', 'yes'), ('incoming_fade_bars', float('nan')),
                ('outgoing_fade_bars', 0), ('incoming_fade_curve', 'unknown')):
            s = project(); setattr(s.clips[1], field, value)
            with self.subTest(field=field), self.assertRaises(djmix.MixError): Session.from_dict(s.to_dict())


class FadeRenderTests(unittest.TestCase):
    def test_fade_edits_reuse_sources_change_only_overlap_and_match_crops_and_stems(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); s = audio_project(root); cache = root / 'cache'
            c = s.clips[1]; c.bass_swap = False
            baseline = render_session(s, root / 'before.wav', cache_dir=cache)
            old_key = audio_recipe(s)['fingerprint']
            c.fades_linked = False; c.incoming_fade_bars = .5
            c.incoming_fade_curve = 'fast'; c.outgoing_fade_curve = 'slow'
            self.assertNotEqual(audio_recipe(s)['fingerprint'], old_key)
            changed = render_session(s, root / 'after.wav', cache_dir=cache)
            self.assertEqual((changed['processed_tracks'], changed['reused_tracks']), (0, 2))
            self.assertGreater(changed['reused_mix_frames'], 0)
            p = layout(s); start, end = p[1]['start'], p[0]['end']
            a, _ = sf.read(baseline['path'], always_2d=True); b, _ = sf.read(changed['path'], always_2d=True)
            self.assertEqual(changed['attenuation_db'], baseline['attenuation_db'])
            np.testing.assert_allclose(a[:start], b[:start], atol=3e-7)
            np.testing.assert_allclose(a[end:], b[end:], atol=3e-7)
            self.assertGreater(np.max(abs(a[start:end] - b[start:end])), .001)
            region = (start + djmix.SR // 4, end - djmix.SR // 4)
            crop = render_session(s, root / 'crop.wav', region=region, cache_dir=cache)
            data, _ = sf.read(crop['path'], always_2d=True); pad = 1000
            np.testing.assert_allclose(data[pad:-pad], b[region[0]+pad:region[1]-pad], atol=4e-7)
            report = prepare_blend(s, 1, cache)
            sounds = [sf.read(Path(report['folder']) / f'{name}.wav', always_2d=True)[0]
                      for name in ('outgoing', 'incoming', 'blend')]
            np.testing.assert_allclose(sounds[0]+sounds[1], sounds[2], atol=4e-7)
            with patch('studio_transition_audio.render_session', side_effect=AssertionError('stale cache')):
                c.fades_linked = False
                self.assertTrue(prepare_blend(s, 1, cache)['cached'])
                c.outgoing_fade_curve = 'linear'
                with self.assertRaisesRegex(AssertionError, 'stale cache'): prepare_blend(s, 1, cache)


class FadeGestureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(Path(self.temp.name)); self.addCleanup(self.w.close)
        self.w.session = project(); self.w.selected_id = self.w.session.clips[2].id
        self.w.refresh(); self.w.show(); self.app.processEvents()
        self.v = self.w.timeline; self.v.scale = 15; self.v.refresh(); self.app.processEvents()

    def test_clip_body_moves_entry_and_later_songs_without_mutating_during_drag(self):
        w, v = self.w, self.v; before = copy.deepcopy(w.session.to_dict()); positions = layout(w.session)
        start = v.clip_rect(positions[2], 2).center().toPoint(); shift = QPoint(30, 0)  # One bar at 120 BPM.
        with patch.object(w, 'job', side_effect=AssertionError('drag started processing')):
            QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(v.viewport(), start-shift)
            self.assertEqual(w.session.to_dict(), before)
            self.assertEqual(v.positions[2]['start'], positions[2]['start']-2*djmix.SR)
            self.assertEqual(v.positions[3]['start'], positions[3]['start']-2*djmix.SR)
            QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=start-shift)
        self.assertEqual(w.session.clips[2].blend_bars, 3); self.assertEqual(len(w.history), 1)
        w.undo(); self.assertEqual(w.session.to_dict(), before)
        start = v.clip_rect(v.positions[2], 2).center().toPoint()
        QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(v.viewport(), start-shift); QTest.keyClick(v, Qt.Key.Key_Escape)
        QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=start-shift)
        self.assertEqual(w.session.to_dict(), before); self.assertEqual(len(w.history), 0)

    def test_fade_handle_linking_independent_draft_and_undo(self):
        w, v = self.w, self.v; before = copy.deepcopy(w.session.to_dict())
        handle = next(r for r, _, incoming in v.edits.fade_handles() if incoming).center().toPoint()
        target = handle-QPoint(15,0)  # Shorten by half a bar.
        QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=handle)
        QTest.mouseMove(v.viewport(), target)
        self.assertEqual(w.session.to_dict(), before)
        QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=target)
        self.assertEqual(w.selected_clip().incoming_fade_bars, 1.5)
        self.assertEqual(w.selected_clip().outgoing_fade_bars, 1.5)
        self.assertEqual(len(w.history), 1); w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_editor_waveform_handle_is_a_visual_only_preview_until_release(self):
        w = self.w; w.edit_fades(); self.app.processEvents(); d = w.transition_editor
        self.addCleanup(lambda: d.reject() if w.transition_editor is not None else None)
        before = copy.deepcopy(d.session.to_dict()); actual = copy.deepcopy(w.session.to_dict())
        rect = next(r for r, incoming in d.view.fade_handles() if incoming)
        origin = rect.center().toPoint()
        delta = round((d.view.width()-2*d.view._PAD)/(d.view._end-d.view._start))  # One second = half a bar.
        target = origin-QPoint(delta, 0)
        with patch.object(w, 'job', side_effect=AssertionError('fade drag started processing')):
            QTest.mousePress(d.view, Qt.MouseButton.LeftButton, pos=origin)
            QTest.mouseMove(d.view, target)
            self.assertEqual(d.session.to_dict(), before)
            self.assertEqual(w.session.to_dict(), actual)
            QTest.mouseRelease(d.view, Qt.MouseButton.LeftButton, pos=target)
        self.assertEqual(d.session.clips[2].incoming_fade_bars, 1.5)
        self.assertEqual(d.session.clips[2].outgoing_fade_bars, 1.5)
        self.assertEqual(w.session.to_dict(), actual)
        changed = copy.deepcopy(d.session.to_dict())
        origin = next(r for r, incoming in d.view.fade_handles() if incoming).center().toPoint()
        QTest.mousePress(d.view, Qt.MouseButton.LeftButton, pos=origin)
        QTest.mouseMove(d.view, origin-QPoint(delta, 0)); QTest.keyClick(d.view, Qt.Key.Key_Escape)
        QTest.mouseRelease(d.view, Qt.MouseButton.LeftButton, pos=origin-QPoint(delta, 0))
        self.assertEqual(d.session.to_dict(), changed)
        w.edit_fades(); d = w.transition_editor
        d.fades.link_checkbox.setChecked(False); d.fades.in_spin.setValue(.5)
        d.fades.out_combo.setCurrentIndex(d.fades.out_combo.findData('slow'))
        self.assertEqual(w.session.to_dict(), before)
        self.assertTrue(d.compare.isEnabled()); d.apply_changes()
        self.assertFalse(w.selected_clip().fades_linked)
        self.assertEqual(w.selected_clip().incoming_fade_bars, .5)
        self.assertEqual(w.selected_clip().outgoing_fade_curve, 'slow')
        self.assertEqual(len(w.history), 1); w.undo(); self.assertEqual(w.session.to_dict(), before)
