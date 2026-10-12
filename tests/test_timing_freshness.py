"""Stale source analysis cannot drive timing controls or overwrite fresh choices."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

import djmix
from desktop_app import Studio
from studio_audition import prepare_comparison
from studio_engine import Clip, Session, Track
from studio_guidance import timing_guidance
from studio_recommendations import review_mix
from studio_timing import VERSION, current_analysis, signature


class TimingFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'song.wav'
        self.source.write_bytes(b'original source fixture')
        # Undo must not launch unrelated waveform work on this non-audio fixture.
        bands = patch.object(Studio, 'load_bands')
        bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(self.root)
        self.addCleanup(self.close)
        self.track = Track('song', str(self.source), 'Song', 120, 120, cue_out=120)
        self.track.timing = {'version': VERSION, 'source': signature(self.source), 'state': 'drift',
                            'bpm': 121., 'coverage': .4, 'map': [[0., 0.], [120., 120.]],
                            'regions': [{'start': 16., 'end': 64., 'p95_ms': 60., 'grid': [16., 64.]}]}
        self.w.session = Session(tracks={'song': self.track}, clips=[Clip('song', 0, 120)])
        self.w.selected_id = self.w.session.clips[0].id; self.w.refresh()

    def close(self):
        self.w.close()
        self.assertFalse(self.w.jobs); self.assertFalse(self.w.band_workers)
        self.app.processEvents()  # Deliver queued layout callbacks before deleting the widgets.
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_changed_source_with_any_saved_result_is_explained_and_unavailable(self):
        self.source.write_bytes(b'replaced source with different audio')
        for state in ('drift', 'steady', 'uncertain'):
            with self.subTest(state=state):
                self.track.timing['state'] = state; self.w.refresh()
                self.assertEqual(self.w.timing_result.text(), 'Timing needs updating')
                self.assertFalse(self.w.stabilize.isEnabled()); self.assertFalse(self.w.compare_timing_btn.isEnabled())
                self.assertTrue(self.w.use_bpm_btn.isHidden()); self.assertTrue(self.w.timing_btn.isEnabled())
                self.assertIn('source audio changed', self.w.timing_hint.toolTip().lower())
                self.assertEqual(current_analysis(self.track), {})
                notes = review_mix(self.w.session)['songs'][0]['notes']
                self.assertTrue(any(kind == 'timing' and 'does not match' in note for kind, note in notes))

    def test_missing_source_disables_analysis_with_a_reconnect_explanation(self):
        self.source.unlink(); self.w.refresh()
        self.assertFalse(self.w.timing_btn.isEnabled())
        self.assertIn('Reconnect', self.w.timing_btn.toolTip())
        with patch.object(self.w, 'job') as job:
            self.w.analyze_timing(); job.assert_not_called()

    def test_stale_actions_cannot_change_bpm_enable_correction_or_start_a_job(self):
        self.source.write_bytes(b'changed'); before = copy.deepcopy(self.w.session.to_dict())
        with patch.object(self.w, 'job') as job:
            self.w.use_timing_bpm(); self.w.compare_timing(); self.w.set_clip_value('stabilize', True)
            job.assert_not_called()
        self.assertEqual(self.w.session.to_dict(), before); self.assertEqual(self.w.history, [])
        with patch('studio_audition.render_session') as render:
            with self.assertRaisesRegex(djmix.MixError, 'saved timing does not match'):
                prepare_comparison(self.w.session, self.w.selected_clip(), self.root / 'audio-cache')
            render.assert_not_called()

    def test_existing_correction_can_be_turned_off_when_analysis_is_stale(self):
        self.w.selected_clip().stabilize = True
        self.source.write_bytes(b'changed'); self.w.refresh()
        self.assertTrue(self.w.stabilize.isEnabled()); self.assertFalse(self.w.compare_timing_btn.isEnabled())
        self.w.stabilize.click()
        self.assertFalse(self.w.selected_clip().stabilize)
        self.assertEqual(self.w.history[-1]['action'], 'Toggle tempo correction')

    def test_source_bpm_edits_keep_current_source_timing_and_suggested_bpm_is_explicit(self):
        original = copy.deepcopy(self.track.timing)
        self.w.set_bpm(123.)
        self.assertEqual(current_analysis(self.track), original)
        self.assertTrue(self.w.stabilize.isEnabled()); self.assertTrue(self.w.compare_timing_btn.isEnabled())
        self.w.use_timing_bpm(); self.assertEqual(self.track.bpm, 121.)
        self.w.undo(); self.assertEqual(self.track.bpm, 123.)

    def test_late_result_rejects_changed_audio_bpm_or_path_without_overwriting(self):
        for change in ('audio', 'bpm', 'path', 'duration'):
            with self.subTest(change=change):
                self.source.write_bytes(b'original source fixture')
                self.track.path = str(self.source); self.track.bpm = 120.; self.track.duration = 120.
                self.track.timing = {}; result = {'version': VERSION, 'source': signature(self.source), 'state': 'steady'}
                with patch.object(self.w, 'job') as job: self.w.analyze_timing()
                finished = job.call_args.args[1]
                if change == 'audio': self.source.write_bytes(b'changed')
                elif change == 'bpm': self.track.bpm = 125.
                elif change == 'duration': self.track.duration = 125.
                else:
                    moved = self.root / 'moved.wav'; moved.write_bytes(self.source.read_bytes()); self.track.path = str(moved)
                finished(result)
                self.assertEqual(self.track.timing, {}); self.assertEqual(self.w.history, [])

    def test_matching_late_result_is_applied_without_changing_bpm_or_cues(self):
        before = copy.deepcopy(self.w.session.to_dict())
        result = {**copy.deepcopy(self.track.timing), 'state': 'steady', 'map': [], 'regions': []}
        with patch.object(self.w, 'job') as job: self.w.analyze_timing()
        job.call_args.args[1](result)
        self.assertEqual(self.track.timing, result)
        self.assertEqual(self.track.bpm, before['tracks']['song']['bpm'])
        self.assertEqual(self.w.selected_clip().cue_in, before['clips'][0]['cue_in'])
        self.assertEqual(self.w.selected_clip().cue_out, before['clips'][0]['cue_out'])
        self.assertEqual(len(self.w.history), 1)


if __name__ == '__main__': unittest.main()
