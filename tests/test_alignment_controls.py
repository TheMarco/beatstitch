"""Live alignment preview, draft isolation, source bounds, and exact apply/undo."""
import copy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PySide6.QtWidgets import QApplication

from desktop_app import Studio
from studio_alignment_audio import AlignmentMixer, AlignmentTransport, RATE, alignment_identity
from studio_alignment_ui import visual_evidence
from tests.test_ab_audio import Device, Sink
from tests.test_beat_alignment import make_kicks
from tests.test_transitions import project


def preview_report(session, index, delay=.06):
    margin = RATE * 2; frames = RATE * 8
    outgoing = make_kicks(12, 120, RATE)
    incoming = make_kicks(12, 120, RATE, shift=delay)
    sides = []
    for audio in (outgoing, incoming):
        stereo = np.column_stack([audio, -audio]).astype(np.float32) * .15
        sides.append({'full': stereo, 'low': stereo.copy()})
    return visual_evidence({'sides': sides, 'margin': margin, 'frames': frames, 'bpm': 120,
                            'cue': session.clips[index].cue_in, 'start': 0,
                            'intro_percussive': session.clips[index].intro_percussive,
                            'identity': alignment_identity(session, index)})


class AlignmentControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with patch.object(Studio, 'load_bands'): self.window = Studio(self.root)
        self.window.session = project()
        for track in self.window.session.tracks.values():
            path = self.root / f'{track.id}.wav'; path.write_bytes(b'fixture'); track.path = str(path)
        self.window.session.clips[2].cue_in = 4.
        self.window.session.clips[2].intro_percussive = True
        self.window.selected_id = self.window.session.clips[2].id
        self.window.refresh(); self.window.edit_blend()
        self.dialog = self.window.transition_editor
        self.controls = self.dialog.alignment
        self.controls.player.play = Mock()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.dialog.busy = False; self.dialog.reject(); self.window.close()

    def ready(self):
        from studio_beat_alignment import estimate_alignment
        report = preview_report(self.dialog.session, self.dialog.index)
        a, b = [s['full'][report['margin']:report['margin'] + report['frames']] for s in report['sides']]
        result = estimate_alignment(a, b, RATE, 120)
        self.controls.set_result(report, result)
        return report

    def test_automatic_alignment_is_reviewable_and_apply_undo_restores_exactly(self):
        before = copy.deepcopy(self.window.session.to_dict())
        self.ready()
        self.assertAlmostEqual(self.dialog.session.clips[2].cue_in, 4.06, delta=.015)
        self.assertFalse(self.dialog.session.clips[2].intro_percussive)
        self.assertTrue(self.dialog.apply.isEnabled()); self.assertTrue(self.dialog.compare.isEnabled())
        self.assertEqual(self.window.session.to_dict(), before)
        self.assertFalse(self.window.history)
        self.dialog.apply_changes()
        self.assertEqual(len(self.window.history), 1)
        self.window.undo(); self.assertEqual(self.window.session.to_dict(), before)

    def test_live_nudge_monitor_and_reset_do_not_start_processing_or_change_project(self):
        before = copy.deepcopy(self.window.session.to_dict()); self.ready()
        self.dialog.start_job = Mock()
        self.controls.player.play.reset_mock()
        self.controls.player.playing = True
        frame = self.controls.player.mixer.frame
        self.controls.nudge(-.01)
        self.assertAlmostEqual(self.controls.view.offset, self.controls.offset)
        cue = self.dialog.session.clips[2].cue_in
        self.controls.before.click(); self.assertEqual(self.controls.player.mixer.offset, 0)
        self.assertEqual(self.dialog.session.clips[2].cue_in, cue)
        self.controls.after.click()
        self.assertAlmostEqual(self.controls.player.mixer.offset, self.controls.offset * RATE)
        self.controls.reset.click()
        self.assertEqual(self.dialog.session.clips[2].cue_in, 4.)
        self.assertTrue(self.dialog.session.clips[2].intro_percussive)
        self.assertFalse(self.dialog.apply.isEnabled())
        self.assertEqual(self.controls.player.mixer.frame, frame)
        self.assertTrue(self.controls.player.playing)
        self.controls.player.play.assert_not_called(); self.dialog.start_job.assert_not_called()
        self.assertEqual(self.window.session.to_dict(), before)

    def test_bounds_and_changed_audio_cannot_silently_change_the_draft(self):
        self.ready(); cue = self.dialog.session.clips[2].cue_in
        self.assertFalse(self.controls.move_to(100))
        self.assertEqual(self.dialog.session.clips[2].cue_in, cue)
        self.dialog.session.clips[2].semitones = 1
        self.dialog.session.clips[2].transpose = True
        self.dialog.change_draft()
        self.assertIsNone(self.controls.data)
        self.assertFalse(self.controls.earlier.isEnabled())
        self.assertFalse(self.controls.player.playing)

    def test_uncertain_check_does_not_move_a_cue(self):
        report = preview_report(self.dialog.session, self.dialog.index)
        before = copy.deepcopy(self.dialog.session.to_dict())
        self.controls.set_result(report, {'state': 'uncertain', 'offset_seconds': None, 'reason': 'No stable pulse.'})
        self.assertEqual(self.dialog.session.to_dict(), before)
        self.assertFalse(self.dialog.apply.isEnabled())
        self.assertIn('No reliable automatic correction', self.controls.summary.text())

    def test_late_results_after_cancellation_and_source_replacement_are_rejected(self):
        report = preview_report(self.dialog.session, self.dialog.index)
        cancel = threading.Event(); self.dialog.start_job = Mock(return_value=cancel)
        self.controls.align()
        finished = self.dialog.start_job.call_args.args[1]
        track = self.dialog.session.tracks[self.dialog.session.clips[2].track_id]
        Path(track.path).write_bytes(b'replacement source')
        finished((report, {'state': 'suggested', 'offset_seconds': -.06, 'reason': 'fixture'}))
        self.assertIsNone(self.controls.data)
        self.assertEqual(self.dialog.session.clips[2].cue_in, 4.)
        self.controls.align(); self.dialog.reject()
        self.assertTrue(cancel.is_set())
        self.dialog.start_job.call_args.args[1]((report, {'state': 'aligned', 'offset_seconds': 0, 'reason': 'fixture'}))
        self.assertIsNone(self.controls.data)

    def test_measured_markers_move_visually_and_draw_without_errors(self):
        report = self.ready()
        for i in (0, 1): self.assertGreater(len(report['markers'][i]), 8)
        original_b = report['markers'][1].copy()
        self.controls.move_to(-.1)
        self.assertEqual(self.controls.view.offset, -.1)
        np.testing.assert_array_equal(report['markers'][1], original_b)
        self.controls.view.resize(650, 156)
        self.assertFalse(self.controls.view.grab().isNull())

    def test_direct_alignment_view_focuses_one_task_and_keeps_the_draft_when_returning(self):
        self.ready(); cue = self.dialog.session.clips[2].cue_in
        self.dialog.focus_alignment()
        self.assertTrue(self.dialog.view.isHidden())
        self.assertFalse(self.controls.isHidden())
        self.assertEqual(self.dialog.full_listen_label, 'Listen to full blend')
        self.assertEqual(self.dialog.windowTitle(), 'Align beats 2 → 3')
        self.dialog.more_blend_controls.click()
        self.assertFalse(self.dialog.view.isHidden())
        self.assertEqual(self.dialog.session.clips[2].cue_in, cue)


class LiveMixerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_live_controls_keep_one_sink_and_sample_clock(self):
        with tempfile.TemporaryDirectory() as folder:
            session = project()
            for track in session.tracks.values():
                p = Path(folder) / f'{track.id}.wav'; p.write_bytes(b'fixture'); track.path = str(p)
            report = preview_report(session, 1)
        player = AlignmentTransport(sink_factory=Sink)
        self.addCleanup(player.close)
        player.load_report(report); player.set_device(Device()); player.play()
        stream, starts, resets = player.stream, player.start_count, player.reset_count
        previous = player.mixer.frame
        for i in range(90):
            player.set_offset(-.06 if i % 2 else 0)
            player.set_focus(bool(i % 3)); player.select('blend' if i % 2 else 'incoming')
            player.sink.consume(480); player._pump()
            self.assertGreaterEqual(player.mixer.frame, previous); previous = player.mixer.frame
            self.assertIs(player.stream, stream)
            self.assertEqual((player.start_count, player.reset_count), (starts, resets))

    def test_offset_moves_audio_peaks_in_the_same_direction_as_the_view(self):
        frames, margin = RATE * 2, RATE
        a = np.zeros((frames + margin * 2, 2), dtype=np.float32)
        b = a.copy(); b[margin + RATE + 2880:margin + RATE + 2880 + 20] = .4
        mixer = AlignmentMixer({'sides': [{'low': a, 'full': a}, {'low': b, 'full': b}],
                                'margin': margin, 'frames': frames})
        mixer.set_offset(-.06)
        heard = np.frombuffer(mixer.read(frames), dtype='<f4').reshape(-1, 2)
        self.assertEqual(int(np.argmax(heard[:, 0])), RATE)
        mixer.seek(0); mixer.set_offset(.04)
        heard = np.frombuffer(mixer.read(frames), dtype='<f4').reshape(-1, 2)
        self.assertEqual(int(np.argmax(heard[:, 0])), RATE + 4800)


if __name__ == '__main__': unittest.main()
