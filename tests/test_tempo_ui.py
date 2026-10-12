"""Tempo drafts, one-step Undo, native controls and inverse playback seeking."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from desktop_app import Studio
from studio_engine import Clip, Session, Track
from studio_tempo_curve import session_curve
from studio_tempo_ui import TempoDialog


class TempoUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        with patch.object(Studio, 'load_bands'): self.window = Studio(Path(self.temp.name))
        self.addCleanup(self.window.close)
        self.window.session = Session(target_bpm=120, tracks={'a': Track('a', 'missing.wav', 'Song', 120, 120)},
                                      clips=[Clip('a', 0, 120)])
        self.window.selected_id = self.window.session.clips[0].id; self.window.refresh()

    def test_editor_drafts_apply_cancel_disable_and_undo(self):
        w = self.window; original = copy.deepcopy(w.session.to_dict())
        def cancel(dialog):
            dialog.enabled.setChecked(True); dialog.rows[1]['bpm'].setValue(130)
            dialog.reject(); return dialog.result()
        with patch.object(TempoDialog, 'exec', cancel): w.show_tempo_curve()
        self.assertEqual(w.session.to_dict(), original)
        def accept(dialog):
            dialog.enabled.setChecked(True)
            dialog.rows[1]['bpm'].setValue(132)
            self.assertTrue(dialog.apply.isEnabled())
            QTest.mouseClick(dialog.apply, Qt.MouseButton.LeftButton)
            return dialog.result()
        with patch.object(TempoDialog, 'exec', accept): w.show_tempo_curve()
        self.assertTrue(w.session.tempo_enabled); self.assertFalse(w.tempo.isEnabled())
        self.assertEqual(w.session.tempo_points[-1]['bpm'], 132)
        self.assertEqual(len(w.history), 1)
        applied = copy.deepcopy(w.session.to_dict())
        w.undo(); self.assertEqual(w.session.to_dict(), original); self.assertTrue(w.tempo.isEnabled())
        w.redo(); self.assertEqual(w.session.to_dict(), applied)
        def disable(dialog):
            dialog.enabled.setChecked(False); dialog.accept(); return dialog.result()
        with patch.object(TempoDialog, 'exec', disable): w.show_tempo_curve()
        self.assertFalse(w.session.tempo_enabled); self.assertEqual(w.session.tempo_points, applied['tempo_points'])
        self.assertTrue(w.tempo.isEnabled())

    def test_bad_points_block_apply_and_playback_inverts_elapsed_time(self):
        w = self.window; dialog = TempoDialog(w.session, w); self.addCleanup(dialog.close)
        dialog.enabled.setChecked(True)
        self.assertIn('Steady at 120 BPM', dialog.message.text())
        dialog.rows[1]['bar'].setValue(1)
        self.assertFalse(dialog.apply.isEnabled()); self.assertIn('Point 2 needs a later bar than point 1 (bar 1)', dialog.message.text())
        dialog.rows[1]['bar'].setValue(33); dialog.rows[1]['bpm'].setValue(144)
        self.assertTrue(dialog.apply.isEnabled())
        self.assertNotIn('Steady at', dialog.message.text())
        dialog.add.click(); self.assertEqual(len(dialog.rows), 3)
        self.assertEqual(dialog.rows[-1]['bar'].value(), 49)
        dialog.rows[-1]['remove'].click(); self.assertEqual(len(dialog.rows), 2)
        self.assertFalse(dialog.rows[0]['remove'].isEnabled())
        w.session.tempo_enabled = True; w.session.tempo_points = dialog.draft.tempo_points
        w.audio_curve = session_curve(w.session); w.audio_origin = 12.
        expected = 28.
        elapsed = float(w.audio_curve.to_output(expected) - w.audio_curve.to_output(w.audio_origin))
        with patch.object(w, 'player', Mock(position=lambda: elapsed * 1000)):
            self.assertAlmostEqual(w.playback_reference(), expected, places=9)
        w.mix_cursor = expected; w.refresh()
        self.assertIn('0:26', w.time_label.text())
        self.assertIn('tempo curve', w.simple_song.metadata.text())
        # Exercise drawing directly: Qt logs exceptions from paint callbacks
        # without propagating them to unittest.
        image = w.timeline._render_static()
        self.assertFalse(image.isNull())


if __name__ == '__main__': unittest.main()
