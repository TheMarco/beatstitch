"""Focused CueEditor viewport zoom/pan and zoomed cue-drag regression tests."""
import os
import copy
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication

from studio_engine import Clip, Track
from studio_widgets import CueEditor


def track(duration=200.0, bpm=120.0):
    return Track("t", "song.wav", "Song", duration, bpm=bpm, cue_in=0.0,
                 cue_out=duration, waveform=[0.5] * 64)


class CueEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make(self, width=146, cue_in=10.0, cue_out=190.0):
        editor = CueEditor({})
        editor.resize(width, 100)
        editor.show()
        self.addCleanup(editor.close)
        self.app.processEvents()
        editor.set_clip(track(), Clip("t", cue_in, cue_out), 0, 0)
        return editor

    def test_fit_maps_full_track(self):
        editor = self.make()
        self.assertEqual(editor.viewport, (0.0, 200.0))
        self.assertAlmostEqual(editor.time_at(0), 0.0)
        self.assertAlmostEqual(editor.time_at(editor.width()), 200.0, places=5)
        self.assertAlmostEqual(editor.x_of(100.0), editor.width() / 2, places=5)

    def test_zoom_centers_on_start_cue_by_default(self):
        editor = self.make()
        editor.zoom_in()
        self.assertAlmostEqual(editor.view_span, 100.0)
        self.assertLess(editor.view_start, 10.0)
        self.assertGreater(editor.view_end, 10.0)

    def test_focus_end_shows_short_section_near_end_cue(self):
        editor = self.make()
        editor.focus_end()
        self.assertLess(editor.view_span, 200.0)
        self.assertLessEqual(editor.view_start, 190.0)
        self.assertGreaterEqual(editor.view_end, 190.0)

    def test_pan_clamps_inside_source(self):
        editor = self.make()
        editor.focus_start()
        editor._pan_by(-1000.0)
        self.assertEqual(editor.view_start, 0.0)
        editor._pan_by(1000.0)
        self.assertEqual(editor.view_end, 200.0)

    def test_refresh_same_clip_preserves_zoom(self):
        editor = self.make()
        editor.focus_end()
        before = editor.viewport
        editor.set_clip(editor.track, editor.clip, 0, 0)
        self.assertEqual(editor.viewport, before)
        editor.set_clip(copy.deepcopy(editor.track), copy.deepcopy(editor.clip), 0, 0)
        self.assertEqual(editor.viewport, before)
        editor.set_clip(track(), Clip("t", 10.0, 190.0), 0, 0)
        self.assertEqual(editor.viewport, (0.0, 200.0))

    def test_zoomed_mouse_drag_emits_snapped_source_seconds(self):
        editor = self.make()
        editor.focus_start()
        start_x = editor.x_of(10.0)
        target = 14.0  # bar grid at 120bpm: 2s bars from 0
        target_x = editor.x_of(target)
        got = []
        editor.cuesChanged.connect(lambda a, b: got.append((a, b)))
        y = editor.height() // 2
        QTest.mousePress(editor, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(start_x), y))
        QTest.mouseMove(editor, QPoint(int(target_x), y))
        QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(target_x), y))
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(got[0][0], target, places=2)
        self.assertAlmostEqual(got[0][1], 190.0)

    def test_offscreen_handle_not_grabbed(self):
        editor = self.make()
        editor.focus_start()  # End cue far offscreen
        self.assertIsNone(editor._handle_at(editor.width() - 2))
        editor.setEnabled(False)
        got = []
        editor.cuesChanged.connect(lambda a, b: got.append((a, b)))
        x = editor.x_of(10.0)
        QTest.mousePress(editor, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(int(x), editor.height() // 2))
        self.assertIsNone(editor.dragging)
        self.assertEqual(got, [])

    def apply_keys(self, editor):
        got = []
        def apply(a, b):
            got.append((a, b))
            editor.clip.cue_in, editor.clip.cue_out = a, b
            editor.set_clip(editor.track, editor.clip, 0, 0)
        editor.cuesChanged.connect(apply)
        editor.setFocus()
        return got

    def test_keyboard_selects_marker_and_edits_by_bar_beat_and_fine_step(self):
        editor = self.make(width=500)
        got = self.apply_keys(editor)
        QTest.keyClick(editor, Qt.Key.Key_Down)
        self.assertEqual(got, [])
        self.assertEqual(editor._focus, 'End')
        self.assertLessEqual(editor.view_start, 190)
        QTest.keyClick(editor, Qt.Key.Key_Left)
        QTest.keyClick(editor, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClick(editor, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
        self.assertEqual(got[:2], [(10, 188), (10, 187.5)])
        self.assertAlmostEqual(got[-1][1], 187.51)
        QTest.keyClick(editor, Qt.Key.Key_Up)
        QTest.keyClick(editor, Qt.Key.Key_Right)
        self.assertEqual(editor.clip.cue_in, 12)
        interface = QAccessible.queryAccessibleInterface(editor)
        self.assertEqual(interface.role(), QAccessible.Role.Grouping)
        self.assertTrue(interface.state().focusable)
        self.assertIn('SetFocus', interface.actionInterface().actionNames())
        self.assertIn('Start selected, 12.000 seconds', interface.text(QAccessible.Text.Name))
        self.assertIn('End 187.510 seconds', interface.text(QAccessible.Text.Description))
        QTest.keyClick(editor, Qt.Key.Key_Plus)
        self.assertLess(editor.view_span, 8)
        QTest.keyClick(editor, Qt.Key.Key_0)
        self.assertEqual(editor.viewport, (0, 200))

    def test_keyboard_uses_irregular_grid_without_jumping_unsupported_gaps(self):
        editor = self.make()
        self.apply_keys(editor)
        grid = {'downbeats': [10., 12.04, 14.01, 30.], 'beats': [10., 10.51, 11.02, 11.53, 12.04]}
        with patch('studio_beat_grid.display_grid', return_value=grid):
            QTest.keyClick(editor, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
            self.assertAlmostEqual(editor.clip.cue_in, 10.51)
            QTest.keyClick(editor, Qt.Key.Key_Right)
            self.assertAlmostEqual(editor.clip.cue_in, 12.04)
            QTest.keyClick(editor, Qt.Key.Key_Right)
            self.assertAlmostEqual(editor.clip.cue_in, 14.01)
            QTest.keyClick(editor, Qt.Key.Key_Right)
            self.assertAlmostEqual(editor.clip.cue_in, 16.01)  # no leap to 30
            QTest.keyClick(editor, Qt.Key.Key_Left)
            self.assertAlmostEqual(editor.clip.cue_in, 14.01)

    def test_keyboard_bounds_disabled_and_empty_state(self):
        editor = self.make(cue_in=0, cue_out=2)
        got = self.apply_keys(editor)
        QTest.keyClick(editor, Qt.Key.Key_Left)
        QTest.keyClick(editor, Qt.Key.Key_Right)
        self.assertEqual(got, [])  # source bound / keep one bar
        QTest.keyClick(editor, Qt.Key.Key_Down)
        QTest.keyClick(editor, Qt.Key.Key_Left)
        self.assertEqual(got, [])
        editor.setEnabled(False)
        QTest.keyClick(editor, Qt.Key.Key_Right)
        self.assertEqual(got, [])
        editor.setEnabled(True)
        editor.set_clip(None, None, 0, 0)
        QTest.keyClick(editor, Qt.Key.Key_Right)
        self.assertIn('No song selected', editor.accessibleName())
        self.assertNotIn('Start 0.000 seconds', editor.accessibleDescription())
        self.assertEqual(got, [])

    def test_escape_cancels_mouse_preview_without_committing(self):
        editor = self.make(width=500)
        got = self.apply_keys(editor)
        editor.focus_start()
        y = editor.height() // 2
        QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=QPoint(round(editor.x_of(10)), y))
        QTest.mouseMove(editor, QPoint(round(editor.x_of(12)), y))
        self.assertEqual(editor.preview[0], 12)
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=QPoint(round(editor.x_of(12)), y))
        self.assertEqual(got, [])
        self.assertEqual(editor.cues(), (10, 190))
        self.assertIsNone(editor.dragging)


if __name__ == "__main__":
    unittest.main()
