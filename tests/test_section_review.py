"""Listening-review isolation, range validation and explicit human feedback."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PySide6.QtWidgets import QApplication

from prototype_section_review import SectionReview, review_regions
from studio_cache import source_signature


class SectionReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_ranges_merge_only_touching_equal_labels_and_reject_bad_predictions(self):
        sections = [dict(label='buildup', start=0, end=10), dict(label='drop', start=10, end=20),
                    dict(label='drop', start=20, end=30), dict(label='drop', start=31, end=40.05)]
        self.assertEqual(review_regions(sections, 40), [dict(label='buildup', start=0, end=10),
                         dict(label='drop', start=10, end=30), dict(label='drop', start=31, end=40)])
        for changes in ({'start': float('nan')}, {'end': 80}, {'label': 'chorus'}, {'start': -1}, {'end': 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                review_regions([{**sections[0], **changes}], 40)
        with self.assertRaises(ValueError): review_regions(sections + [dict(label='drop', start=5, end=8)], 40)

    def window(self, root):
        source = root / 'song.wav'; source.write_bytes(b'review-source')
        report = root / 'sections.json'
        report.write_text(json.dumps({'source': source_signature(source), 'sections': [
            dict(label='buildup', start=12, end=24), dict(label='drop', start=24, end=40)]}))
        probe = patch('prototype_section_review.djmix.probe', return_value={'duration': 40})
        devices = patch('prototype_section_review.QMediaDevices')
        probe.start(); self.addCleanup(probe.stop)
        outputs = devices.start(); self.addCleanup(devices.stop)
        outputs.defaultAudioOutput.return_value.isNull.return_value = False
        refresh = patch.object(SectionReview, 'refresh_output'); refresh.start(); self.addCleanup(refresh.stop)
        w = SectionReview(report, bands=np.zeros((2000, 3), np.float32)); self.addCleanup(w.close)
        return w

    def test_selection_audition_and_ratings_never_change_predictions(self):
        with tempfile.TemporaryDirectory() as temp:
            w = self.window(Path(temp)); original = w.report_file.read_bytes()
            with patch.object(w.audition, 'listen_at') as listen:
                w.table.selectRow(1); self.app.processEvents()
                listen.assert_not_called(); self.assertFalse(w.feedback_file.exists())
                w.begin.click(); listen.assert_called_once_with(20)
                w.end.click(); self.assertEqual(listen.call_args.args, (36,))
            w.rating_buttons[0].click()
            saved = json.loads(w.feedback_file.read_text())
            self.assertEqual(saved['ratings'], {'1': 'yes'})
            self.assertEqual(w.report_file.read_bytes(), original)
            again = SectionReview(w.report_file, bands=np.zeros((2000, 3), np.float32))
            self.addCleanup(again.close); self.assertEqual(again.feedback, {'1': 'yes'}); again.close()
            with patch.object(w.audition, 'stop') as stop:
                w.reject(); stop.assert_called_once()

    def test_changed_source_and_failed_save_cannot_create_false_feedback(self):
        with tempfile.TemporaryDirectory() as temp:
            w = self.window(Path(temp))
            with patch.object(Path, 'write_text', side_effect=OSError('Disk full')):
                w.rate('yes')
            self.assertEqual(w.feedback, {}); self.assertFalse(w.feedback_file.exists())
            self.assertIn('Could not save', w.status.text())
            w.source.unlink(); w.rate('yes')
            self.assertEqual(w.feedback, {}); self.assertIn('source has changed', w.status.text())
            w.close()


if __name__ == '__main__': unittest.main()
