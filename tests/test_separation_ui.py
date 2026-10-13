import copy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication

import djmix
from desktop_app import Studio
from studio_engine import Clip, Session, Track
from tests.test_separation import fake_worker


class SeparationWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root / 'Source.wav'
        t = np.arange(24 * djmix.SR) / djmix.SR
        audio = .12 * np.sin(2 * np.pi * 90 * t)
        sf.write(self.path, np.column_stack((audio, audio)), djmix.SR)
        for target in ('studio_separation.available', 'studio_separation_ui.available'):
            installed = patch(target, return_value=True); installed.start(); self.addCleanup(installed.stop)
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(self.root); self.addCleanup(self.close)
        track = Track('song', str(self.path), 'Source', 24., 120., 'A minor', cue_out=24.)
        self.w.session = Session(tracks={'song': track}, clips=[Clip('song', 0, 24, blend_bars=2) for _ in range(2)])
        self.w.selected_id = self.w.session.clips[1].id; self.w.refresh()

    def settle(self):
        deadline = time.monotonic() + 20
        while self.w.jobs and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.01)
        self.assertFalse(self.w.jobs); self.app.processEvents()

    def close(self):
        if self.w.separation_dialog: self.w.separation_dialog.reject()
        self.settle(); self.w.close(); self.app.processEvents()

    def test_prepare_compare_change_amount_apply_undo_redo_save(self):
        before = copy.deepcopy(self.w.session.to_dict())
        self.w.show_separation(); dialog = self.w.separation_dialog
        self.assertTrue(dialog.prepare.isEnabled(), dialog.status.text()); self.assertFalse(dialog.apply.isEnabled())
        with patch('studio_separation._run_worker', side_effect=fake_worker) as worker:
            dialog.prepare_audio(); self.settle()
            self.assertTrue(dialog.apply.isEnabled(), dialog.status.text())
            self.assertEqual(self.w.session.to_dict(), before)
            dialog.player.seek(2000); dialog.choices.buttons()[1].click()
            self.assertEqual(dialog.player.position(), 2000)
            dialog.amount.setValue(50)
            self.assertFalse(dialog.apply.isEnabled()); self.assertIsNone(dialog.report)
            dialog.prepare_audio(); self.settle(); self.assertEqual(worker.call_count, 1)
        dialog.apply_copy(); self.settle()
        replacement = self.w.session.tracks[self.w.session.clips[1].track_id]
        self.assertNotEqual(replacement.id, 'song'); self.assertTrue(Path(replacement.path).is_file())
        self.assertEqual(self.w.session.clips[0].track_id, 'song')
        self.w.undo(); self.assertEqual(self.w.session.clips[1].track_id, 'song')
        self.w.redo(); self.assertEqual(self.w.session.clips[1].track_id, replacement.id)
        path = self.root / 'Saved.djmix'; self.w.session.save(path)
        reopened = Session.from_dict(__import__('json').loads(path.read_text()))
        self.assertEqual(reopened.clips[1].track_id, replacement.id)
        self.assertTrue(Path(reopened.tracks[replacement.id].path).is_file())

    def test_missing_model_offers_download_without_preparing_audio(self):
        with patch('studio_separation_ui.available', return_value=False):
            self.w.show_separation(); dialog = self.w.separation_dialog
            self.assertTrue(dialog.prepare.isEnabled()); self.assertIn('Download vocal removal', dialog.prepare.text())
            self.assertIn('146 MB', dialog.status.text())
            self.assertFalse(dialog.apply.isEnabled()); self.assertTrue(self.w.act_separation.isEnabled())
            with patch('studio_downloads.ensure_models', return_value=False) as offer:
                dialog.prepare_audio()
                offer.assert_called_once_with(dialog, ['separation'], dialog.prepare_audio)
                self.assertFalse(dialog.busy); self.assertFalse(self.w.jobs)


if __name__ == '__main__': unittest.main()
