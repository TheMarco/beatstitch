"""Simple/Advanced are views of one mix, with no hidden resets or audio work."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_app import Studio
from studio_engine import Clip, Session, Track
from studio_timing import VERSION


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(self.root); self.addCleanup(self.close)
        tracks = {str(i): Track(str(i), f'{i}.wav', f'Song {i}', 120, 120, 'C minor', cue_out=120)
                  for i in range(3)}
        self.w.session = Session(tracks=tracks, clips=[Clip(k, 0, 120) for k in tracks])
        self.w.selected_id = self.w.session.clips[1].id
        self.w.refresh(); self.w.show(); self.app.processEvents()

    def close(self):
        self.w.close(); self.app.processEvents()

    def test_default_keeps_waveforms_and_blend_controls_visible(self):
        w = self.w
        self.assertFalse(w.advanced_mode)
        self.assertTrue(w.simple_song.isVisible())
        self.assertFalse(w.advanced_sample.isVisible())
        self.assertFalse(w.advanced_clip.isVisible())
        self.assertFalse(w.advanced_transition_fields.isVisible())
        for control in (w.timeline, w.cue_editor, w.blend_bars, w.overlap_increase,
                        w.preview_btn, w.edit_blend_btn, w.review_btn):
            self.assertTrue(control.isVisible())
        self.assertIn('Source 120.0 BPM', w.simple_song.metadata.text())
        self.assertIn('Mix 128 BPM', w.simple_song.metadata.text())

    def test_switching_views_preserves_mix_history_cursor_and_prepared_audio(self):
        w = self.w; clip = w.selected_clip()
        clip.transpose = True; clip.semitones = 3; clip.stabilize = True; clip.intro_percussive = True
        w.session.tracks[clip.track_id].timing = {'version': VERSION, 'state': 'drift', 'coverage': .5,
                                                'map': [[0, 0], [120, 120]], 'regions': [{'start': 8, 'end': 90}]}
        w.refresh(); w.mix_cursor = 33.5; w.timeline.cursor = 33.5
        before = copy.deepcopy(w.session.to_dict()); selection = w.selected_id; revision = w.revision
        assets = {('transition', 1): {'path': 'already-prepared.wav'}}
        w.rendered_assets = copy.deepcopy(assets)
        with patch.object(w, 'job') as job:
            w.advanced_toggle.click()
            self.assertTrue(w.advanced_sample.isVisible()); self.assertTrue(w.act_advanced.isChecked())
            w.act_advanced.trigger()
            self.assertTrue(w.simple_song.isVisible()); self.assertFalse(w.advanced_toggle.isChecked())
            job.assert_not_called()
        self.assertEqual(w.session.to_dict(), before)
        self.assertEqual((w.mix_cursor, w.selected_id, w.revision), (33.5, selection, revision))
        self.assertEqual(w.rendered_assets, assets); self.assertEqual(w.history, [])
        self.assertIn('shifted +3', w.simple_song.pitch.text())
        self.assertIn('Tempo correction on', w.simple_song.timing.text())

    def test_pitch_bypass_restores_remembered_shift_and_undo(self):
        w = self.w; w.set_transpose(3); before = copy.deepcopy(w.session.to_dict())
        w.simple_song.pitch_toggle.click()
        self.assertEqual(w.selected_clip().shift, 0)
        self.assertEqual(w.selected_clip().semitones, 3)
        self.assertIn('Keeping the original key', w.simple_song.pitch.text())
        self.assertIn('Restore +3', w.simple_song.pitch_toggle.text())
        w.undo(); self.assertEqual(w.session.to_dict(), before)
        w.simple_song.pitch_toggle.click(); w.simple_song.pitch_toggle.click()
        self.assertEqual(w.selected_clip().shift, 3)

    def test_pitch_suggestion_is_explicit_and_visible_in_both_views(self):
        w = self.w; w.session.tracks['1'].key = 'C# minor'; w.refresh()
        self.assertTrue(w.simple_song.suggest_key.isVisible())
        shift = w.key_suggestion['semitones']
        before = copy.deepcopy(w.session.to_dict())
        w.simple_song.suggest_key.click()
        self.assertEqual(w.selected_clip().shift, shift)
        w.simple_song.pitch_toggle.click()
        self.assertTrue(w.simple_song.pitch_toggle.isVisible())
        self.assertFalse(w.simple_song.suggest_key.isVisible())
        w.simple_song.pitch_toggle.click()
        w.show_song_settings()
        self.assertTrue(w.advanced_sample.isVisible())
        self.assertEqual(w.transpose_knob.value(), shift)
        self.assertTrue(w.transpose_toggle.isChecked())
        w.undo(); w.undo(); w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_more_settings_and_shortcut_remember_view_without_dirtying_project(self):
        w = self.w; before = w.dirty
        w.simple_song.more.click()
        self.assertTrue(w.advanced_mode); self.assertTrue(w.advanced_sample.isVisible())
        self.assertEqual(w.dirty, before)
        self.assertTrue(json.loads((self.root / 'preferences.json').read_text())['advanced_controls'])
        QTest.keyClick(w, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.app.processEvents()
        self.assertFalse(w.advanced_mode)
        w.set_advanced_mode(True); w.close()
        self.w = Studio(self.root)
        self.w.show(); self.app.processEvents()
        self.assertTrue(self.w.advanced_mode)
        self.assertFalse(self.w.simple_song.isVisible())

    def test_overlap_edit_in_simple_view_keeps_limits_and_named_undo(self):
        w = self.w; before = copy.deepcopy(w.session.to_dict())
        w.overlap_decrease.click()
        self.assertEqual(w.selected_clip().blend_bars, 7)
        self.assertIn('overlap length', w.history[-1]['action'])
        w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_arrangement_keyboard_selects_and_seeks_without_editing_or_preparing_audio(self):
        w = self.w
        w.activateWindow(); w.timeline.setFocus(); self.app.processEvents()
        before = copy.deepcopy(w.session.to_dict())
        for control in (w.timeline, w.overview):
            interface = QAccessible.queryAccessibleInterface(control)
            self.assertEqual(interface.role(), QAccessible.Role.Grouping)
            self.assertTrue(interface.state().focusable)
        with patch.object(w, 'job') as job:
            QTest.keyClick(w.timeline, Qt.Key.Key_Down)
            self.assertEqual(w.selected_id, w.session.clips[2].id)
            self.assertIn('Song 3 of 3: Song 2', w.timeline.accessibleName())
            QTest.keyClick(w.timeline, Qt.Key.Key_Up)
            self.assertEqual(w.selected_id, w.session.clips[1].id)
            QTest.keyClick(w.timeline, Qt.Key.Key_Right)
            QTest.keyClick(w.timeline, Qt.Key.Key_Right)
            self.assertAlmostEqual(w.mix_cursor, 120 / 128)
            QTest.keyClick(w.timeline, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(w.mix_cursor, 0)
            QTest.keyClick(w.timeline, Qt.Key.Key_End)
            self.assertEqual(w.mix_cursor, w.timeline.duration())
            QTest.keyClick(w.timeline, Qt.Key.Key_Home)
            self.assertEqual(w.mix_cursor, 0)
            job.assert_not_called()
        self.assertEqual(w.session.to_dict(), before)
        self.assertEqual(w.history, [])
        QTest.keyClick(w.timeline, Qt.Key.Key_Tab)
        self.assertIsNot(self.app.focusWidget(), w.timeline)

    def test_overview_keys_navigate_and_home_overrides_global_transport_action(self):
        w = self.w
        w.activateWindow(); w.overview.setFocus(); self.app.processEvents()
        w.timeline.scale = 20; w.timeline.refresh()
        w.seek_timeline(60)
        QTest.keyClick(w.overview, Qt.Key.Key_Right)
        self.assertAlmostEqual(w.mix_cursor, 60 + 240 / 128)
        QTest.keyClick(w.overview, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(w.mix_cursor, 60 + 9 * 240 / 128)
        QTest.keyClick(w.overview, Qt.Key.Key_End)
        self.assertEqual(w.mix_cursor, w.timeline.duration())
        self.assertGreater(w.timeline.horizontalScrollBar().value(), 0)
        QTest.keyClick(w.overview, Qt.Key.Key_Home)
        self.assertEqual(w.mix_cursor, 0)
        self.assertEqual(w.timeline.horizontalScrollBar().value(), 0)
        self.assertIn('Position 0:00.0', w.overview.accessibleDescription())
        w.timeline.setEnabled(False)
        QTest.keyClick(w.overview, Qt.Key.Key_Right)
        self.assertEqual(w.mix_cursor, 0)

    def test_source_keyboard_cues_share_undo_and_rejected_edits_restore_marker(self):
        w = self.w
        w.activateWindow(); w.cue_editor.setFocus(); self.app.processEvents()
        before = copy.deepcopy(w.session.to_dict())
        QTest.keyClick(w.cue_editor, Qt.Key.Key_Right)
        self.assertEqual(w.selected_clip().cue_in, 2)
        self.assertEqual(w.history[-1]['action'], 'Change song cues')
        w.undo(); self.assertEqual(w.session.to_dict(), before)
        # Both 16-source-second overlaps just fit inside this 34-second slice.
        w.set_cues(86, 120)
        before = copy.deepcopy(w.session.to_dict()); history_size = len(w.history)
        QTest.keyClick(w.cue_editor, Qt.Key.Key_Right)
        self.assertEqual(w.session.to_dict(), before)
        self.assertEqual(len(w.history), history_size)
        self.assertEqual(w.cue_editor.cues(), (86, 120))
        self.assertIn('Start selected, 86.000 seconds', w.cue_editor.accessibleName())
        # A protected passage also blocks moving the incoming blend over it.
        w.set_cues(4, 120)
        w.selected_clip().protected_moments = [{'start': 20., 'end': 30., 'label': 'Vocal', 'kind': 'vocal'}]
        w.refresh()
        before = copy.deepcopy(w.session.to_dict()); history_size = len(w.history)
        QTest.keyClick(w.cue_editor, Qt.Key.Key_Right)
        self.assertEqual(w.session.to_dict(), before)
        self.assertEqual(len(w.history), history_size)
        self.assertEqual(w.cue_editor.cues(), (4, 120))
        self.assertIsNone(w.cue_editor.preview)
        # Delete must not remove a song when the source waveform has focus.
        QTest.keyClick(w.cue_editor, Qt.Key.Key_Delete)
        self.assertEqual(w.session.to_dict(), before)
        QTest.keyClick(w.cue_editor, Qt.Key.Key_Tab)
        self.assertIsNot(self.app.focusWidget(), w.cue_editor)


if __name__ == '__main__': unittest.main()
