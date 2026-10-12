"""Focused timing-state UX, contextual help, and offline navigation checks."""
import os
import re
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl, Qt
from PySide6.QtWidgets import QApplication, QPushButton
from desktop_app import Studio
from studio_engine import Clip, Session, Track
from studio_help import HelpDialog, matching_topics
from studio_help_content import TOPICS
from studio_help_course import COURSE_IDS
from studio_timing import VERSION


class HelpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        with patch.object(Studio, "load_bands"):
            self.window = Studio(Path(self.folder.name))
        self.addCleanup(self.close_window)
        track = Track("song", "missing.wav", "Test song", 120, 120, cue_out=120)
        self.window.session = Session(tracks={"song": track}, clips=[Clip("song", 0, 120)])
        self.window.selected_id = self.window.session.clips[0].id
        self.window.refresh()

    def close_window(self):
        self.window.jobs.clear()
        if self.window.help_window:
            self.window.help_window.close()
        self.window.close()
        self.app.processEvents()

    def drift(self):
        return {"version": VERSION, "state": "drift", "coverage": .4,
                "map": [[0, 0], [120, 120]], "regions": [{"start": 16, "end": 64}]}

    def test_timing_results_explain_availability_without_claiming_perfection(self):
        w = self.window
        cases = [({}, "Timing not analyzed", False, "Analyze"),
                 ({"version": VERSION, "state": "steady"}, "No significant drift detected", False, "entire song"),
                 ({"version": VERSION, "state": "uncertain"}, "Timing inconclusive", False, "does not mean"),
                 ({"version": VERSION, "state": "drift"}, "Timing inconclusive", False, "reliable correction"),
                 (self.drift(), "Correction available", True, "not a confidence score")]
        for timing, title, enabled, explanation in cases:
            with self.subTest(state=title):
                w.session.tracks["song"].timing = timing
                w.refresh()
                self.assertEqual(w.timing_result.text(), title)
                self.assertEqual(w.stabilize.isEnabled(), enabled)
                self.assertIn(explanation, w.stabilize.toolTip())
                self.assertTrue(w.timing_hint.text())
        w.stabilize.click()
        self.assertTrue(w.selected_clip().stabilize)
        self.assertIn("On · covers 40%", w.timing_hint.text())
        w.stabilize.click()
        self.assertFalse(w.selected_clip().stabilize)

    def test_processing_and_cue_range_explain_disabled_actions(self):
        w = self.window
        w.session.tracks["song"].timing = self.drift()
        w.refresh()
        self.assertTrue(w.compare_timing_btn.isEnabled())
        w.selected_clip().cue_out = 10
        w.refresh()
        self.assertFalse(w.compare_timing_btn.isEnabled())
        self.assertIn("Widen Start/End", w.compare_timing_btn.toolTip())
        w.jobs["test"] = (None, False, True, False)
        w.refresh()
        self.assertEqual(w.timing_result.text(), "Processing in progress")
        self.assertFalse(w.stabilize.isEnabled())
        self.assertFalse(w.timing_btn.isEnabled())
        self.assertIn("Previous result: Correction available", w.timing_result.toolTip())

    def test_context_help_search_links_history_and_empty_results(self):
        w = self.window
        w.set_advanced_mode(True)
        w.show()
        w.bpm.setFocus()
        self.app.processEvents()
        w.show_help()
        guide = w.help_window
        self.assertEqual(guide.current_topic, "tempo")
        self.assertFalse(guide.isModal())
        guide.follow_link(QUrl("help:timing"))
        self.assertEqual(guide.current_topic, "timing")
        self.assertIn("No significant drift detected", guide.browser.toPlainText())
        guide.go(-1)
        self.assertEqual(guide.current_topic, "tempo")
        guide.go(1)
        self.assertEqual(guide.current_topic, "timing")
        guide.search.setText("AirPods")
        self.assertEqual(guide.current_topic, "troubleshooting")
        guide.search.setText("zz-no-match-zz")
        self.assertEqual(guide.topic_list.count(), 0)
        self.assertIn("No matching topics", guide.browser.toPlainText())
        w.show_help("keys")
        self.assertIs(w.help_window, guide)
        self.assertFalse(guide.search.text())
        self.assertEqual(guide.topic_list.count(), len(TOPICS))
        self.assertEqual(guide.current_topic, "keys")

    def test_all_offline_topics_and_links_are_reachable(self):
        guide = HelpDialog()
        self.addCleanup(guide.close)
        ids = {topic["id"] for topic in TOPICS}
        self.assertEqual(len(ids), len(TOPICS))
        for topic in TOPICS:
            guide.open_topic(topic["id"])
            text = guide.browser.toPlainText()
            self.assertIn(topic["title"], text)
            self.assertNotRegex(text, r"\{(?:MOD|ALT|REDO)\}")
            for target in re.findall(r"\]\(help:([^)]*)\)", topic["body"]):
                self.assertIn(target, ids)
        self.assertIn("timing", {t["id"] for t in matching_topics("significant drift")})

    def test_course_navigation_search_and_reference_return_preserve_mix(self):
        before = copy.deepcopy(self.window.session.to_dict())
        self.window.show_help('beginner-course')
        guide = self.window.help_window
        guide.follow_link(QUrl('help:' + COURSE_IDS[0]))
        self.assertFalse(guide.previous_lesson.isEnabled())
        for index, identity in enumerate(COURSE_IDS):
            self.assertEqual(guide.current_topic, identity)
            self.assertEqual(guide.lesson_position.text(), f'Lesson {index + 1} of {len(COURSE_IDS)}')
            self.assertFalse(guide.next_lesson.isHidden())
            if index + 1 < len(COURSE_IDS):
                guide.next_lesson.click()
        self.assertFalse(guide.next_lesson.isEnabled())
        guide.previous_lesson.click()
        self.assertEqual(guide.current_topic, COURSE_IDS[-2])
        guide.follow_link(QUrl('help:tempo'))
        self.assertTrue(guide.next_lesson.isHidden())
        guide.back_btn.click()
        self.assertEqual(guide.current_topic, COURSE_IDS[-2])
        self.assertFalse(guide.next_lesson.isHidden())
        guide.forward_btn.click()
        self.assertEqual(guide.current_topic, 'tempo')
        guide.search.setText('zz-no-course-match')
        self.assertTrue(guide.next_lesson.isHidden())
        guide.course_button.click()
        self.assertEqual(guide.current_topic, 'beginner-course')
        self.assertFalse(guide.search.text())
        self.assertEqual(guide.topic_list.count(), len(TOPICS))
        self.assertEqual(self.window.session.to_dict(), before)

    def test_course_small_window_keeps_navigation_and_reading_accessible(self):
        guide = HelpDialog(listen_examples=lambda: None)
        self.addCleanup(guide.close)
        guide.resize(740, 470)
        guide.show()
        for identity in ('beginner-course', 'learn-check-bpm', 'learn-final-check'):
            guide.open_topic(identity)
            self.app.processEvents()
            self.assertEqual(guide.size().width(), 740)
            self.assertEqual(guide.browser.horizontalScrollBar().maximum(), 0)
            for button in guide.findChildren(QPushButton):
                if not button.isHidden():
                    self.assertGreaterEqual(button.height(), 30)
                    position = button.mapTo(guide, button.rect().topLeft())
                    self.assertTrue(guide.rect().contains(position))
                    self.assertTrue(guide.rect().contains(button.mapTo(guide, button.rect().bottomRight())))
        self.assertTrue(guide.topic_list.wordWrap())
        self.assertEqual(guide.topic_list.horizontalScrollBar().maximum(), 0)

    def test_reference_history_and_empty_search_preserve_reading_position(self):
        guide = HelpDialog()
        self.addCleanup(guide.close)
        guide.resize(740, 470)
        guide.show()
        guide.search.setText('140 BPM')
        self.assertEqual(guide.current_topic, 'learn-check-bpm')
        self.app.processEvents()
        scroll = guide.browser.verticalScrollBar()
        lesson_position = scroll.maximum() // 2
        self.assertGreater(lesson_position, 0)
        scroll.setValue(lesson_position)
        guide.follow_link(QUrl('help:tempo'))
        self.app.processEvents()
        reference_position = scroll.maximum() // 3
        self.assertGreater(reference_position, 0)
        scroll.setValue(reference_position)
        guide.back_btn.click()
        self.app.processEvents()
        self.assertEqual(guide.current_topic, 'learn-check-bpm')
        self.assertEqual(scroll.value(), lesson_position)
        guide.forward_btn.click()
        self.app.processEvents()
        self.assertEqual(guide.current_topic, 'tempo')
        self.assertEqual(scroll.value(), reference_position)
        guide.search.setText('zz-no-match-zz')
        guide.search.clear()
        guide.open_topic('tempo')
        self.app.processEvents()
        self.assertEqual(scroll.value(), reference_position)


if __name__ == "__main__":
    unittest.main()
