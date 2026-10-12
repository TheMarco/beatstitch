"""Cue suggestions stay drafts; computed analysis does not invalidate audio."""
import copy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from desktop_app import Studio
from studio_cache import source_signature
from studio_structure import VERSION, MODEL_SHA256
from studio_beat_tempo import VERSION as TEMPO_VERSION
from tests.test_transitions import project


class CueReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(root)
        self.w.session = project()
        for track in self.w.session.tracks.values():
            path = root / (track.id + '.wav')
            path.write_bytes(b'fixture')
            track.path = str(path)
        self.track = self.w.session.tracks['1']
        self.track.structure = {'version': VERSION, 'source': source_signature(self.track.path), 'source_bpm': 120,
                                'bars': [{'start': 2., 'end': 4., 'number': 0}], 'coverage': .5,
                                'downbeats': [2., 4.1, 6.], 'beats': [2., 2.5, 3., 3.5, 4.1],
                                'cues': [{'kind': 'start', 'time': 2., 'label': 'Bar start', 'reason': 'Listen first.'}]}
        self.w.selected_id = self.w.session.clips[1].id
        self.w.refresh()
        self.w.review_song_cues()
        self.dialog = self.w.cue_review
        self.addCleanup(self.close)

    def close(self):
        if self.w.cue_review is not None:
            self.w.cue_review.busy = False
            self.w.cue_review.reject()
        self.w.close()

    def test_try_validate_apply_undo_and_discard(self):
        before = copy.deepcopy(self.w.session.to_dict())
        self.assertFalse(self.dialog.auto_analyze.isActive())
        self.dialog.try_cue(self.track.structure['cues'][0])
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertTrue(self.dialog.apply.isEnabled())
        self.dialog.change_cues(19, 20)
        self.assertEqual(self.dialog.clip.cue_in, 2)
        self.assertIn('incoming and outgoing overlaps', self.dialog.status.text())
        self.dialog.accept_cues()
        self.assertEqual(self.w.session.clips[1].cue_in, 2)
        self.assertEqual(len(self.w.history), 1)
        self.w.undo()
        self.assertEqual(self.w.session.to_dict(), before)
        self.w.review_song_cues()
        self.w.cue_review.change_cues(4, 20)
        self.w.cue_review.reject()
        self.assertEqual(self.w.session.to_dict(), before)

    def set_tempo_evidence(self, bpm):
        for track in (self.track, self.dialog.track):
            track.structure['model_sha256'] = MODEL_SHA256
            track.structure['tempo_estimate'] = {'version': TEMPO_VERSION, 'duration': track.duration,
                                                  'bpm': bpm, 'coverage': .8}
        self.dialog.show_analysis()
        self.dialog.refresh_draft()

    def test_beat_tempo_disagreement_try_apply_and_undo_preserve_user_control(self):
        self.set_tempo_evidence(126.)
        before = copy.deepcopy(self.w.session.to_dict())
        d = self.dialog; panel = d.beat_controls
        self.assertIn('estimates disagree', panel.tempo_summary.text())
        self.assertIn('Tempo estimates differ', d.evidence.text())
        self.assertTrue(panel.try_estimate.isEnabled())
        panel.try_estimate.click()
        self.assertEqual(d.track.bpm, 126.)
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertFalse(panel.try_estimate.isEnabled())
        self.assertTrue(d.apply.isEnabled())
        d.accept_cues()
        self.assertEqual(self.track.bpm, 126.)
        self.assertEqual(len(self.w.history), 1)
        self.w.undo()
        self.assertEqual(self.w.session.to_dict(), before)

    def test_half_tempo_is_an_explicit_choice_and_discard_does_not_change_mix(self):
        self.set_tempo_evidence(60.)
        before = copy.deepcopy(self.w.session.to_dict())
        panel = self.dialog.beat_controls
        self.assertIn('half/double', panel.tempo_summary.text())
        self.assertIn('Either estimate may be wrong', panel.tempo_summary.text())
        panel.try_estimate.click()
        self.assertEqual(self.dialog.track.bpm, 60.)
        self.dialog.reject()
        self.assertEqual(self.w.session.to_dict(), before)

    def set_opening_evidence(self, bpm):
        for track in (self.track, self.dialog.track):
            track.opening_tempo = {'version': 1, 'source': source_signature(track.path),
                                   'duration': track.duration, 'bpm': bpm}
        self.dialog.show_analysis()
        self.dialog.refresh_draft()

    def test_imported_beat_default_keeps_opening_alternative_visible_and_undoable(self):
        self.set_tempo_evidence(120.)
        self.set_opening_evidence(240.)
        before = copy.deepcopy(self.w.session.to_dict())
        d = self.dialog; panel = d.beat_controls
        self.assertIn('half/double', panel.tempo_summary.text())
        self.assertIn('Opening-passage estimate: 240.00', panel.tempo_summary.text())
        self.assertIn('Tempo estimates differ', d.evidence.text())
        self.assertFalse(panel.try_estimate.isEnabled())
        self.assertTrue(panel.try_opening.isEnabled())
        panel.try_opening.click()
        self.assertEqual(d.track.bpm, 240.)
        self.assertEqual(self.w.session.to_dict(), before)
        panel.try_estimate.click()
        self.assertEqual(d.track.bpm, 120.)
        panel.try_opening.click()
        d.accept_cues()
        self.assertEqual(self.track.bpm, 240.)
        self.assertEqual(len(self.w.history), 1)
        self.w.undo()
        self.assertEqual(self.w.session.to_dict(), before)
        self.w.review_song_cues()
        self.w.cue_review.beat_controls.try_opening.click()
        self.w.cue_review.reject()
        self.assertEqual(self.w.session.to_dict(), before)

    def test_stale_opening_alternative_cannot_replace_draft_bpm(self):
        self.set_opening_evidence(126.)
        before = copy.deepcopy(self.w.session.to_dict())
        Path(self.track.path).write_bytes(b'changed source')
        self.dialog.beat_controls.try_opening.click()
        self.assertEqual(self.dialog.track.bpm, 120.)
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertIn('no longer current', self.dialog.status.text())
        self.assertTrue(self.dialog.beat_controls.try_opening.isHidden())

    def test_stale_beat_tempo_cannot_be_tried_from_an_old_button(self):
        self.set_tempo_evidence(126.)
        before = copy.deepcopy(self.w.session.to_dict())
        Path(self.track.path).write_bytes(b'changed source')
        self.dialog.beat_controls.try_estimate.click()
        self.assertEqual(self.dialog.track.bpm, 120.)
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertIn('no longer current', self.dialog.status.text())
        self.assertTrue(self.dialog.beat_controls.try_estimate.isHidden())

    def test_minimum_dialog_size_scrolls_beat_controls_without_clipping_explanations(self):
        import studio_theme as theme
        old_font, old_palette, old_style = self.app.font(), self.app.palette(), self.app.styleSheet()
        self.addCleanup(self.app.setStyleSheet, old_style)
        self.addCleanup(self.app.setPalette, old_palette)
        self.addCleanup(self.app.setFont, old_font)
        self.app.setFont(theme.font(theme.SIZE_BODY))
        self.app.setPalette(theme.palette())
        self.app.setStyleSheet(theme.stylesheet())
        self.set_tempo_evidence(60.)
        self.set_opening_evidence(240.)
        d = self.dialog; panel = d.beat_controls
        d.tabs.setCurrentWidget(panel); d.resize(820, 650); d.show(); self.app.processEvents()
        self.assertEqual((d.width(), d.height()), (820, 650))
        self.assertGreater(panel.verticalScrollBar().maximum(), 0)
        for label in panel.findChildren(QLabel):
            if label.wordWrap():
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()), label.text())
        self.assertGreaterEqual(panel.bpm.height(), panel.bpm.sizeHint().height())
        panel.bpm.setFocus(Qt.FocusReason.TabFocusReason)
        self.app.processEvents()
        for _ in range(12):
            if panel.mark.hasFocus():
                break
            QTest.keyClick(self.app.focusWidget(), Qt.Key.Key_Tab)
            self.app.processEvents()
        self.assertTrue(panel.mark.hasFocus(), 'Keyboard users must reach the lower beat controls.')
        from PySide6.QtCore import QPoint, QRect
        rect = QRect(panel.mark.mapTo(panel.viewport(), QPoint(0, 0)), panel.mark.size())
        self.assertTrue(panel.viewport().rect().contains(rect))

    def test_protected_passage_and_fitted_blends_apply_as_one_undo(self):
        before = copy.deepcopy(self.w.session.to_dict()); panel = self.dialog.protection
        panel.start.setValue(2); panel.end.setValue(18); panel.name.setText('Favorite vocal')
        panel.kind.setCurrentIndex(panel.kind.findData('vocal')); panel.add.click()
        self.assertFalse(self.dialog.apply.isEnabled())
        self.assertIn('covers protected', self.dialog.status.text())
        self.assertEqual(self.w.session.to_dict(), before)
        panel.fit.click()
        self.assertTrue(self.dialog.apply.isEnabled())
        self.assertIn('2 → 1 bars', self.dialog.status.text())
        self.dialog.accept_cues()
        self.assertEqual([c.blend_bars for c in self.w.session.clips], [2, 1, 1, 2])
        self.assertEqual(self.w.selected_clip().protected_moments[0]['label'], 'Favorite vocal')
        self.assertEqual(len(self.w.history), 1)
        self.assertIn('protected passages', self.w.history[-1]['action'])
        protected = copy.deepcopy(self.w.session.to_dict())
        self.w.set_cues(3, 20)
        self.assertEqual(self.w.session.to_dict(), protected)
        self.w.set_transition_value('in', 'blend_bars', 2)
        self.assertEqual(self.w.session.to_dict(), protected)
        self.w.undo(); self.assertEqual(self.w.session.to_dict(), before)

    def test_manual_protection_entry_skips_analysis_and_cancel_keeps_mix(self):
        self.dialog.reject(); self.track.structure = {}
        before = copy.deepcopy(self.w.session.to_dict())
        self.w.review_song_cues(protect=True); self.dialog = self.w.cue_review
        self.assertFalse(self.dialog.auto_analyze.isActive())
        panel = self.dialog.protection
        self.assertIs(self.dialog.tabs.currentWidget(), panel)
        panel.start.setValue(0); panel.end.setValue(12); panel.add.click(); panel.fit.click()
        self.assertIn('no room for even one bar', self.dialog.status.text())
        self.assertFalse(self.dialog.apply.isEnabled())
        self.dialog.reject(); self.assertEqual(self.w.session.to_dict(), before)

    def vocal_content(self, start=2., end=12.):
        from studio_content import VERSION as CONTENT_VERSION, MODEL_SHA256
        scores = {'voice': .8, 'drums': .1, 'pitched': .3, 'music': .8}
        return {'version': CONTENT_VERSION, 'model_sha256': MODEL_SHA256,
                'source': source_signature(self.track.path), 'duration': self.track.duration,
                'windows': [{'start': start, 'end': end, 'scores': scores, 'rms_db': -18., 'state': 'voice'}]}

    def test_possible_vocal_is_reviewed_as_a_range_then_protected_with_one_undo(self):
        content = self.vocal_content()
        self.track.content = copy.deepcopy(content); self.dialog.track.content = content
        before = copy.deepcopy(self.w.session.to_dict()); d = self.dialog; panel = d.protection
        d.refresh_draft()
        self.assertIn('1 possible vocal', panel.vocals.summary.text())
        button = panel.vocals.findChild(QPushButton, 'useVoiceRange')
        d.wave.cursor = 17.
        self.assertIsNotNone(button); button.click()
        self.assertEqual((panel.start.value(), panel.end.value(), panel.kind.currentData()), (2., 12., 'vocal'))
        self.assertEqual(d.wave.cursor, 17.)
        self.assertFalse(d.clip.protected_moments); self.assertFalse(d.apply.isEnabled())
        self.assertEqual(self.w.session.to_dict(), before)
        panel.add.click()
        self.assertFalse(d.apply.isEnabled()); self.assertIn('covers protected', d.status.text())
        panel.fit.click(); self.assertTrue(d.apply.isEnabled()); d.accept_cues()
        self.assertEqual(self.w.selected_clip().protected_moments[0]['kind'], 'vocal')
        self.assertEqual(self.w.session.clips[1].blend_bars, 1)
        self.assertEqual(len(self.w.history), 1)
        self.w.undo(); self.assertEqual(self.w.session.to_dict(), before)

    def test_vocal_audition_leaves_mix_unchanged_and_stale_range_is_rejected(self):
        from studio_content import voice_regions
        d = self.dialog; panel = d.protection
        d.track.content = self.vocal_content(); d.output_available = True; panel.refresh()
        before = copy.deepcopy(self.w.session.to_dict())
        d.audition.listen_at = Mock()
        listen = panel.vocals.findChild(QPushButton, 'listenVoice'); listen.click()
        d.audition.listen_at.assert_called_once_with(2.)
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertFalse(d.clip.protected_moments)
        candidate = voice_regions(d.track.content)[0]
        fields = panel.start.value(), panel.end.value(), panel.kind.currentData()
        Path(self.track.path).write_bytes(b'replacement song')
        panel.use_voice_range(candidate)
        self.assertIn('no longer current', d.status.text())
        self.assertEqual((panel.start.value(), panel.end.value(), panel.kind.currentData()), fields)
        panel.refresh()
        self.assertIn('not checked yet', panel.vocals.summary.text())
        # Detached old controls must not remain interactive until deferred deletion.
        self.assertIsNone(panel.vocals.findChild(QPushButton, 'useVoiceRange'))

    def test_vocal_range_outside_kept_audio_requires_explicit_review(self):
        d = self.dialog; panel = d.protection
        d.track.content = self.vocal_content(15., 25.); panel.refresh()
        self.assertTrue(any('outside Start/End' in label.text() for label in panel.vocals.findChildren(QLabel)))
        panel.vocals.findChild(QPushButton, 'useVoiceRange').click()
        self.assertEqual((panel.start.value(), panel.end.value()), (15., 25.))
        self.assertEqual(d.clip.cue_out, 20.)
        panel.add.click()
        self.assertFalse(d.apply.isEnabled())
        self.assertIn('Keep the whole protected passage', d.status.text())
        panel.remove(0)
        result = copy.deepcopy(d.track.content)
        result['windows'][0]['scores']['voice'] = .01; result['windows'][0]['state'] = 'uncertain'
        d.track.content = result; panel.refresh()
        self.assertIn('does not prove it is vocal-free', panel.vocals.summary.text())
        self.assertIsNone(panel.vocals.findChild(QPushButton, 'useVoiceRange'))

    def test_passage_analysis_updates_both_panels_without_audio_edits(self):
        d = self.dialog
        before = copy.deepcopy(self.w.session.clips)
        revision = self.w.revision, self.w.audio_revision
        d.start_job = Mock(return_value=threading.Event())
        d.analyze_passages()
        d.start_job.call_args.args[1](self.vocal_content())
        self.assertIn('1 possible vocal passage', d.protection.vocals.summary.text())
        self.assertIn('No consistent drum-led passage', d.passages.summary.text())
        self.assertTrue(self.track.content)
        self.assertEqual(self.w.session.clips, before)
        self.assertEqual((self.w.revision, self.w.audio_revision), revision)
        self.assertFalse(d.apply.isEnabled())

    def test_cancel_prevents_late_passage_publication(self):
        d = self.dialog; before = copy.deepcopy(self.track.content)
        cancel = threading.Event(); result = self.vocal_content()
        d.start_job = Mock(return_value=cancel)
        d.analyze_passages(); d.reject()
        self.assertTrue(cancel.is_set())
        d.start_job.call_args.args[1](result)
        self.assertEqual(self.track.content, before)

    def section_result(self):
        from studio_song_sections import VERSION, MODEL_ID
        return {'version': VERSION, 'model_id': MODEL_ID, 'duration': self.track.duration,
                'source': source_signature(self.track.path), 'device': 'cpu',
                'regions': [dict(label='buildup', start=2., end=8.), dict(label='drop', start=8., end=12.)]}

    def test_sections_are_annotations_and_protection_is_explicit_apply_with_undo(self):
        d = self.dialog; revision = self.w.revision, self.w.audio_revision
        clips = copy.deepcopy(self.w.session.clips)
        d.start_job = Mock(return_value=threading.Event()); d.analyze_sections()
        d.start_job.call_args.args[1](self.section_result())
        self.assertTrue(self.track.song_sections)
        self.assertEqual((self.w.revision, self.w.audio_revision), revision)
        self.assertEqual(self.w.session.clips, clips); self.assertFalse(d.apply.isEnabled())
        before = copy.deepcopy(self.w.session.to_dict())
        original_cell = d.sections.buttons[0]
        d.output_available = True; d.sections.refresh(); d.audition.listen_at = Mock()
        self.assertIs(d.sections.buttons[0], original_cell)
        d.sections.select_row(1)
        d.audition.listen_at.assert_not_called()
        d.sections.begin.click(); d.audition.listen_at.assert_called_once_with(6.)
        d.wave.cursor = 17.; d.sections.protect.click()
        self.assertEqual(d.wave.cursor, 17.)
        panel = d.protection
        self.assertEqual((panel.start.value(), panel.end.value(), panel.kind.currentData()), (8., 12., 'drop'))
        self.assertFalse(d.clip.protected_moments); self.assertEqual(self.w.session.to_dict(), before)
        panel.add.click(); self.assertTrue(d.apply.isEnabled()); d.accept_cues()
        self.assertEqual(self.w.selected_clip().protected_moments[0]['kind'], 'drop')
        self.w.undo(); self.assertEqual(self.w.session.to_dict(), before)

    def test_sections_cancel_and_stale_observations_cannot_publish_or_protect(self):
        d = self.dialog; cancel = threading.Event()
        d.start_job = Mock(return_value=cancel); d.analyze_sections(); d.reject()
        self.assertTrue(cancel.is_set()); d.start_job.call_args.args[1](self.section_result())
        self.assertFalse(self.track.song_sections)
        self.w.review_song_cues(); self.dialog = d = self.w.cue_review
        result = self.section_result(); d.track.song_sections = result; d.sections.refresh()
        d.start_job = Mock(); d.analyze_sections(); d.start_job.assert_not_called()
        self.assertIn('Reusing', d.status.text())
        Path(self.track.path).write_bytes(b'new source')
        previous = d.protection.start.value(), d.protection.end.value()
        d.sections.protect.click()
        self.assertEqual((d.protection.start.value(), d.protection.end.value()), previous)
        self.assertIn('no longer current', d.status.text())
        self.assertEqual(len(d.sections.buttons), 0)

    def test_section_cue_buttons_survive_output_refresh_and_are_removed_safely(self):
        from studio_beat_grid import grid_settings
        d = self.dialog; d.track.beat_grid = grid_settings(d.track, 120, 0)
        result = self.section_result()
        result['regions'] = [dict(label='intro', start=0, end=8), dict(label='drop', start=8, end=22),
                             dict(label='outro', start=22, end=d.track.duration)]
        d.track.song_sections = result; d.output_available = False; d.sections.refresh()
        button = d.sections.findChild(QPushButton, 'trySectionCue')
        self.assertIsNotNone(button)
        d.output_available = True; d.sections.refresh()
        self.assertIs(d.sections.findChild(QPushButton, 'trySectionCue'), button)
        d.track.song_sections = {}; d.sections.refresh()
        self.assertIsNone(d.sections.findChild(QPushButton, 'trySectionCue'))

    def test_analysis_does_not_change_cues_bpm_or_audio_revision(self):
        before = copy.deepcopy(self.w.session.clips)
        revision, audio_revision = self.w.revision, self.w.audio_revision
        self.dialog.start_job = Mock(return_value=threading.Event())
        self.dialog.analyze()
        result = copy.deepcopy(self.track.structure)
        result['coverage'] = .75
        self.dialog.start_job.call_args.args[1](result)
        self.assertEqual(self.track.structure['coverage'], .75)
        self.assertEqual(self.track.bpm, 120)
        self.assertEqual(self.w.session.clips, before)
        self.assertEqual((self.w.revision, self.w.audio_revision), (revision, audio_revision))

    def test_cancel_prevents_late_analysis_publication(self):
        before = copy.deepcopy(self.track.structure)
        cancel = threading.Event()
        self.dialog.start_job = Mock(return_value=cancel)
        self.dialog.analyze()
        self.dialog.reject()
        self.assertTrue(cancel.is_set())
        result = copy.deepcopy(before)
        result['coverage'] = .8
        self.dialog.start_job.call_args.args[1](result)
        self.assertEqual(self.track.structure, before)

    def test_detected_snap_unsupported_gaps_and_keyboard_seek(self):
        wave = self.dialog.wave
        self.assertAlmostEqual(wave.snap(4, Qt.KeyboardModifier.NoModifier), 4.1)
        self.assertEqual(wave.snap(12, Qt.KeyboardModifier.NoModifier), 12)
        self.assertEqual(wave.snap(4, Qt.KeyboardModifier.AltModifier), 4)
        self.dialog.audition.seek_to = Mock()
        wave.cursor = 3
        QTest.keyClick(wave, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.dialog.audition.seek_to.assert_called_once_with(5000)

    def test_review_keyboard_keeps_seeking_and_fine_cue_edits_distinct(self):
        wave = self.dialog.wave
        before = copy.deepcopy(self.w.session.to_dict())
        self.dialog.audition.seek_to = Mock()
        QTest.keyClick(wave, Qt.Key.Key_Up)
        QTest.keyClick(wave, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
        self.dialog.audition.seek_to.assert_not_called()
        self.assertAlmostEqual(self.dialog.clip.cue_in, .01)
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertIn('Song waveform and draft cues', wave.accessibleName())
        self.assertIn('Left/Right seeks', wave.accessibleDescription())
        self.assertIn('Apply changes', wave.accessibleDescription())
        QTest.keyClick(wave, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.dialog.audition.seek_to.assert_called_once_with(2000)

    def test_manual_bpm_and_phase_are_drafts_apply_together_and_undo_exactly(self):
        before = copy.deepcopy(self.w.session.to_dict())
        self.dialog.change_grid(bpm=60, anchor=2.125)
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertAlmostEqual(self.dialog.wave.snap(6.0, Qt.KeyboardModifier.NoModifier), 6.125)
        self.assertTrue(self.dialog.apply.isEnabled())
        self.dialog.accept_cues()
        self.assertEqual(self.track.bpm, 60)
        self.assertEqual(self.track.beat_grid['anchor'], 2.125)
        self.assertEqual(len(self.w.history), 1)
        self.w.undo()
        self.assertEqual(self.w.session.to_dict(), before)

    def test_bpm_change_rejects_impossible_overlap_and_changed_file_cannot_apply(self):
        before = self.dialog.track.bpm
        self.dialog.change_grid(bpm=20)
        self.assertEqual(self.dialog.track.bpm, before)
        self.assertIn('overlap', self.dialog.status.text())
        self.dialog.change_grid(anchor=2.125)
        Path(self.track.path).write_bytes(b'new version of source')
        self.dialog.accept_cues()
        self.assertEqual(self.track.beat_grid, {})
        self.assertIn('changed on disk', self.dialog.status.text())

    def test_cancelled_audio_preparation_cannot_start_playback(self):
        cancel = threading.Event()
        self.dialog.start_job = Mock(return_value=cancel)
        self.dialog.audition.load = Mock()
        self.dialog.prepare_source(10, True)
        self.dialog.reject()
        self.assertTrue(cancel.is_set())
        self.dialog.start_job.call_args.args[1]({'path': 'unused'})
        self.dialog.audition.load.assert_not_called()

    def test_click_grid_respects_decoded_frames_when_container_has_padding(self):
        import numpy as np
        from studio_ab_audio import RATE
        from studio_grid_audio import GridMixer
        audition = self.dialog.audition
        audition.player.mixer = GridMixer(np.zeros((RATE, 2), dtype=np.float32))
        audition.ready = True
        audition.set_grid({'beats': [.25, .999999, 1.002], 'downbeats': [.25, 1.002]})
        self.assertEqual(audition.player.mixer.beats, (.25,))
        self.assertEqual(audition.player.mixer.downbeats, (.25,))


if __name__ == '__main__':
    unittest.main()
