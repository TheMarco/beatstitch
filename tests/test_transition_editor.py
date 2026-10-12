"""Draft isolation, apply/undo and safe preparation lifecycle in the native editor."""
import copy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QPushButton

from desktop_app import Studio
from tests.test_transitions import project


class BlendEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(Studio, 'load_bands'):
            self.window = Studio(Path(self.temp.name))
        self.window.session = project()
        self.window.selected_id = self.window.session.clips[2].id
        self.window.refresh()
        self.addCleanup(self.close_window)

    def close_window(self):
        dialog = self.window.transition_editor
        if dialog is not None:
            dialog.busy = False
            dialog.reject()
        self.window.close()

    def test_draft_is_explicit_pair_two_three_and_one_undo(self):
        w = self.window
        before = copy.deepcopy(w.session.to_dict())
        w.edit_blend()
        dialog = w.transition_editor
        self.assertEqual(dialog.index, 2)
        self.assertEqual(dialog.view._index, 2)
        self.assertFalse(dialog.apply.isEnabled())
        dialog.length.setValue(4)
        dialog.bass.setChecked(False)
        dialog.balance.setChecked(True)
        dialog.effect.setCurrentIndex(dialog.effect.findData('echo'))
        self.assertEqual(w.session.to_dict(), before)
        self.assertEqual(len(w.history), 0)
        self.assertTrue(dialog.apply.isEnabled())
        self.assertEqual(dialog.close_button.text(), 'Discard draft')
        dialog.apply_changes()
        self.assertEqual(w.session.clips[2].blend_bars, 4)
        self.assertFalse(w.session.clips[2].bass_swap)
        self.assertTrue(w.session.clips[2].balance_levels)
        self.assertEqual(w.session.clips[2].transition_effect, 'echo')
        self.assertEqual(len(w.history), 1)
        self.assertIn('Edit 2 → 3 blend', w.act_undo.text())
        w.undo()
        self.assertEqual(w.session.to_dict(), before)
        w.edit_blend()
        w.transition_editor.length.setValue(3)
        w.transition_editor.reject()
        self.assertEqual(w.session.to_dict(), before)

    def test_stale_draft_cannot_overwrite_changed_mix(self):
        w = self.window
        w.edit_blend()
        dialog = w.transition_editor
        dialog.length.setValue(4)
        w.edit(lambda: setattr(w.session, 'target_bpm', 125), action='Change tempo')
        dialog.apply_changes()
        self.assertEqual(w.session.clips[2].blend_bars, 2)
        self.assertIn('mix changed', dialog.status.text())

    def voice_pair(self):
        from studio_beat_grid import grid_settings
        from tests.test_overlap_content import voice_content
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        w = self.window
        for identity, intervals in (('1', [(16., 20.)]), ('2', [(0., 10.)])):
            track = w.session.tracks[identity]
            path = Path(self.temp.name) / (identity + '.wav'); path.write_bytes(b'fixture')
            track.path = str(path); track.content = voice_content(track, intervals)
        track = w.session.tracks['2']; track.beat_grid = grid_settings(track, 120., 0.)
        w.session.clips[2].intro_percussive = True
        w.refresh(); w.edit_blend()
        return w.transition_editor

    def test_voice_cue_is_auditionable_draft_with_explicit_apply_and_exact_undo(self):
        d = self.voice_pair(); w = self.window
        before = copy.deepcopy(w.session.to_dict())
        self.assertIn('Possible voices together', d.voices.summary.text())
        d.voices.choices.findChildren(QPushButton, 'tryVoiceCue')[0].click()
        self.assertEqual(d.session.clips[2].cue_in, 10.)
        self.assertEqual(d.session.clips[2].cue_out, 20.)
        self.assertFalse(d.session.clips[2].intro_percussive)
        self.assertTrue(d.apply.isEnabled()); self.assertTrue(d.compare.isEnabled())
        self.assertIn('omits 10.0 source seconds', d.choice_reason.text())
        self.assertIn('No simultaneous', d.voices.summary.text())
        self.assertEqual(w.session.to_dict(), before)
        d.apply_changes()
        self.assertEqual(w.session.clips[2].cue_in, 10.)
        self.assertEqual(len(w.history), 1)
        w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_replaced_source_cannot_accept_a_voice_cue(self):
        d = self.voice_pair(); w = self.window
        d.voices.choices.findChildren(QPushButton, 'tryVoiceCue')[0].click()
        Path(w.session.tracks['1'].path).write_bytes(b'replacement outgoing audio')
        d.apply_changes()
        self.assertEqual(w.session.clips[2].cue_in, 0.)
        self.assertIn('source changed on disk', d.status.text())

    def test_voice_check_publishes_annotations_only_and_cancel_rejects_late_results(self):
        d = self.voice_pair(); w = self.window
        results = {identity: copy.deepcopy(w.session.tracks[identity].content) for identity in ('1', '2')}
        for identity in results:
            w.session.tracks[identity].content = {}; d.session.tracks[identity].content = {}
        before = copy.deepcopy(w.session.clips); revision = w.revision, w.audio_revision
        d.start_job = Mock(return_value=threading.Event())
        d.check_voices(); d.start_job.call_args.args[1](results)
        self.assertEqual(w.session.clips, before)
        self.assertEqual((w.revision, w.audio_revision), revision)
        self.assertEqual(w.session.tracks['2'].content, results['2'])
        self.assertIn('Possible voices together', d.voices.summary.text())
        self.assertFalse(d.apply.isEnabled())
        saved = copy.deepcopy(w.session.to_dict()); cancel = threading.Event()
        d.start_job = Mock(return_value=cancel); d.check_voices(); d.reject()
        self.assertTrue(cancel.is_set())
        d.start_job.call_args.args[1]({})
        self.assertEqual(w.session.to_dict(), saved)

    def test_local_harmony_uses_prepared_draft_and_is_invalidated_by_edits(self):
        w = self.window; before = copy.deepcopy(w.session.to_dict())
        w.edit_blend(); d = w.transition_editor
        d.start_job = Mock(return_value=threading.Event())
        d.player.load = Mock(); d.player.play = Mock()
        d.toggle()
        side = {'state': 'estimated', 'keys': ['C major'], 'tonal': True, 'reason': 'Consistent local estimate.'}
        local = {'state': 'possible_clash', 'reason': 'Try different cues.',
                 'outgoing': side, 'incoming': {**side, 'keys': ['C# major']}}
        with patch('studio_transition_editor.prepare_blend', return_value={'folder': 'prepared'}), \
                patch('studio_transition_editor.analyze_overlap', return_value=local) as analyze:
            report = d.start_job.call_args.args[0](lambda _: None, lambda _: None)
        self.assertEqual(analyze.call_args.args[0].to_dict(), before)
        self.assertEqual(analyze.call_args.args[1], 2)
        d.start_job.call_args.args[1](report)
        self.assertIn('Possible clash in this overlap', d.local_advice.text())
        d.player.play.assert_called_once()
        d.length.setValue(4)
        self.assertIsNone(d.overlap_harmony)
        self.assertIn('Overlap changed', d.local_advice.text())
        self.assertEqual(w.session.to_dict(), before)

    def test_crowding_check_and_shorter_alternative_are_explicit_and_undoable(self):
        w = self.window; before = copy.deepcopy(w.session.to_dict())
        w.edit_blend(); d = w.transition_editor
        d.start_job = Mock(return_value=threading.Event())
        d.player.play = Mock()
        d.crowding.check_audio()
        self.assertTrue(d.busy)
        result = {'state': 'possible_crowding', 'seconds': 4., 'parts': [{'start': 0., 'end': 4., 'flagged': True}]}
        with patch('studio_crowding_ui.prepare_blend', return_value={'folder': 'prepared'}), \
                patch('studio_crowding_ui.source_signature', return_value=['unchanged']), \
                patch('studio_crowding_ui.analyze_crowding', return_value=result) as analyze:
            result = d.start_job.call_args.args[0](lambda _: None, lambda _: None)
        self.assertEqual(analyze.call_args.args[0].to_dict(), before)
        d.start_job.call_args.args[1](result)
        self.assertFalse(d.busy); self.assertFalse(d.apply.isEnabled())
        d.player.play.assert_not_called()
        self.assertIn('Possible musical crowding', d.crowding.summary.text())
        self.assertEqual(w.session.to_dict(), before)
        d.seek_to = Mock(); d.ready = True; d.crowding.listen_here()
        d.seek_to.assert_called_once(); d.player.play.assert_called_once()
        d.crowding.try_shorter()
        self.assertEqual(d.session.clips[2].blend_bars, 1)
        self.assertIsNone(d.crowding.result)
        self.assertTrue(d.compare.isEnabled()); self.assertTrue(d.apply.isEnabled())
        self.assertEqual(w.session.to_dict(), before)
        d.apply_changes(); self.assertEqual(w.session.clips[2].blend_bars, 1)
        w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_crowding_cancel_and_stale_draft_reject_late_results(self):
        w = self.window; w.edit_blend(); d = w.transition_editor
        cancel = threading.Event(); d.start_job = Mock(return_value=cancel)
        d.crowding.check_audio()
        d.session.clips[2].bass_swap = not d.session.clips[2].bass_swap
        d.start_job.call_args.args[1]({'state': 'not_flagged'})
        self.assertIsNone(d.crowding.result)
        self.assertIn('draft changed', d.status.text())
        d.crowding.check_audio(); d.reject()
        self.assertTrue(cancel.is_set())
        d.start_job.call_args.args[1]({'state': 'not_flagged'})
        self.assertIsNone(d.crowding.result)

    def crowding_search_result(self, d):
        from studio_crowding_choices import choice_fingerprint
        analysis = {'state': 'not_flagged', 'seconds': 0., 'parts': [], 'overlap_seconds': 4.}
        return {'checked': 1, 'fingerprint': choice_fingerprint(d.session, d.index), 'reason': 'Listen to compare.',
                'choices': [{'time': 8., 'source_delta': 8., 'mix_delta': -8., 'before_seconds': 4.,
                             'seconds': 0., 'analysis': analysis}]}

    def crowding_pair(self):
        self.voice_pair().reject()
        self.window.session.clips[2].cue_out = 28.
        self.window.refresh(); self.window.edit_blend()
        return self.window.transition_editor

    def test_measured_entrance_preserves_user_mix_until_apply_and_undo(self):
        d = self.crowding_pair(); w = self.window; before = copy.deepcopy(w.session.to_dict())
        d.crowding.result = {'state': 'possible_crowding', 'seconds': 4.}
        d.start_job = Mock(return_value=threading.Event()); d.crowding.find_entrances()
        d.start_job.call_args.args[1](self.crowding_search_result(d))
        # Unrelated observations learned during listening do not stale the audio.
        d.session.tracks['2'].song_sections = {'new': 'annotation'}
        d.crowding.try_entrance(0)
        self.assertEqual(d.session.clips[2].cue_in, 8.)
        self.assertEqual(d.session.clips[2].cue_out, 28.)
        self.assertEqual(d.session.clips[2].blend_bars, 2)
        self.assertFalse(d.session.clips[2].intro_percussive)
        self.assertIn('No musical crowding flagged', d.crowding.summary.text())
        self.assertEqual(w.session.to_dict(), before); self.assertTrue(d.compare.isEnabled())
        d.apply_changes(); self.assertEqual(w.session.clips[2].cue_in, 8.)
        w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_measured_entrance_rejects_changed_source_and_late_search_after_cancel(self):
        d = self.crowding_pair(); w = self.window; before = copy.deepcopy(w.session.to_dict())
        d.crowding.result = {'state': 'possible_crowding', 'seconds': 4.}
        d.crowding.search_result = self.crowding_search_result(d)
        Path(d.session.tracks['1'].path).write_bytes(b'replaced outgoing source')
        d.crowding.try_entrance(0)
        self.assertIsNone(d.crowding.search_result); self.assertEqual(w.session.to_dict(), before)
        d.crowding.result = {'state': 'possible_crowding', 'seconds': 4.}
        cancel = threading.Event(); d.start_job = Mock(return_value=cancel)
        d.crowding.find_entrances(); d.reject(); self.assertTrue(cancel.is_set())
        d.start_job.call_args.args[1]({'choices': [], 'checked': 1, 'reason': 'Late'})
        self.assertIsNone(d.crowding.search_result)

    def test_new_voice_observations_can_reject_a_previously_offered_entrance(self):
        from tests.test_overlap_content import voice_content
        d = self.crowding_pair()
        d.crowding.search_result = self.crowding_search_result(d)
        track = d.session.tracks['2']
        track.content = voice_content(track, [(8., 12.)])
        d.crowding.try_entrance(0)
        self.assertEqual(d.session.clips[2].cue_in, 0.)
        self.assertIsNone(d.crowding.search_result)
        self.assertIn('New voice observations', d.status.text())

    def test_cancel_during_preparation_never_loads_or_plays(self):
        w = self.window
        w.edit_blend()
        dialog = w.transition_editor
        cancel = threading.Event()
        job = Mock(return_value=cancel)
        dialog.start_job = job
        dialog.player.load = Mock()
        dialog.player.play = Mock()
        dialog.toggle()
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.settings.isEnabled())
        dialog.reject()
        self.assertTrue(cancel.is_set())
        job.call_args.args[1]({'folder': 'unused'})
        dialog.player.load.assert_not_called()
        dialog.player.play.assert_not_called()

    def test_new_draft_invalidates_listening_and_failed_job_is_retryable(self):
        self.window.edit_blend()
        dialog = self.window.transition_editor
        dialog.ready = True
        dialog.player.close = Mock()
        dialog.length.setValue(3)
        self.assertFalse(dialog.ready)
        dialog.player.close.assert_called_once()
        dialog.start_job = Mock(return_value=threading.Event())
        dialog.toggle()
        dialog.start_job.call_args.kwargs['failed_callback']('Source unavailable')
        self.assertFalse(dialog.busy)
        self.assertTrue(dialog.settings.isEnabled())
        self.assertEqual(dialog.status.text(), 'Source unavailable')

    def test_pitch_alternative_remains_a_draft_then_applies_as_one_undo(self):
        w = self.window
        w.session.tracks['2'].key = 'D minor'
        before = copy.deepcopy(w.session.to_dict())
        w.edit_blend()
        dialog = w.transition_editor
        index = next(i for i, option in enumerate(dialog.choice_options) if option['clip'].shift == -2)
        dialog.choice.setCurrentIndex(index)
        self.assertEqual(w.session.to_dict(), before)
        self.assertTrue(dialog.compare.isEnabled())
        self.assertIn('whole incoming clip', dialog.choice_reason.text())
        dialog.length.setValue(3)
        self.assertEqual(dialog.choice.currentIndex(), -1)
        dialog.apply_changes()
        self.assertEqual((w.session.clips[2].shift, w.session.clips[2].blend_bars), (-2, 3))
        self.assertEqual(len(w.history), 1)
        w.undo()
        self.assertEqual(w.session.to_dict(), before)

    def test_cancel_comparison_does_not_open_or_play_late_audio(self):
        w = self.window
        w.edit_blend()
        dialog = w.transition_editor
        dialog.choice.setCurrentIndex(1)
        cancel = threading.Event()
        dialog.start_job = Mock(return_value=cancel)
        dialog.compare_draft()
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.choice.isEnabled())
        dialog.reject()
        self.assertTrue(cancel.is_set())
        with patch('studio_blend_comparison.BlendComparison') as comparison:
            dialog.start_job.call_args.args[1]({'folder': 'unused'})
        comparison.assert_not_called()

    def test_comparison_selection_stays_in_sync_and_requires_explicit_apply(self):
        import numpy as np
        import soundfile as sf
        from studio_blend_comparison import BlendComparison
        w = self.window
        current, draft = copy.deepcopy(w.session), copy.deepcopy(w.session)
        draft.clips[2].blend_bars = 4
        root = Path(self.temp.name)
        for name in ('current', 'draft'):
            sf.write(root / f'{name}.wav', np.zeros((96000, 2)), 48000)
        report = {'folder': str(root), 'start': 32, 'end': 34, 'duration': 2}
        apply = Mock(side_effect=[False, True])
        d = BlendComparison(current, draft, 2, {}, report, 'system', 0, apply, w)
        self.addCleanup(d.player.close)
        d.player.seek(500)
        d.choices.buttons()[1].setChecked(True)  # Also the accessibility toggle path.
        self.assertEqual(d.player.variant, 'draft')
        self.assertEqual(d.player.position(), 500)
        self.assertAlmostEqual(d.view._incoming_start, 28)
        apply.assert_not_called()
        d.commit()
        self.assertIn('could not accept', d.status.text())
        d.commit()
        self.assertEqual(d.result(), d.DialogCode.Accepted)
        self.assertEqual(w.session.to_dict(), current.to_dict())

    def test_editor_controls_do_not_overlap_waveform_at_laptop_size(self):
        self.window.edit_blend()
        d = self.window.transition_editor
        d.resize(760, 680)
        d.effect.setCurrentIndex(d.effect.findData('echo'))
        self.app.processEvents()
        self.assertGreater(d.choice.geometry().top(), d.view.geometry().bottom())
        self.assertGreater(d.settings.geometry().top(), d.choice_reason.geometry().bottom())
        self.assertGreater(d.play.geometry().top(), d.details_scroll.geometry().bottom())
        self.assertLess(d.buttons.geometry().bottom(), d.height())
        d.details_scroll.ensureWidgetVisible(d.effect)
        self.app.processEvents()
        from PySide6.QtCore import QPoint
        top = d.effect.mapTo(d.details_scroll.viewport(), QPoint(0, 0)).y()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(top + d.effect.height(), d.details_scroll.viewport().height())


if __name__ == '__main__':
    unittest.main()
