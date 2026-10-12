"""Regression checks for recovery ownership and the native editing controls."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QAccessible, QAccessibleActionInterface, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_app import Studio
from studio_engine import Clip, Session, Track


class DesktopControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.band_patch = patch.object(Studio, "load_bands")
        self.band_patch.start()
        self.addCleanup(self.band_patch.stop)
        self.windows = []
        self.addCleanup(self.close_windows)
        self.window = self.open_window()
        track = Track("a", str(self.root / "song.wav"), "Song", 120, 120, "C minor", cue_out=120)
        self.window.session = Session(tracks={track.id: track}, clips=[Clip(track.id, 0, 120)])
        self.window.selected_id = self.window.session.clips[0].id
        self.window.refresh()

    def open_window(self):
        window = Studio(self.root)
        self.windows.append(window)
        return window

    def close_windows(self):
        for window in self.windows:
            window.close()
        self.app.processEvents()

    def restart(self):
        self.window.close()
        self.windows.remove(self.window)
        self.window = self.open_window()
        return self.window

    def test_new_mix_restarts_without_owning_old_file(self):
        old = self.root / "Untitled mix.djmix"
        self.window.write_project(old)
        original = old.read_bytes()
        self.window.new_project()
        self.window.add_track("a", 0)
        self.window.set_clip_value("cue_in", 4)
        recovered = self.restart()
        self.assertIsNone(recovered.project_path)
        self.assertEqual(recovered.session.clips[0].cue_in, 4)
        with patch.object(recovered, "save_project_as") as save_as:
            recovered.save_project()
            save_as.assert_called_once()
        self.assertEqual(old.read_bytes(), original)

    def test_recovery_retains_exact_owner_and_save_as_updates_it(self):
        first = self.root / "one" / "Mix.djmix"
        second = self.root / "two" / "Mix.djmix"
        self.window.write_project(first)
        first_bytes = first.read_bytes()
        self.window.write_project(second)
        self.window.set_clip_value("cue_in", 4)
        # Stale preferences must not redirect the recovered Save.
        self.window.write_prefs(last_project=str(first))
        recovered = self.restart()
        self.assertEqual(recovered.project_path, second.resolve())
        self.assertTrue(recovered.dirty)
        recovered.save_project()
        self.assertEqual(first.read_bytes(), first_bytes)
        self.assertEqual(json.loads(second.read_text())["clips"][0]["cue_in"], 4)
        self.assertFalse(self.restart().dirty)

    def test_legacy_autosave_does_not_guess_owner_by_name(self):
        saved = self.root / "Untitled mix.djmix"
        self.window.write_project(saved)
        self.window.close()
        self.windows.remove(self.window)
        self.window.session.save(self.root / "autosave.djmix")  # Old format, no owner.
        recovered = self.open_window()
        self.assertIsNone(recovered.project_path)
        self.assertTrue(recovered.dirty)

    def test_shift_changes_drag_sensitivity_without_a_jump(self):
        widget = self.window.tempo
        commits = []
        widget.committed.connect(commits.append)

        def mouse(kind, y, shift=False):
            buttons = Qt.MouseButton.LeftButton
            modifiers = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
            event = QMouseEvent(kind, QPointF(20, y), QPointF(20, y), buttons, buttons, modifiers)
            QApplication.sendEvent(widget, event)

        mouse(QEvent.Type.MouseButtonPress, 40)
        mouse(QEvent.Type.MouseMove, 0)
        self.assertEqual(widget.value(), 138)
        mouse(QEvent.Type.MouseMove, 0, True)
        self.assertEqual(widget.value(), 138)
        mouse(QEvent.Type.MouseMove, -8, True)
        self.assertAlmostEqual(widget.value(), 138.02)
        mouse(QEvent.Type.MouseMove, -8)
        self.assertAlmostEqual(widget.value(), 138.02)
        mouse(QEvent.Type.MouseMove, -12)
        mouse(QEvent.Type.MouseButtonRelease, -12)
        self.assertEqual(commits, [139.02])
        self.assertEqual(self.window.session.target_bpm, 139.02)

    def test_accessible_values_edit_models_and_keyboard_typing_still_work(self):
        w = self.window
        for widget in (w.tempo, w.bpm, w.cue_in, w.cue_out, w.blend_bars, w.transpose_knob):
            interface = QAccessible.queryAccessibleInterface(widget)
            self.assertTrue(interface.text(QAccessible.Text.Name))
            self.assertIsNotNone(interface.valueInterface())
            self.assertEqual(interface.valueInterface().currentValue(), widget.value())
        tempo = QAccessible.queryAccessibleInterface(w.tempo).valueInterface()
        tempo.setCurrentValue(130)
        self.assertEqual(w.session.target_bpm, 130)
        QAccessible.queryAccessibleInterface(w.cue_in).valueInterface().setCurrentValue(3.25)
        self.assertEqual(w.selected_clip().cue_in, 3.25)
        QAccessible.queryAccessibleInterface(w.transpose_knob).valueInterface().setCurrentValue(2)
        self.assertEqual(w.selected_clip().shift, 2)
        QTest.keyClick(w.tempo, Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(w.session.target_bpm, 130.01)
        w.cue_in.edit()
        w.cue_in.editor.setText("0:07.500")
        QTest.keyClick(w.cue_in.editor, Qt.Key.Key_Return)
        self.assertEqual(w.selected_clip().cue_in, 7.5)
        w.cue_in.edit()
        w.cue_in.editor.setText("0:08.000")
        QTest.keyClick(w.cue_in.editor, Qt.Key.Key_Escape)
        self.assertEqual(w.selected_clip().cue_in, 7.5)

    def test_transpose_bypass_remembers_shift_through_undo_and_restart(self):
        w = self.window
        w.set_transpose(3)
        action = QAccessible.queryAccessibleInterface(w.transpose_toggle).actionInterface()
        action.doAction(QAccessibleActionInterface.toggleAction())
        self.assertEqual((w.selected_clip().shift, w.selected_clip().semitones), (0, 3))
        self.assertEqual(w.transpose_knob.value(), 3)
        w.undo()
        self.assertEqual(w.selected_clip().shift, 3)
        w.redo()
        self.assertEqual((w.selected_clip().shift, w.selected_clip().semitones), (0, 3))
        recovered = self.restart()
        self.assertFalse(recovered.transpose_toggle.isChecked())
        self.assertEqual(recovered.transpose_knob.value(), 3)
        recovered.transpose_toggle.click()
        self.assertEqual(recovered.selected_clip().shift, 3)

    def test_manual_key_and_unknown_override_estimate_with_undo_and_recovery(self):
        from test_key_detection import observation
        from studio_key import remember_analysis
        w = self.window
        track = w.session.tracks['a']
        Path(track.path).write_bytes(b'source identity fixture')
        remember_analysis(track, observation(Path(track.path)))
        w.refresh()
        self.assertTrue(w.use_key_btn.isEnabled())
        w.use_key_btn.click()
        self.assertEqual((track.key, track.key_origin), ('D minor', 'detected'))
        # Explicitly choosing the same value still establishes an override.
        w.key.activated.emit(w.key.currentIndex())
        self.assertEqual(track.key_origin, 'manual')
        w.key.setCurrentIndex(w.key.findData(''))
        w.key.activated.emit(w.key.currentIndex())
        self.assertEqual((track.key, track.key_origin), ('', 'manual'))
        self.assertIn('chosen by you', w.key_hint.text())
        w.undo()
        self.assertEqual((w.session.tracks['a'].key, w.session.tracks['a'].key_origin), ('D minor', 'manual'))
        w.redo()
        recovered = self.restart()
        self.assertEqual((recovered.session.tracks['a'].key, recovered.session.tracks['a'].key_origin), ('', 'manual'))
        self.assertTrue(recovered.use_key_btn.isEnabled())

    def test_wrapped_key_guidance_leaves_pitch_and_timing_controls_clear(self):
        from test_key_detection import observation
        from studio_key import remember_analysis
        w = self.window
        track = w.session.tracks['a']
        Path(track.path).write_bytes(b'source identity fixture')
        track.key_origin = 'manual'
        remember_analysis(track, observation(Path(track.path)))
        w.resize(1520, 968)
        w.set_advanced_mode(True, persist=False)
        w.refresh()
        w.show()
        self.app.processEvents()
        self.assertLess(w.transpose_knob.geometry().bottom(), w.timing_result.y())
        self.assertGreaterEqual(w.key_hint.height(), w.key_hint.heightForWidth(w.key_hint.width()))
        self.assertLess(w.transpose_toggle.geometry().bottom(), w.shift_hint.y())
        w.key.setFocus()
        QTest.keyClick(w.key, Qt.Key.Key_U)
        self.assertEqual((track.key, track.key_origin), ('', 'manual'))


if __name__ == "__main__":
    unittest.main()
