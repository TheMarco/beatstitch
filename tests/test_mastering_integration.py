"""Saved mastering uses one complete master for playback, excerpts and export."""
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
from studio_engine import Session, layout
from studio_export import export_audio
from studio_render_cache import audio_recipe, prepare_listening
from tests.test_finishing import project


class MasteringIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.session = project(self.root)

    def test_save_roundtrip_old_projects_default_off_and_invalid_values_rejected(self):
        session = self.session
        self.assertFalse(session.mastering_enabled)
        session.mastering_enabled = True
        saved = session.to_dict()
        self.assertEqual(saved['version'], 17)
        self.assertTrue(Session.from_dict(json.loads(json.dumps(saved))).mastering_enabled)
        saved['version'] = 14; saved.pop('mastering_enabled')
        self.assertFalse(Session.from_dict(saved).mastering_enabled)
        for invalid in ('on', 1, None):
            saved['mastering_enabled'] = invalid
            with self.assertRaises(djmix.MixError): Session.from_dict(saved)

    def test_mastering_recipe_preserves_raw_audio_and_tracks_whole_mix(self):
        session = self.session
        original = audio_recipe(session)['fingerprint']
        region = (8 * djmix.SR, 12 * djmix.SR)
        dry_region = audio_recipe(session, region)['fingerprint']
        session.mastering_enabled = True
        self.assertEqual(original, audio_recipe(session, include_mastering=False)['fingerprint'])
        self.assertNotEqual(original, audio_recipe(session)['fingerprint'])
        mastered_region = audio_recipe(session, region)['fingerprint']
        session.clips[1].semitones = 1; session.clips[1].transpose = True
        self.assertNotEqual(mastered_region, audio_recipe(session, region)['fingerprint'])
        session.mastering_enabled = False
        self.assertEqual(dry_region, audio_recipe(session, region)['fingerprint'])

    def test_one_master_reuses_assembly_and_crops_and_exports_identical_audio(self):
        session, root = self.session, self.root
        rendered = prepare_listening(session, root / 'renders', audio_cache=root / 'audio-cache')
        before_files = {t.path: Path(t.path).read_bytes() for t in session.tracks.values()}
        session.mastering_enabled = True
        import studio_mastering
        with patch('studio_engine.render_session', side_effect=AssertionError('Mastering must reuse the assembled mix')), \
                patch('studio_mastering.master_file', wraps=studio_mastering.master_file) as finish:
            master = prepare_listening(session, root / 'renders', audio_cache=root / 'audio-cache')
            self.assertEqual((master['processed_tracks'], master['reused_tracks']), (0, 2))
            self.assertTrue(master['mastering_applied'])
            self.assertLessEqual(master['true_peak_db'], -1)
            region = (8 * djmix.SR, 18 * djmix.SR)
            crop = prepare_listening(session, root / 'renders', region, audio_cache=root / 'audio-cache')
            self.assertEqual(finish.call_count, 1)
            self.assertTrue(prepare_listening(session, root / 'renders', audio_cache=root / 'audio-cache')['cached_render'])
            session.mastering_enabled = False
            self.assertEqual(prepare_listening(session, root / 'renders', audio_cache=root / 'audio-cache')['path'], rendered['path'])
        full_audio, _ = sf.read(master['path'])
        crop_audio, _ = sf.read(crop['path'])
        pad = 250
        np.testing.assert_allclose(crop_audio[pad:-pad], full_audio[region[0] + pad:region[1] - pad], atol=2e-7)
        output = root / 'export.wav'
        export_audio(Path(master['path']), output, 'wav', protected_sources=[Path(t.path) for t in session.tracks.values()])
        np.testing.assert_array_equal(sf.read(output)[0], full_audio)
        self.assertEqual({p: Path(p).read_bytes() for p in before_files}, before_files)


class MasteringControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with patch.object(Studio, 'load_bands'): self.w = Studio(self.root / 'data')
        self.w.session = project(self.root); self.w.refresh()
        self.addCleanup(self.close_host)

    def close_host(self):
        self.w.close(); self.app.processEvents()

    def test_setting_is_a_draft_cancel_preserves_mix_and_apply_is_one_undo(self):
        w = self.w; before = copy.deepcopy(w.session.to_dict())
        w.show_mastering(); d = w.mastering_dialog
        d.enabled.setChecked(True)
        self.assertEqual(w.session.to_dict(), before)
        d.reject(); self.app.processEvents()
        self.assertEqual(w.session.to_dict(), before)
        w.show_mastering(); d = w.mastering_dialog
        d.enabled.setChecked(True); d.apply_changes(); self.app.processEvents()
        self.assertTrue(w.session.mastering_enabled)
        self.assertIn('On', w.mastering_btn.text())
        w.undo(); self.assertEqual(w.session.to_dict(), before)
        w.redo(); self.assertTrue(w.session.mastering_enabled)

    def test_late_cancelled_comparison_cannot_start_audio_or_apply(self):
        w = self.w; w.show_mastering(); d = w.mastering_dialog
        callbacks = {}; cancel = threading.Event()
        def job(work, finished, **kwargs):
            callbacks.update(work=work, finished=finished, failure=kwargs['failed_callback'])
            return cancel
        with patch.object(w, 'job', side_effect=job):
            d.prepare_comparison(); d.enabled.setChecked(True); d.reject()
        self.assertTrue(cancel.is_set())
        callbacks['finished'](({'folder': 'invalid'}, []))
        self.assertIsNone(d.audition)
        self.assertFalse(w.session.mastering_enabled)
        self.app.processEvents(); self.assertIsNone(w.mastering_dialog)

    def test_external_edit_prevents_stale_draft_apply(self):
        w = self.w; w.show_mastering(); d = w.mastering_dialog
        d.enabled.setChecked(True); w.session.name = 'Renamed elsewhere'
        d.apply_changes()
        self.assertFalse(w.session.mastering_enabled)
        self.assertIn('mix changed', d.status.text())


if __name__ == '__main__': unittest.main()
