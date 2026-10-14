"""Library batches must be ordered, atomic, undoable, and safe for source media."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import soundfile as sf
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

import djmix
from desktop_app import Studio
from studio_engine import Clip, Session, Track, load_track
from studio_library import duplicate_track
from studio_source_identity import fingerprint
from studio_widgets import TRACKS_MIME


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(self.root); self.addCleanup(self.close)
        tracks = {}
        for identity, name, bpm, key in [('a', 'Zulu', 128, 'A minor'), ('b', 'Alpha', 124, 'E minor'),
                                          ('c', 'Middle', 150, 'C# minor')]:
            path = self.root / f'{identity}.wav'; path.write_bytes(identity.encode())
            tracks[identity] = Track(identity, str(path), name, 120, bpm, key, cue_out=120)
        self.w.session = Session(tracks=tracks)
        self.w.refresh(); self.w.show(); self.app.processEvents()

    def close(self):
        self.w.close(); self.app.processEvents()

    def test_keyboard_batch_uses_visible_sort_and_one_undo(self):
        w = self.w
        w.library.select_ids(['a', 'b']); w.library._sort(0)
        self.assertEqual(w.library.selected_ids(), ['b', 'a'])
        w.refresh(); self.assertEqual(w.library.selected_ids(), ['b', 'a'])
        QTest.keyClick(w.library, Qt.Key.Key_Return)
        self.assertEqual([c.track_id for c in w.session.clips], ['b', 'a'])
        self.assertEqual(len(w.history), 1)
        self.assertEqual(w.history[-1]['action'], 'Add 2 songs to mix')
        w.undo(); self.assertEqual(w.session.clips, [])
        w.redo(); self.assertEqual([c.track_id for c in w.session.clips], ['b', 'a'])

    def test_find_matches_includes_relative_major_without_transposing(self):
        w = self.w
        w.session.tracks['b'].key = 'C major'
        w.add_track('a', 0)  # A minor
        before = copy.deepcopy(w.session.to_dict())
        w.search.setText('not a song')
        w.simple_song.play_key.matches.click()
        self.assertEqual(w.library_tools.filter.currentData(), 'key')
        self.assertEqual(w.search.text(), '')
        self.assertIn('b', w.library_model.ids)
        self.assertNotIn('c', w.library_model.ids)
        w.library.select_ids(['b']); w.library_tools.update_selection()
        self.assertIn('A minor ↔ C major', w.library_tools.key_info.text())
        self.assertIn('Relative major/minor', w.library_tools.key_info.text())
        self.assertIn('No extra pitch shift', w.library_tools.key_info.text())
        self.assertEqual(w.session.to_dict(), before)
        w.add_track('b', 1)
        self.assertTrue(all(clip.shift == 0 for clip in w.session.clips))
        self.assertIn('No extra pitch shift', w.verdict.text())
        self.assertIsNone(w.key_suggestion)
        self.assertEqual(w.play_key.target.count(), 12)
        self.assertTrue(all('major' in w.play_key.target.itemText(i) for i in range(12)))

    def test_tab_leaves_table_for_batch_and_collection_controls(self):
        w = self.w; w.library.select_ids(['a', 'b']); w.library.setFocus()
        self.app.processEvents()
        QTest.keyClick(w.library, Qt.Key.Key_Backtab)
        self.assertIs(self.app.focusWidget(), w.library_tools.remove)
        QTest.keyClick(w.library_tools.remove, Qt.Key.Key_Backtab)
        self.assertIs(self.app.focusWidget(), w.library_tools.detect)
        QTest.keyClick(w.library_tools.detect, Qt.Key.Key_Backtab)
        self.assertIs(self.app.focusWidget(), w.library_tools.add)
        QTest.keyClick(w.library_tools.add, Qt.Key.Key_Backtab)
        self.assertIs(self.app.focusWidget(), w.library_tools.filter)
        QTest.keyClick(w.library_tools.filter, Qt.Key.Key_Backtab)
        self.assertIs(self.app.focusWidget(), w.library_tools.manage)
        with patch('studio_library_ui.QMenu') as menu:
            QTest.keyClick(w.library_tools.manage, Qt.Key.Key_Space)
        menu.return_value.exec.assert_called_once()

    def key_observation(self, path, winner='D minor'):
        from studio_cache import source_signature
        from studio_key import VERSION, MODEL_ID, KEYS
        candidates = [{'key': name, 'score': 1.0 if name == winner else 0.0} for name in KEYS]
        candidates.sort(key=lambda item: -item['score'])
        return {'version': VERSION, 'model_id': MODEL_ID, 'source': source_signature(path),
                'state': 'estimated', 'reason': 'Listen to confirm.', 'excerpt_seconds': 60.,
                'frames': 2646000, 'candidates': candidates}

    def run_analysis_job(self, identities, *, tempo=False, detector=None):
        w = self.w
        with patch('desktop_app.estimate_track' if tempo else 'studio_key.analyze_key', side_effect=detector):
            with patch.object(w, 'require_models', return_value=True), patch.object(w, 'job') as job:
                w.analyze_library_tracks(identities, tempo=tempo)
            work, finished = job.call_args.args
            work(Mock(), job.call_args.kwargs['item'])
        finished(None)
        return job.call_args

    def test_batch_detection_fills_unknowns_keeps_manual_choices_and_is_undoable(self):
        w = self.w
        w.session.tracks['a'].key, w.session.tracks['a'].key_origin = '', 'pending'
        w.session.tracks['b'].key, w.session.tracks['b'].key_origin = '', 'saved'
        w.session.tracks['c'].key, w.session.tracks['c'].key_origin = '', 'manual'
        w.add_track('a', 0); w.refresh()
        w.library_tools.filter.setCurrentIndex(w.library_tools.filter.findData('analysis'))
        before = copy.deepcopy(w.session.to_dict()); history = len(w.history)
        calls = self.run_analysis_job(['a', 'b', 'c'], detector=lambda path, *_: self.key_observation(path))
        self.assertTrue(calls.kwargs['cancellable'])
        self.assertEqual(w.session.tracks['a'].key, 'D minor')
        self.assertEqual(w.library_tools.filter.currentData(), 'all')
        self.assertEqual(set(w.library.selected_ids()), {'a', 'b', 'c'})
        self.assertEqual(w.session.tracks['b'].key, 'D minor')
        self.assertEqual(w.session.tracks['c'].key, '')
        self.assertEqual(w.session.tracks['c'].key_origin, 'manual')
        self.assertEqual(w.session.clips, Session.from_dict(before).clips)
        self.assertEqual(len(w.history), history + 1)
        after = copy.deepcopy(w.session.to_dict())
        w.undo(); self.assertEqual(w.session.to_dict(), before)
        w.redo(); self.assertEqual(w.session.to_dict(), after)
        w.use_estimated_keys(['c']); self.assertEqual(w.session.tracks['c'].key, 'D minor')
        self.assertEqual(json.loads((w.data / 'library-cache/c.json').read_text())['track']['key'], 'D minor')

    def test_batch_failure_does_not_hide_other_estimates_or_replace_known_key(self):
        w = self.w
        w.session.tracks['a'].key, w.session.tracks['a'].key_origin = '', 'pending'
        w.session.tracks['b'].key_origin = 'manual'
        def detect(path, *_):
            if path.stem == 'c': raise djmix.MixError('Recording could not be decoded')
            return self.key_observation(path)
        with patch.object(w, 'notify') as notice:
            self.run_analysis_job(['a', 'b', 'c'], detector=detect)
        self.assertEqual(w.session.tracks['a'].key, 'D minor')
        self.assertEqual(w.session.tracks['b'].key, 'E minor')
        self.assertEqual(w.session.tracks['c'].key, 'C# minor')
        self.assertIn('Recording could not be decoded', notice.call_args.args[0])
        self.assertIn('Middle', notice.call_args.args[0])
        self.assertEqual(w.session.tracks['c'].key_analysis['state'], 'unavailable')

    def test_model_request_keeps_the_entire_selection_for_retry(self):
        w = self.w
        with patch.object(w, 'require_models', return_value=False) as needed, patch.object(w, 'job') as job:
            w.analyze_library_tracks(['b', 'a'])
        self.assertEqual(needed.call_args.args[0], ['key']); job.assert_not_called()
        with patch.object(w, 'analyze_library_tracks') as retry:
            needed.call_args.args[1]()
        retry.assert_called_once_with(['b', 'a'], tempo=False, kind='keys')

    def test_cancelling_batch_keeps_completed_results(self):
        w = self.w
        for track in w.session.tracks.values(): track.key, track.key_origin = '', 'pending'
        def detect(path, *_):
            if path.stem == 'b': raise djmix.MixError('Operation cancelled.')
            return self.key_observation(path)
        with patch('studio_key.analyze_key', side_effect=detect), \
                patch.object(w, 'require_models', return_value=True), patch.object(w, 'job') as job:
            w.analyze_library_tracks(['a', 'b', 'c'])
            with self.assertRaisesRegex(djmix.MixError, 'Operation cancelled'):
                job.call_args.args[0](Mock(), job.call_args.kwargs['item'])
        with patch.object(w, 'say') as say:
            job.call_args.kwargs['failed_callback']('Operation cancelled.')
        self.assertEqual(w.session.tracks['a'].key, 'D minor')
        self.assertEqual(w.session.tracks['b'].key, '')
        self.assertEqual(w.session.tracks['c'].key_analysis, {})
        self.assertIn('Stopped.', say.call_args.args[0])

    def test_changed_source_never_receives_a_completed_estimate(self):
        w = self.w; w.session.tracks['a'].key = ''
        with patch('studio_key.analyze_key', side_effect=lambda path, *_: self.key_observation(path)), \
                patch.object(w, 'require_models', return_value=True), patch.object(w, 'job') as job:
            w.analyze_library_tracks(['a'])
            job.call_args.args[0](Mock(), job.call_args.kwargs['item'])
        Path(w.session.tracks['a'].path).write_bytes(b'replacement audio')
        with patch.object(w, 'notify') as notice:
            job.call_args.args[1](None)
        self.assertEqual(w.session.tracks['a'].key, '')
        self.assertEqual(w.session.tracks['a'].key_analysis, {})
        self.assertIn('Source changed', notice.call_args.args[0])

    def test_bpm_batch_preserves_arranged_and_manual_bpm_and_cues(self):
        w = self.w; w.add_track('a', 0)
        w.session.tracks['b'].estimated = False
        before = copy.deepcopy(w.session.to_dict())
        def estimate(track, *_):
            track.bpm, track.cue_in, track.cue_out = 129, 4, 110
            track.key_analysis = self.key_observation(track.path)
            track.opening_tempo = {'bpm': 129}
            return track
        self.run_analysis_job(['a', 'b', 'c'], tempo=True, detector=estimate)
        self.assertEqual(w.session.tracks['a'].bpm, 128)
        self.assertEqual(w.session.tracks['a'].cue_in, 0)
        self.assertEqual(w.session.tracks['b'].bpm, 124)
        self.assertEqual(w.session.tracks['c'].bpm, 129)
        self.assertEqual(w.session.tracks['c'].cue_in, 4)
        self.assertEqual(w.session.clips, Session.from_dict(before).clips)
        w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_keyboard_library_remove_restores_songs_clips_and_collections_in_one_undo(self):
        w = self.w; w.add_tracks(['a', 'b'], 0)
        w.session.collections = {'Set': ['a', 'b', 'c']}
        w.refresh(); before = copy.deepcopy(w.session.to_dict()); history = len(w.history)
        w.library.select_ids(['b', 'c']); w.library.setFocus(); self.app.processEvents()
        QTest.keyClick(w.library, Qt.Key.Key_Delete)
        self.assertEqual(set(w.session.tracks), {'a'})
        self.assertEqual([c.track_id for c in w.session.clips], ['a'])
        self.assertEqual(w.session.collections['Set'], ['a'])
        self.assertEqual(len(w.history), history + 1)
        self.assertTrue(all((self.root / f'{i}.wav').exists() for i in ['a', 'b', 'c']))
        after = copy.deepcopy(w.session.to_dict())
        w.undo(); self.assertEqual(w.session.to_dict(), before)
        w.redo(); self.assertEqual(w.session.to_dict(), after)

    def test_remove_button_and_mix_undo_do_not_lose_later_imports(self):
        w = self.w; w.library.select_ids(['c']); w.library_tools.update_selection()
        w.library_tools.remove.click()
        self.assertNotIn('c', w.session.tracks)
        w.undo(); self.assertIn('c', w.session.tracks)
        w.add_track('a', 0)
        path = self.root / 'later.wav'; path.write_bytes(b'later')
        w.receive_track(Track('later', str(path), 'Later import', 120, 128, cue_out=120))
        w.undo(); self.assertIn('later', w.session.tracks)

    def test_batch_menu_enables_detection_reanalysis_accept_and_removal(self):
        w = self.w; w.add_track('a', 0)
        w.session.tracks['a'].key_analysis = self.key_observation(w.session.tracks['a'].path)
        w.refresh(); w.library.select_ids(['a', 'b'])
        captured = []
        class TestMenu(QMenu):
            def exec(self, *_):
                captured.extend((a.text(), a.isEnabled()) for a in self.actions())
                return None
        with patch('desktop_app.QMenu', TestMenu):
            w.library_menu(w.library.visualRect(w.library_model.index(0, 0)).center())
        for text in ['Analyze BPM and key again', 'Detect keys for 2 selected songs',
                     'Use detected keys for selected songs', 'Remove selected songs from library and mix']:
            self.assertIn((text, True), captured)

    def test_common_pitch_settings_apply_all_selected_uses_and_keep_source_modes(self):
        from studio_library_batch import clip_value
        w = self.w; w.add_tracks(['a', 'b'], 0); w.add_track('a', 2)
        before = copy.deepcopy(w.session.to_dict())
        clip_value(w, ['a'], 'pitch', 2)
        self.assertEqual([c.shift for c in w.session.clips], [2, 0, 2])
        self.assertEqual(w.session.tracks['a'].key, 'A minor')
        w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_common_source_labels_do_not_transpose_or_move_cues(self):
        from studio_library_batch import labels
        w = self.w; w.add_tracks(['a', 'b'], 0)
        clips = copy.deepcopy(w.session.clips)
        with patch('PySide6.QtWidgets.QInputDialog.getItem', return_value=('C major', True)):
            labels(w, ['a', 'b'], key=True)
        self.assertEqual([w.session.tracks[i].key for i in ['a', 'b']], ['C major', 'C major'])
        self.assertEqual(w.session.clips, clips)
        self.assertTrue(all(w.session.tracks[i].key_origin == 'manual' for i in ['a', 'b']))

    def test_blend_settings_skip_first_song_without_an_incoming_blend(self):
        from studio_library_batch import clip_value
        w = self.w; w.add_tracks(['a', 'b', 'c'], 0)
        before = copy.deepcopy(w.session.to_dict())
        clip_value(w, ['a', 'b'], 'balance_levels', True)
        self.assertEqual([c.balance_levels for c in w.session.clips], [False, True, False])
        w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_passage_batch_stores_each_result_without_changing_keys_bpm_or_cues(self):
        from studio_cache import source_signature
        w = self.w; w.add_tracks(['a', 'b'], 0)
        before = copy.deepcopy(w.session.to_dict())
        def detect(track, *_): return {'source': source_signature(track.path), 'windows': []}
        with patch.object(w, 'require_models', return_value=True), patch.object(w, 'job') as job, \
                patch('studio_library_batch.observe', side_effect=detect):
            w.analyze_library_tracks(['a', 'b'], kind='content')
            job.call_args.args[0](Mock(), job.call_args.kwargs['item'])
        job.call_args.args[1](None)
        self.assertTrue(all(w.session.tracks[i].content for i in ['a', 'b']))
        self.assertEqual(w.session.clips, Session.from_dict(before).clips)
        self.assertEqual([w.session.tracks[i].key for i in ['a', 'b']], ['A minor', 'E minor'])
        self.assertEqual([row['state'] for row in w.last_library_batch['rows']], ['Ready', 'Ready'])
        w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_space_activates_focused_controls_but_keeps_timeline_transport_shortcut(self):
        w = self.w; transport = Mock()
        w.act_play.triggered.disconnect(); w.act_play.triggered.connect(transport)
        w.library_tools.manage.setFocus(); self.app.processEvents()
        with patch('studio_library_ui.QMenu') as menu:
            QTest.keyClick(w.library_tools.manage, Qt.Key.Key_Space)
        menu.return_value.exec.assert_called_once(); transport.assert_not_called()
        w.library.select_ids(['a', 'b']); w.library_tools.add.setFocus()
        QTest.keyClick(w.library_tools.add, Qt.Key.Key_Space)
        self.assertEqual([c.track_id for c in w.session.clips], ['a', 'b'])
        transport.assert_not_called()
        w.timeline.setFocus(); self.app.processEvents()
        QTest.keyClick(w.timeline, Qt.Key.Key_Space)
        transport.assert_called_once()

    def test_delete_only_removes_a_clip_when_arrangement_has_focus(self):
        w = self.w; w.add_track('a', 0); before = copy.deepcopy(w.session.to_dict())
        history = len(w.history)
        w.library.setFocus(); self.app.processEvents()
        for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            QTest.keyClick(w.library, key)
            self.assertEqual(w.session.to_dict(), before)
        self.assertEqual(len(w.history), history)
        w.search.setText('song'); w.search.setFocus(); w.search.setCursorPosition(4)
        QTest.keyClick(w.search, Qt.Key.Key_Backspace)
        self.assertEqual(w.search.text(), 'son'); self.assertEqual(w.session.to_dict(), before)
        w.timeline.setFocus(); self.app.processEvents()
        QTest.keyClick(w.timeline, Qt.Key.Key_Delete)
        self.assertEqual(w.session.clips, [])
        w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_drag_batch_inserts_together_at_target_and_rejects_bad_payload(self):
        w = self.w; w.add_track('c', 0); before = copy.deepcopy(w.session.to_dict())
        w.library.select_ids(['a', 'b'])
        mime = w.library_model.mimeData(w.library.selectionModel().selectedIndexes())
        self.assertEqual(json.loads(bytes(mime.data(TRACKS_MIME))), ['a', 'b'])
        event = QDropEvent(QPointF(1, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        w.timeline.dropEvent(event)
        self.assertEqual([c.track_id for c in w.session.clips], ['a', 'b', 'c'])
        w.undo(); self.assertEqual(w.session.to_dict(), before)
        mime.setData(TRACKS_MIME, b'not json')
        event = QDropEvent(QPointF(1, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        w.timeline.dropEvent(event)
        self.assertFalse(event.isAccepted()); self.assertEqual(w.session.to_dict(), before)

    def test_invalid_batch_and_missing_bpm_never_partly_insert(self):
        w = self.w; w.add_track('a', 0)
        w.session.tracks['c'].cue_out = 2
        before = copy.deepcopy(w.session.to_dict()); history = len(w.history)
        w.add_tracks(['b', 'c'], 1)
        self.assertEqual(w.session.to_dict(), before); self.assertEqual(len(w.history), history)
        w.session.tracks['c'].bpm = None
        before = copy.deepcopy(w.session.to_dict())
        with patch.object(w, 'notify') as notify:
            w.add_tracks(['b', 'c'], 1)
        self.assertIn('No songs added', notify.call_args.args[0]); self.assertIn('Middle', notify.call_args.args[0])
        self.assertEqual(w.session.to_dict(), before)
        with patch('desktop_app.QInputDialog.getDouble', return_value=(128, True)):
            w.add_track('c', 1)
        self.assertEqual(w.session.to_dict(), before, 'Rejected single insertion must restore entered BPM too')

    def test_filters_compose_without_hidden_selection_or_mix_edits(self):
        w = self.w; w.add_track('a', 0)
        w.session.collections = {'Warmup': ['a', 'b']}; w.refresh()
        before = copy.deepcopy(w.session.to_dict()); history = len(w.history)
        w.library.select_ids(['a', 'b', 'c'])
        w.library_tools.collection.setCurrentIndex(w.library_tools.collection.findData('Warmup'))
        self.assertEqual(w.library.selected_ids(), ['a', 'b'])
        w.library_tools.filter.setCurrentIndex(w.library_tools.filter.findData('unused'))
        self.assertEqual(w.library_model.ids, ['b']); self.assertEqual(w.library.selected_ids(), ['b'])
        w.search.setText('9A'); self.assertEqual(w.library_model.ids, ['b'])
        w.search.setText('no match'); self.assertEqual(w.library.selected_ids(), [])
        self.assertFalse(w.library_tools.add.isEnabled()); self.assertIn('No matching', w.library_tools.hint.text())
        w.search.clear(); self.assertEqual(w.library.selected_ids(), [], 'Previously hidden songs must not be reselected')
        w.library_tools.collection.setCurrentIndex(0)
        for kind, expected in [('key', ['a', 'b']), ('tempo', ['a', 'b']), ('used', ['a']), ('analysis', [])]:
            w.library_tools.filter.setCurrentIndex(w.library_tools.filter.findData(kind))
            self.assertEqual(w.library_model.ids, expected)
        Path(w.session.tracks['c'].path).unlink()
        w.library_tools.filter.setCurrentIndex(w.library_tools.filter.findData('missing'))
        self.assertEqual(w.library_model.ids, ['c'])
        self.assertEqual(w.session.to_dict(), before); self.assertEqual(len(w.history), history)

    def test_collections_persist_manage_members_and_undo_without_removing_sources(self):
        w = self.w; w.library.select_ids(['a', 'b'])
        with patch('studio_library_ui.QInputDialog.getText', return_value=('Warmup', True)):
            w.library_tools.create()
        self.assertEqual(w.session.collections, {'Warmup': ['a', 'b']})
        project = self.root / 'sets.djmix'; w.session.save(project)
        restored = Session.from_dict(json.loads(project.read_text()))
        self.assertEqual(restored.collections, w.session.collections)
        w.library.select_ids(['b']); w.library_tools.remove_selected()
        self.assertEqual(w.session.collections, {'Warmup': ['a']})
        self.assertEqual(len(w.session.tracks), 3)
        w.undo(); self.assertEqual(w.session.collections, {'Warmup': ['a', 'b']})
        with patch('studio_library_ui.QInputDialog.getText', return_value=('Opening set', True)):
            w.library_tools.rename()
        self.assertEqual(w.library_tools.collection.currentData(), 'Opening set')
        w.library_tools.delete(); self.assertEqual(w.session.collections, {})
        w.undo(); self.assertEqual(w.session.collections, {'Opening set': ['a', 'b']})
        w.library.select_ids(['c']); w.library_tools.add_to('Opening set'); w.library_tools.add_to('Opening set')
        self.assertEqual(w.session.collections['Opening set'], ['a', 'b', 'c'])
        self.assertTrue(all(Path(t.path).exists() for t in w.session.tracks.values()))
        legacy = w.session.to_dict(); legacy['version'] = 6; legacy.pop('collections')
        self.assertEqual(Session.from_dict(legacy).collections, {})
        invalid = copy.deepcopy(w.session.to_dict()); invalid['collections'] = {'Bad': ['absent']}
        with self.assertRaisesRegex(djmix.MixError, 'collection'): Session.from_dict(invalid)

    def test_duplicate_import_preserves_edits_and_skips_analysis(self):
        w = self.w; original = self.root / 'original.wav'
        sf.write(original, np.sin(np.arange(8000) * .1).astype('float32') * .1, 8000)
        track = load_track(original, self.root / 'library-cache')
        track.bpm, track.key, track.cue_in, track.status = 126, 'A minor', .1, 'My corrected source'
        w.session = Session(tracks={track.id: track}); w.refresh()
        copied = self.root / 'copy.wav'; shutil.copyfile(original, copied)
        before = copy.deepcopy(w.session.to_dict())
        with patch.object(w, 'job') as job: w.import_paths([original, copied, copied])
        with patch('desktop_app.load_track', wraps=load_track) as load, patch('desktop_app.estimate_track') as estimate:
            published = []; result = job.call_args.args[0](lambda _: None, published.append)
        self.assertEqual(result, (0, [])); self.assertEqual(published, [])
        load.assert_not_called(); estimate.assert_not_called()
        with patch.object(w, 'say') as say: job.call_args.args[1](result)
        self.assertIn('Skipped 3', say.call_args.args[0])
        self.assertEqual(w.session.to_dict(), before)
        self.assertTrue(original.exists() and copied.exists())

    def test_missing_or_changed_source_is_not_treated_as_usable_duplicate(self):
        w = self.w; original = self.root / 'original.wav'; copied = self.root / 'copy.wav'
        original.write_bytes(b'original audio'); copied.write_bytes(original.read_bytes())
        a = Track('old', str(original), 'Original', 120, 128, fingerprint=fingerprint(original))
        b = Track('new', str(copied), 'Copy', 120, 128, fingerprint=fingerprint(copied))
        self.assertIs(duplicate_track(b, {'old': a}), a)
        original.unlink(); self.assertIsNone(duplicate_track(b, {'old': a}))
        original.write_bytes(b'changed bytes'); self.assertIsNone(duplicate_track(b, {'old': a}))
        w.session = Session(tracks={'old': a}); w.refresh()
        changed = Track('old', str(original), 'Changed', 120, 120, fingerprint=fingerprint(original))
        with patch.object(w, 'job') as job: w.import_paths([original])
        with patch('desktop_app.load_track', return_value=changed):
            published = []; count, errors = job.call_args.args[0](lambda _: None, published.append)
        self.assertEqual(count, 0); self.assertFalse(published)
        self.assertIn('Reconnect source audio', errors[0]); self.assertEqual(w.session.tracks['old'].bpm, 128)


if __name__ == '__main__': unittest.main()
