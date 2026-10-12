"""The guided setup must remain a draft until one reversible Apply."""
import copy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

import djmix
from desktop_app import Studio
from studio_engine import Clip, Session, Track, layout
from studio_first_mix import starter_session


def songs():
    return {str(i): Track(str(i), f'{i}.wav', f'Song {i}', 120, bpm, 'C minor', cue_out=120)
            for i, bpm in enumerate((120, 128, 130))}


class StarterTests(unittest.TestCase):
    def test_library_cues_and_original_keys_without_mutating_sources(self):
        tracks = songs(); tracks['0'].cue_in = 8
        before = copy.deepcopy(tracks)
        draft, notes = starter_session(tracks, ['0', '1'], 124, 8)
        self.assertEqual(tracks, before)
        self.assertEqual(draft.clips[0].cue_in, 8)
        self.assertTrue(all(c.shift == 0 for c in draft.clips))
        self.assertEqual(layout(draft)[1]['incoming'], round(8 * 240 / 124 * djmix.SR))
        self.assertEqual(notes, [])

    def test_short_middle_song_limits_both_overlaps(self):
        tracks = songs(); tracks['1'].bpm = 120; tracks['1'].cue_out = 10
        draft, notes = starter_session(tracks, list(tracks), 127.3, 8)
        self.assertEqual([c.blend_bars for c in draft.clips[1:]], [2, 2])
        positions = layout(draft)
        self.assertLess(positions[1]['incoming'] + positions[2]['incoming'], positions[1]['length'])
        self.assertEqual(len(notes), 2)
        tracks['1'].cue_out = 4
        with self.assertRaisesRegex(djmix.MixError, 'too little kept audio'):
            starter_session(tracks, list(tracks), 120)

    def test_missing_bpm_duplicate_songs_and_invalid_cues_are_rejected(self):
        tracks = songs(); tracks['1'].bpm = None
        with self.assertRaisesRegex(djmix.MixError, 'needs a BPM'):
            starter_session(tracks, ['0', '1'], 120)
        with self.assertRaisesRegex(djmix.MixError, 'different songs'):
            starter_session(tracks, ['0', '0'], 120)
        tracks['0'].cue_in = 130
        with self.assertRaisesRegex(djmix.MixError, 'library start and end cues'):
            starter_session(tracks, ['0', '2'], 120)


class GuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(self.root)
        self.addCleanup(self.close)
        self.w.session = Session(tracks=songs(), clips=[Clip('2', 12, 100, semitones=2, transpose=True)])
        for track in self.w.session.tracks.values():
            track.path = str(self.root / f'{track.id}.wav'); Path(track.path).touch()
        self.w.refresh()
        def synchronous(work, finished, **kwargs):
            try: result = work(lambda _: None, self.w.receive_track)
            except Exception as error: kwargs['failed_callback'](str(error))
            else: finished(result)
            return threading.Event()
        job = patch.object(self.w, 'job', side_effect=synchronous); job.start(); self.addCleanup(job.stop)
        energy = patch('studio_order_dialog.estimate_mix_energy', return_value={})
        energy.start(); self.addCleanup(energy.stop)

    def close(self):
        guide = self.w.guide_dialog
        if guide:
            if guide.planner: guide.planner.reject()
            guide.busy = False; guide.reject()
        self.w.close(); self.app.processEvents()

    def open_guide(self):
        self.w.start_mix_guide(); guide = self.w.guide_dialog
        for identity in ('0', '1'): guide.rows[identity][1].click()
        return guide

    def review(self, guide):
        guide.next_step(); guide.next_step(); self.app.processEvents()
        self.assertIsNotNone(guide.planner.proposal)
        return guide.planner

    def test_cancel_preserves_mix_and_filter_keeps_selection(self):
        before = copy.deepcopy(self.w.session.to_dict())
        guide = self.open_guide(); guide.filter.setText('Song 2')
        self.assertEqual(guide.selected(), ['0', '1'])
        self.assertTrue(guide.next_button.isEnabled())
        planner = self.review(guide)
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertEqual(planner.back.text(), 'Back to settings')
        self.assertEqual(planner.apply.text(), 'Replace arrangement')
        self.assertIn('selected songs:', planner.details.toPlainText())
        planner.reject(); guide.reject()
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertEqual(self.w.history, [])

    def test_missing_bpm_is_draft_until_apply_and_one_undo_restores_everything(self):
        self.w.session.tracks['1'].bpm = None
        self.w.session.fade_in_bars, self.w.session.fade_out_bars = 2, 4
        self.w.mix_cursor = 75
        before = copy.deepcopy(self.w.session.to_dict())
        guide = self.open_guide()
        self.assertFalse(guide.rows['1'][1].isEnabled())
        self.assertFalse(guide.next_button.isEnabled())
        with patch('studio_first_mix.QInputDialog.getDouble', return_value=(126., True)):
            guide.enter_bpm('1')
        guide.rows['1'][1].setChecked(True)
        self.assertIsNone(self.w.session.tracks['1'].bpm)
        planner = self.review(guide)
        self.assertTrue(planner.listen.isEnabled())
        planner.commit()
        self.assertEqual(guide.pages.currentIndex(), 2)
        self.assertEqual(len(self.w.history), 1)
        self.assertEqual(self.w.history[-1]['action'], 'Create mix with guide')
        self.assertEqual(self.w.session.tracks['1'].bpm, 126.)
        self.assertEqual([c.track_id for c in self.w.session.clips], ['0', '1'])
        self.assertTrue(all(c.shift == 0 for c in self.w.session.clips))
        self.assertEqual((self.w.session.fade_in_bars, self.w.session.fade_out_bars), (0, 0))
        self.assertEqual(self.w.mix_cursor, 0)
        guide.reject(); self.w.undo()
        self.assertEqual(self.w.session.to_dict(), before)

    def test_stale_draft_is_rejected(self):
        guide = self.open_guide(); planner = self.review(guide)
        before = copy.deepcopy(self.w.session.to_dict()); self.w.revision += 1
        planner.commit()
        self.assertIn('arrangement changed', planner.status.text())
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertEqual(self.w.history, [])

    def test_missing_song_explains_recovery_and_refresh_keeps_other_selections(self):
        source = Path(self.w.session.tracks['1'].path); source.unlink()
        guide = self.open_guide()
        field, chosen, name, bpm, listen = guide.rows['1']
        self.assertIn('Source file missing', name.text())
        self.assertIn('File → Reconnect source audio', name.text())
        self.assertIn('Refresh', name.text())
        self.assertFalse(chosen.isEnabled()); self.assertFalse(listen.isEnabled())
        self.assertFalse(bpm.isEnabled()); self.assertFalse(guide.next_button.isEnabled())
        self.assertEqual(chosen.accessibleDescription(), chosen.toolTip())
        self.assertEqual(listen.accessibleName(), 'Listen to Song 1')
        source.touch(); guide.refresh_songs()
        self.assertEqual(guide.selected(), ['0'])
        self.assertTrue(chosen.isEnabled()); self.assertTrue(listen.isEnabled())
        self.assertNotIn('Source file missing', name.text())
        chosen.click(); self.assertTrue(guide.next_button.isEnabled())

    def test_source_removed_before_either_next_step_keeps_current_arrangement(self):
        before = copy.deepcopy(self.w.session.to_dict())
        source = Path(self.w.session.tracks['1'].path)
        for step in (0, 1):
            with self.subTest(step=step):
                source.touch(); guide = self.open_guide()
                if step: guide.next_step()
                source.unlink(); guide.next_step()
                self.assertEqual(guide.pages.currentIndex(), step)
                self.assertIsNone(guide.planner)
                self.assertIn('Source audio is missing for Song 1', guide.status.text())
                self.assertIn('arrangement has not changed', guide.status.text())
                self.assertEqual(guide.selected(), ['0', '1'])
                self.assertEqual(self.w.session.to_dict(), before)
                self.assertEqual(self.w.history, [])
                guide.reject(); self.app.processEvents()

    def test_source_removed_after_draft_review_cannot_replace_mix_and_can_be_restored(self):
        before = copy.deepcopy(self.w.session.to_dict())
        guide = self.open_guide(); planner = self.review(guide)
        source = Path(self.w.session.tracks['1'].path); source.unlink()
        planner.commit()
        self.assertIn('Source audio is missing for Song 1', planner.status.text())
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertEqual(self.w.history, [])
        self.assertFalse(guide.created)
        source.touch(); planner.commit()
        self.assertTrue(guide.created)
        self.assertEqual(len(self.w.history), 1)
        self.assertEqual([c.track_id for c in self.w.session.clips], ['0', '1'])

    def test_import_callbacks_keep_new_songs_but_close_waits_for_completion(self):
        guide = self.open_guide(); before = copy.deepcopy(self.w.session.clips)
        with patch('studio_first_mix.QFileDialog.getOpenFileNames', return_value=([str(self.root / 'new.wav')], '')):
            with patch.object(self.w, 'job', return_value=threading.Event()) as job:
                guide.import_audio()
        self.assertTrue(guide.busy)
        self.assertTrue(job.call_args.kwargs['cancellable'])
        track = Track('new', str(self.root / 'new.wav'), 'New song', 120, 120, cue_out=120)
        self.w.receive_track(track)
        guide.reject()
        self.assertTrue(guide.cancel.is_set()); self.assertIs(self.w.guide_dialog, guide)
        job.call_args.kwargs['failed_callback']('Operation cancelled.')
        self.assertIsNone(self.w.guide_dialog)
        self.assertIn('new', self.w.session.tracks)
        self.assertEqual(self.w.session.clips, before)

    def test_cancelled_analysis_is_not_reported_as_a_manual_bpm_failure(self):
        path = self.root / 'new.wav'; track = Track('new', str(path), 'New song', 120, None)
        with patch.object(self.w, 'job') as job:
            self.w.import_paths([path], cancellable=True)
        work = job.call_args.args[0]; published = []
        with patch('desktop_app.load_track', return_value=track), patch('desktop_app.estimate_track', side_effect=djmix.MixError('Operation cancelled.')):
            with self.assertRaisesRegex(djmix.MixError, 'Operation cancelled'):
                work(lambda _: None, published.append)
        self.assertEqual(len(published), 1)
        self.assertNotIn('manual analysis', published[0].status)


if __name__ == '__main__': unittest.main()
