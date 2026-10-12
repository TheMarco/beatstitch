"""Overlap bounds, user gestures and undo, without decoding source songs."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import djmix
from desktop_app import Studio
from studio_engine import Clip, Session, Track, layout, overlap_limit
from studio_timing import VERSION


def project():
    tracks = {str(i): Track(str(i), f"{i}.wav", f"Song {i + 1}", 40, 120, "C minor", cue_out=40)
              for i in range(4)}
    return Session(target_bpm=120, tracks=tracks,
                   clips=[Clip(identity, 0, 20, blend_bars=2) for identity in tracks])


class OverlapLimitTests(unittest.TestCase):
    def test_bounds_agree_with_layout_including_both_neighbors(self):
        session = project()
        session.clips[1].blend_bars = 3
        session.clips[3].blend_bars = 4
        before = copy.deepcopy(session.to_dict())
        for bpm in (89.57, 120, 147.3):
            session.target_bpm = bpm
            for index in (1, 2, 3):
                limit = overlap_limit(session, index)
                for bars in range(1, 33):
                    candidate = copy.deepcopy(session)
                    candidate.clips[index].blend_bars = bars
                    if bars <= limit["bars"]:
                        layout(candidate)
                    else:
                        with self.assertRaises(djmix.MixError):
                            layout(candidate)
        session.target_bpm = before["target_bpm"]
        self.assertEqual(session.to_dict(), before)
        self.assertEqual(overlap_limit(session, 2)["bars"], 5)
        self.assertEqual(overlap_limit(session, 2)["song"], "Song 3")
        self.assertTrue(overlap_limit(session, 2)["neighbor"])

    def test_corrected_cues_and_exact_boundary_use_render_sample_lengths(self):
        session = project()
        session.clips[1].cue_out = 10
        self.assertEqual(overlap_limit(session, 1)["bars"], 2)  # 10 - next 4 = 6 sec; strict <.
        track = session.tracks["1"]
        track.timing = {"version": VERSION, "state": "drift", "map": [[0, 0], [10, 10.2], [40, 40]]}
        session.clips[1].stabilize = True
        self.assertEqual(overlap_limit(session, 1)["bars"], 3)
        session.clips[1].blend_bars = 3
        layout(session)


class TransitionControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(Studio, "load_bands"):
            self.w = Studio(Path(self.temp.name))
        self.addCleanup(self.w.close)
        self.w.session = project()
        self.w.selected_id = self.w.session.clips[2].id
        self.w.refresh()

    def test_stepper_limits_presets_named_undo_and_reset(self):
        w = self.w
        self.assertEqual(w.blend_bars.maximum(), 7)
        self.assertIn("maximum 7 bars", w.overlap_increase.accessibleName())
        self.assertEqual(w.overlap_duration.text(), "4.0 sec · max 7 bars")
        self.assertEqual([a.isEnabled() for a in w.overlap_presets.values()], [True, False, False, False])
        w.overlap_presets[4].click()
        self.assertEqual(w.selected_clip().blend_bars, 4)
        self.assertEqual(w.act_undo.text(), "Undo Change 2 → 3 overlap length")
        w.undo()
        self.assertEqual(w.selected_clip().blend_bars, 2)
        self.assertEqual(w.act_redo.text(), "Redo Change 2 → 3 overlap length")
        w.redo()
        w.blend_bars.commit(32)
        self.assertEqual(w.selected_clip().blend_bars, 7)
        self.assertFalse(w.overlap_increase.isEnabled())
        w.set_transition_value("in", "bass_swap", False)
        w.set_transition_value("out", "outro_percussive", True)
        w.selected_clip().semitones = 2
        w.selected_clip().transpose = True
        w.selected_clip().balance_levels = True
        w.selected_clip().transition_effect = 'filter'
        w.refresh()
        self.assertIn('Level balance on', w.overlap_duration.text())
        before = copy.deepcopy(w.session.to_dict())
        w.reset_transition()
        self.assertTrue(w.selected_clip().bass_swap)
        self.assertFalse(w.selected_clip().balance_levels)
        self.assertEqual(w.selected_clip().transition_effect, 'none')
        self.assertFalse(w.session.clips[1].outro_percussive)
        self.assertEqual(w.selected_clip().shift, 2)
        w.undo()
        self.assertEqual(w.session.to_dict(), before)
        with patch.object(w, "notify") as notify:
            w.set_transition_value("in", "blend_bars", 32)
            self.assertIn("1–7 bars", notify.call_args.args[0])
        self.assertEqual(w.session.to_dict(), before)

    def test_drag_is_one_undoable_change_and_escape_cancels(self):
        w = self.w
        w.show()
        self.app.processEvents()
        view = w.timeline
        view.scale = 7
        view.refresh()
        self.app.processEvents()
        edge = view.overlap_handle().center().toPoint()
        shift = QPoint(round(2 * 240 / w.session.target_bpm * view.scale), 0)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=edge)
        QTest.mouseMove(view.viewport(), edge - shift)
        self.assertEqual(w.selected_clip().blend_bars, 2)  # Only commits on release.
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=edge - shift)
        self.assertEqual(w.selected_clip().blend_bars, 4)
        self.assertEqual(len(w.history), 1)
        w.undo()
        self.assertEqual(w.selected_clip().blend_bars, 2)
        edge = view.overlap_handle().center().toPoint()
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=edge)
        QTest.mouseMove(view.viewport(), edge - shift)
        QTest.keyClick(view, Qt.Key.Key_Escape)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=edge - shift)
        self.assertEqual(w.selected_clip().blend_bars, 2)
        self.assertEqual(len(w.history), 0)


if __name__ == "__main__":
    unittest.main()
