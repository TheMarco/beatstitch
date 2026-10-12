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
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import djmix
from desktop_app import Studio
from studio_beat_grid import grid_settings
from studio_engine import Clip, Session, Track


class LoopExtensionWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root / 'Drums.wav'
        t = np.arange(24 * djmix.SR) / djmix.SR
        mono = .1 * np.sin(2 * np.pi * 90 * t) * np.exp(-(t % .5) * 20)
        sf.write(self.path, np.column_stack([mono, mono]), djmix.SR)
        track = Track('drums', str(self.path), 'Test drums', 24, 120, 'A minor', cue_out=24)
        track.beat_grid = grid_settings(track, 120, 0)
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(self.root); self.addCleanup(self.close)
        self.w.session = Session(target_bpm=120, tracks={'drums': track}, clips=[Clip('drums', 0, 24, blend_bars=2) for _ in range(2)])
        self.w.selected_id = self.w.session.clips[1].id; self.w.refresh()

    def settle(self):
        deadline = time.monotonic() + 20
        while self.w.jobs and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.01)
        self.assertFalse(self.w.jobs); self.app.processEvents()

    def close(self):
        if self.w.loop_dialog: self.w.loop_dialog.reject()
        self.settle(); self.w.close(); self.app.processEvents()

    def open(self):
        self.w.show_loop_extension(); d = self.w.loop_dialog
        d.bars.setCurrentIndex(1)  # Two bars fit within this short fixture's edge.
        self.assertTrue(d.prepare.isEnabled(), d.details.text()); return d

    @unittest.skipUnless(djmix.tool_path('ffmpeg'), 'FFmpeg required')
    def test_real_prepare_switch_invalidate_apply_undo_redo_save(self):
        w = self.w; before = copy.deepcopy(w.session.to_dict()); d = self.open()
        d.prepare.click(); self.settle()
        self.assertIsNotNone(d.report, d.status.text()); self.assertTrue(d.apply.isEnabled())
        self.assertEqual(w.session.to_dict(), before)
        self.assertEqual(len(d.cache_paths), 2)
        d.choices.buttons()[1].setChecked(True)
        self.assertEqual(d.player.variant, 'draft'); self.assertEqual(d.wave.variant, 'draft')
        self.assertEqual(d.player.mixer.target, (0., 1.))
        d.first_join.click(); self.assertGreater(d.player.position(), 0)
        d.smooth.setChecked(False)
        self.assertIsNone(d.report); self.assertIsNone(d.player.mixer); self.assertFalse(d.apply.isEnabled())
        d.prepare.click(); self.settle(); self.assertTrue(d.apply.isEnabled(), d.status.text())
        d.apply.click(); self.settle(); self.assertIsNone(w.loop_dialog)
        self.assertEqual(len(w.history), 1); self.assertEqual(w.session.clips[0], Clip(**before['clips'][0]))
        replacement = w.session.tracks[w.session.clips[1].track_id]
        self.assertNotEqual(replacement.path, str(self.path)); self.assertTrue(Path(replacement.path).is_file())
        self.assertTrue(replacement.fingerprint); self.assertEqual(w.session.clips[1].id, before['clips'][1]['id'])
        w.undo(); self.assertEqual(w.session.clips, [Clip(**v) for v in before['clips']])
        self.assertIn(replacement.id, w.session.tracks)
        w.redo(); self.assertEqual(w.session.clips[1].track_id, replacement.id)
        save = self.root / 'saved.djmix'; self.assertTrue(w.write_project(save))
        self.assertTrue(self.path.exists())

    def test_no_grid_and_outer_edge_explain_limits_and_help(self):
        w = self.w; w.session.tracks['drums'].beat_grid = {}; w.show_loop_extension(); d = w.loop_dialog
        self.assertFalse(d.prepare.isEnabled()); self.assertIn('check beats', d.details.text())
        self.assertTrue(d.check_beats.isEnabled()); self.assertFalse(d.apply.isEnabled())
        d.side.setCurrentIndex(1)
        self.assertFalse(d.overlap.isEnabled()); self.assertEqual(d.context.currentData(), 'passage')
        self.assertFalse(d.context.popup.actions()[1].isEnabled())
        d.show_help(); self.assertEqual(d.help_window.current_topic, 'drum-loops')
        d.reject(); self.app.processEvents(); self.assertIsNone(w.loop_dialog)

    def test_waveform_keyboard_seeks_both_versions_in_seconds(self):
        d = self.open(); times = []
        d.wave.report = {'duration': 12., 'current_wave': [.1], 'draft_wave': [.1], 'join_times': []}
        d.wave.seekRequested.connect(times.append); d.wave.position = 4.
        QTest.keyClick(d.wave, Qt.Key.Key_Right); QTest.keyClick(d.wave, Qt.Key.Key_Left); QTest.keyClick(d.wave, Qt.Key.Key_Home)
        self.assertEqual(times, [5., 3., 0.]); self.assertEqual(d.seek.singleStep(), 1000)

    def test_picker_accessibility_press_opens_menu_and_action_selects_context(self):
        from PySide6.QtGui import QAccessible, QAccessibleActionInterface
        d = self.open()
        interface = QAccessible.queryAccessibleInterface(d.context).actionInterface()
        interface.doAction(QAccessibleActionInterface.pressAction())
        QTest.qWait(180)  # Qt's accessible button press uses the 100 ms click animation.
        self.assertTrue(d.context.popup.isVisible())
        d.context.popup.actions()[1].trigger(); d.context.popup.close()
        self.assertEqual(d.context.currentData(), 'blend')
        QTest.keyClick(d.context, Qt.Key.Key_Left)
        self.assertEqual(d.context.currentData(), 'passage')

    def test_close_after_worker_result_queued_cannot_apply_or_keep_dialog_alive(self):
        before = copy.deepcopy(self.w.session.to_dict()); d = self.open()
        # This result must not be accessed: cancellation occurs after the worker
        # returned but before the main thread delivers its queued signals.
        with patch('studio_loop_ui.prepare_extension', return_value={}), patch('studio_loop_ui.prepare_comparison', return_value={}):
            d.prepare.click(); self.assertTrue(self.w.pool.waitForDone(10000)); d.reject(); self.settle()
        self.assertIsNone(self.w.loop_dialog); self.assertEqual(self.w.session.to_dict(), before)


if __name__ == '__main__': unittest.main()
