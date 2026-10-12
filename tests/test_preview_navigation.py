"""Keyboard seeking reaches preview players without changing drafts or starting audio."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_app import Studio
from studio_beat_controls import GridAudition
from studio_blend_comparison import BlendComparison
from studio_grid_audio import GridMixer
from studio_order_dialog import OrderAudition
from studio_transition_audio import BlendMixer
from tests.test_transitions import project


class PreviewNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with patch.object(Studio, 'load_bands'):
            self.host = Studio(self.root)
        self.host.session = project()
        self.host.selected_id = self.host.session.clips[2].id
        self.host.refresh(); self.host.show()
        self.before = copy.deepcopy(self.host.session.to_dict())
        self.addCleanup(self.close_host)
        self.audio = np.zeros((12 * 48000, 2), dtype=np.float32)
        for name in ('outgoing', 'incoming', 'current', 'draft'):
            sf.write(self.root / f'{name}.wav', self.audio, 48000)
        self.report = {'folder': str(self.root), 'start': 28, 'end': 40, 'duration': 12}

    def close_host(self):
        self.host.close(); self.app.processEvents()

    def navigate(self, dialog, slider, player, update, *, media=False):
        dialog.show(); dialog.activateWindow(); slider.setFocus(); self.app.processEvents()
        self.assertTrue(slider.isEnabled())
        self.host.mix_cursor = 7.5
        target = 'setPosition' if media else 'seek'
        with patch.object(player, target, wraps=getattr(player, target)) as seek:
            for key, expected in ((Qt.Key.Key_Right, 1000), (Qt.Key.Key_PageUp, 6000),
                                  (Qt.Key.Key_Left, 5000), (Qt.Key.Key_PageDown, 0),
                                  (Qt.Key.Key_End, slider.maximum()), (Qt.Key.Key_Home, 0)):
                seek.reset_mock()
                QTest.keyClick(slider, key)
                seek.assert_called_once_with(expected)
                self.assertEqual(slider.value(), expected)
                if not media:
                    self.assertEqual(player.position(), expected)
                    self.assertFalse(player.playing)
            seek.reset_mock(); update(2500)
            self.assertEqual(slider.value(), 2500)
            seek.assert_not_called()
        self.assertEqual(self.host.mix_cursor, 7.5, 'Home in a preview must not rewind the main mix')
        self.assertEqual(self.host.session.to_dict(), self.before)

    def test_order_preview_keys_seek_the_paused_player(self):
        d = OrderAudition(self.host.session, 2, {}, self.report, 'system', 0, self.host)
        self.addCleanup(d.close); self.addCleanup(d.player.close)
        self.navigate(d, d.seek, d.player, d.position)

    def test_blend_editor_keys_seek_and_preserve_the_listening_mode(self):
        self.host.edit_blend(); d = self.host.transition_editor
        self.addCleanup(d.reject)
        length = d.seek.maximum()
        audio = np.zeros((round(length * 48), 2), dtype=np.float32)
        d.player.mixer = BlendMixer(audio, audio); d.ready = True
        d.player.set_loop(True); d.player.select('incoming')
        self.navigate(d, d.seek, d.player, d.position_changed)
        self.assertEqual(d.player.variant, 'incoming')

    def test_comparison_keys_seek_both_versions_and_switch_keeps_position(self):
        draft = copy.deepcopy(self.host.session); draft.clips[2].blend_bars = 4
        apply = Mock()
        d = BlendComparison(self.host.session, draft, 2, {}, self.report, 'system', 0, apply, self.host)
        self.addCleanup(d.close); self.addCleanup(d.player.close)
        self.navigate(d, d.seek, d.player, d.position_changed)
        d.player.seek(6000); d.choices.buttons()[1].setChecked(True)
        self.assertEqual(d.player.variant, 'draft'); self.assertEqual(d.player.position(), 6000)
        self.assertFalse(d.player.playing); apply.assert_not_called()

    def test_finishing_keys_reach_media_player_without_starting_playback(self):
        self.host.show_finishing(); d = self.host.finishing_dialog
        self.addCleanup(d.reject)
        # Exercise the ready-preview controls without depending on a host audio decoder.
        with QSignalBlocker(d.seek): d.seek.setRange(0, 12000)
        d.preview_path = str(self.root / 'current.wav'); d.update_controls()
        with patch.object(d.player, 'play') as play:
            self.navigate(d, d.seek, d.player, d.position_changed, media=True)
            play.assert_not_called()

    def test_reference_click_keys_seek_without_enabling_click_or_editing_grid(self):
        track = copy.deepcopy(self.host.session.tracks['0']); track.duration = 12
        d = GridAudition(track, self.host)
        self.addCleanup(d.close); self.addCleanup(d.close_audio)
        d.player.mixer = GridMixer(self.audio); d.ready = True
        self.navigate(d, d.seek, d.player, d.position_changed)
        self.assertFalse(d.click.isChecked()); self.assertEqual(d.grid, {})


if __name__ == '__main__': unittest.main()
