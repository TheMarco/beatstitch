"""Listening-mode selections must work for native accessibility, not only clicks."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication

from prototype_drum_loop import LoopTrial
from studio_beat_grid import grid_settings
from studio_engine import Session, Track


class LoopTrialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_accessible_selection_changes_audio_mode_and_edits_invalidate_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'source.wav'
            audio = np.ones((48000, 2), dtype=np.float32) * .1
            for name in ('source', 'current', 'draft'): sf.write(root / f'{name}.wav', audio, 48000)
            track = Track('a', str(source), 'Example', 48., bpm=120.)
            track.beat_grid = grid_settings(track, 120., 0.)
            window = LoopTrial(Session(tracks={'a': track}), root / 'audio-cache')
            try:
                report = {'folder': str(root), 'duration': 1., 'source_frames': 24000,
                          'wave': [.1] * 10, 'source_wave': [.1] * 10, 'join_times': [.5]}
                window.report = report; window.wave.report = report
                window.player.load(root); window.update_controls()
                with patch.object(window.source_player, 'play'), patch.object(window.player, 'play'):
                    # macOS accessibility changes the checked state without a
                    # QPushButton.clicked signal. The audio must follow it.
                    window.original.setChecked(True)
                    self.assertTrue(window.source_mode)
                    self.assertTrue(window.wave.source)
                    self.assertEqual(window.seek.maximum(), 500)
                    window.raw.setChecked(True)
                    self.assertFalse(window.source_mode)
                    self.assertEqual(window.player.variant, 'current')
                    self.assertEqual(window.seek.maximum(), 1000)
                    window.smooth.setChecked(True)
                    self.assertEqual(window.player.variant, 'draft')
                window.repeats.setValue(3)
                self.assertIsNone(window.report)
                self.assertIsNone(window.player.mixer)
                self.assertFalse(window.original.isEnabled())
                self.assertFalse(window.raw.isEnabled())
            finally:
                window.close(); self.app.processEvents()


if __name__ == '__main__': unittest.main()
