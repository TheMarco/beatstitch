"""Focused checks for the dedicated two-song blend editor view."""
import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import djmix
from studio_engine import Clip, Session, Track, layout, preview_range
from studio_timing import VERSION as TIMING_VERSION, corrected_time
from studio_transition_view import TransitionView, project_to_source
from studio_waveform import BINS_PER_SECOND

APP = QApplication.instance() or QApplication([])


def session(stabilize=False):
    tracks = {
        "out": Track("out", "out.wav", "Outgoing Song With A Very Long Name For Elision", 20,
                     120, "C minor", cue_out=20, waveform=[0.1 * ((i % 7) + 1) for i in range(200)]),
        "in": Track("in", "in.wav", "Incoming Song", 20, 124, "D minor", cue_out=20,
                    waveform=[0.1 * ((i % 5) + 1) for i in range(200)]),
    }
    if stabilize:
        tracks["in"].timing = {"version": TIMING_VERSION, "state": "drift",
                               "map": [[0.0, 0.0], [5.0, 5.1], [15.0, 14.9], [20.0, 20.0]]}
    clips = [Clip("out", 0, 20, blend_bars=8), Clip("in", 0, 20, blend_bars=8, stabilize=stabilize)]
    return Session(target_bpm=128, tracks=tracks, clips=clips)


def bands_for(session):
    rng = np.random.default_rng(7)
    return {identity: (rng.random((int(t.duration * BINS_PER_SECOND), 3)) * 0.8 + 0.1).astype(np.float32)
            for identity, t in session.tracks.items()}


def loaded(session, bands="real"):
    view = TransitionView()
    start, end = (s / djmix.SR for s in preview_range(session, 1))
    view.set_data(session, 1, bands_for(session) if bands == "real" else {}, start, end)
    view.resize(860, 340)
    view.show()
    return view


class MappingTests(unittest.TestCase):
    def test_linear_endpoints_match_cues(self):
        s = session()
        positions = layout(s)
        for index, clip in enumerate(s.clips):
            start = positions[index]["start"] / djmix.SR
            end = positions[index]["end"] / djmix.SR
            self.assertAlmostEqual(project_to_source(s, index, start, positions), clip.cue_in, places=4)
            self.assertAlmostEqual(project_to_source(s, index, end, positions), clip.cue_out, places=3)
            track = s.tracks[clip.track_id]
            self.assertAlmostEqual(
                project_to_source(track, clip, s.target_bpm, start, (start + end) / 2),
                (clip.cue_in + clip.cue_out) / 2, places=4)

    def test_stabilized_mapping_inverts_timing_map(self):
        s = session(stabilize=True)
        positions = layout(s)
        start = positions[1]["start"] / djmix.SR
        track = s.tracks["in"]
        clip = s.clips[1]
        base = corrected_time(track, clip, clip.cue_in)
        for source in (2.0, 10.0, 17.0):
            project = start + (corrected_time(track, clip, source) - base) * track.bpm / s.target_bpm
            self.assertAlmostEqual(project_to_source(s, 1, project, positions), source, places=6)
        # Inside the warped region the answer differs from the naive linear map.
        project = start + (corrected_time(track, clip, 7.5) - base) * track.bpm / s.target_bpm
        linear = clip.cue_in + (project - start) * s.target_bpm / track.bpm
        self.assertNotAlmostEqual(project_to_source(s, 1, project), linear, places=3)


class WidgetTests(unittest.TestCase):
    def test_no_session_mutation(self):
        s = session()
        before = copy.deepcopy(s.to_dict())
        view = loaded(s)
        view.set_playhead(1.0)
        view.grab()
        QTest.keyClick(view, Qt.Key.Key_Right)
        self.assertEqual(s.to_dict(), before)

    def test_keyboard_seeks_by_beat_bar_and_ends(self):
        view = loaded(session())
        view.setFocus()
        got = []
        view.seekRequested.connect(got.append)
        view.set_playhead(5.0)
        beat = 60.0 / 128
        QTest.keyClick(view, Qt.Key.Key_Right)
        QTest.keyClick(view, Qt.Key.Key_Left)
        QTest.keyClick(view, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClick(view, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClick(view, Qt.Key.Key_Home)
        QTest.keyClick(view, Qt.Key.Key_End)
        self.assertEqual(len(got), 6)
        self.assertAlmostEqual(got[0], 5.0 + beat, places=6)
        self.assertAlmostEqual(got[1], 5.0 - beat, places=6)
        self.assertAlmostEqual(got[2], 5.0 + 4 * beat, places=6)
        self.assertAlmostEqual(got[3], 5.0 - 4 * beat, places=6)
        self.assertEqual(got[4], 0.0)
        self.assertAlmostEqual(got[5], view._end - view._start, places=6)

    def test_click_seeks_within_preview(self):
        view = loaded(session())
        got = []
        view.seekRequested.connect(got.append)
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=view.rect().center())
        self.assertEqual(len(got), 1)
        self.assertGreaterEqual(got[0], 0.0)
        self.assertLessEqual(got[0], view._end - view._start)

    def test_fallback_peaks_paint_without_bands(self):
        view = loaded(session(), bands="none")
        pixmap = view.grab()
        self.assertFalse(pixmap.isNull())
        self.assertTrue(hasattr(TransitionView, "seekRequested"))
        self.assertTrue(hasattr(TransitionView, "overlapChanged"))
        self.assertEqual(view.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        self.assertIn("Transition", view.accessibleName())
        self.assertGreaterEqual(view.minimumSizeHint().width(), 600)

    def test_stabilized_session_paints(self):
        pixmap = loaded(session(stabilize=True)).grab()
        self.assertFalse(pixmap.isNull())


if __name__ == "__main__":
    unittest.main()
