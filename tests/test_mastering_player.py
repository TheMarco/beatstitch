"""Player UI: checkbox maps B to matched/mastered; errors never look ready."""
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication

from prototype_mastering_audio import RATE, MasteringTransport
from prototype_mastering_player import TITLE, MasteringLab
from tests.test_mastering_audio import Device, Sink, write_folder


class MasteringPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = QApplication.instance()
        if app is not None and not isinstance(app, QApplication):
            # Earlier audio tests created a QCoreApplication; widgets need a
            # QApplication, so replace it (prior tests are already done).
            try:
                from shiboken6 import delete as _shiboken_delete
                _shiboken_delete(app)
            except Exception:
                pass
            app = None
        cls.app = app or QApplication([])

    def _window(self, folder):
        player = MasteringTransport(sink_factory=Sink)
        window = MasteringLab(folder, smoke_test=True, player=player)
        self.addCleanup(window.close)
        return window

    def test_live_comparison_does_not_recalculate_waveform_peaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'demo'
            write_folder(folder)
            window = self._window(folder)
            with patch.object(window.waveform, '_bucket_peaks', wraps=window.waveform._bucket_peaks) as scan:
                window.choose('B')
                window.match.setChecked(False)
                window.match.setChecked(True)
                window.choose('A')
                scan.assert_not_called()

    def test_checkbox_maps_current_mastered_matched(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "demo"
            write_folder(folder)
            window = self._window(folder)
            self.assertTrue(window.ready)
            self.assertEqual(window.windowTitle(), TITLE)
            self.assertEqual(window.a_button.text(), "A · Current mix")
            self.assertEqual(window.b_button.text(), "B · Mastered")
            self.assertTrue(window.match.isChecked())  # default ON
            window.choose("B")
            self.assertEqual(window.effective_variant, "matched")
            self.assertTrue(window.b_button.isChecked())
            window.match.setChecked(False)
            self.assertEqual(window.effective_variant, "mastered")
            window.choose("A")
            self.assertEqual(window.effective_variant, "current")
            self.assertEqual(window.passage.count(), 2)  # whole mix + Chorus
            window.passage.setCurrentIndex(1)
            self.assertEqual(window.player.passage_name.startswith("Chorus"), True)

    def test_checkbox_switches_live_without_sink_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "demo"
            write_folder(folder, n=48000)
            window = self._window(folder)
            window.player.set_device(Device())
            window.choose("B")
            window.player.play()
            self.assertTrue(window.is_playing())
            starts, resets = window.player.start_count, window.player.reset_count
            stream = window.player.stream
            window.match.setChecked(False)
            self.assertEqual(window.effective_variant, "mastered")
            window.match.setChecked(True)
            self.assertEqual(window.effective_variant, "matched")
            window.choose("A")
            window.choose("B")
            self.assertEqual((window.player.start_count, window.player.reset_count), (starts, resets))
            self.assertIs(window.player.stream, stream)
            window.player.close()

    def test_invalid_folder_shows_error_and_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "empty"
            folder.mkdir()
            window = self._window(folder)
            self.assertFalse(window.ready)
            self.assertIn("Could not load", window.status.text())
            self.assertFalse(window.play.isEnabled())
            self.assertFalse(window.a_button.isEnabled())

    def test_missing_report_field_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "bad"
            write_folder(folder)
            raw = json.loads((folder / "report.json").read_text())
            del raw["matched_gain_db"]
            (folder / "report.json").write_text(json.dumps(raw))
            window = self._window(folder)
            self.assertFalse(window.ready)
            self.assertIn("Could not load", window.status.text())


if __name__ == "__main__":
    unittest.main()
