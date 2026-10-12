"""Keyboard seeking in the real timing comparison stays on its shared clock."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from studio_ab_audio import ABMixer, ABTransport, RATE
from studio_audition import TimingComparison


class TimingComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        audio = np.zeros((12 * RATE, 2), dtype=np.float32)
        def load(player, folder):
            player.mixer = ABMixer(audio, audio, audio)
        report = {'name': 'Timing check', 'source_start': 0, 'target_bpm': 128,
                  'shift': 0, 'duration': 12, 'folder': '.'}
        with patch.object(ABTransport, 'load', load), patch.object(TimingComparison, 'refresh_output'):
            self.dialog = TimingComparison(report, 'system', .5)
        self.addCleanup(self.dialog.close)

    def test_keyboard_positions_both_variants_without_starting_playback(self):
        d = self.dialog
        for key, expected in [(Qt.Key.Key_Right, 1000), (Qt.Key.Key_PageUp, 6000),
                              (Qt.Key.Key_End, 12000), (Qt.Key.Key_Home, 0)]:
            QTest.keyClick(d.seek, key)
            self.assertEqual(d.player.position(), expected)
            self.assertFalse(d.player.playing)
        d.player.select('straightened')
        QTest.keyClick(d.seek, Qt.Key.Key_Right)
        self.assertEqual(d.player.position(), 1000)
        self.assertEqual(d.player.mixer.target_mix, 1.)

    def test_playback_position_updates_do_not_seek_back_into_transport(self):
        d = self.dialog
        with patch.object(d.player, 'seek', wraps=d.player.seek) as seek:
            d.position_changed(3000)
            self.assertEqual(d.seek.value(), 3000)
            self.assertEqual(d.time.text(), '0:03')
            seek.assert_not_called()


if __name__ == '__main__':
    unittest.main()
