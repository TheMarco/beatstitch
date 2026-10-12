"""Project browsing must never sacrifice the active mix or overwrite recovery originals."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtTest import QTest
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication

from desktop_app import Studio
from studio_engine import Clip, Session, Track
from studio_projects import (inspect_project, is_recovery, project_candidates, recent_paths,
                             remember_path, write_snapshot)


class ProjectsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(self.root); self.addCleanup(self.close)
        audio = self.root / 'song.wav'; audio.write_bytes(b'fixture')
        track = Track('a', str(audio), 'Original song', 120, 120, 'C minor', cue_out=120)
        self.w.session = Session(name='Current mix', tracks={'a': track}, clips=[Clip('a', 0, 120)])
        self.w.selected_id = self.w.session.clips[0].id; self.w.dirty = True; self.w.autosave(); self.w.refresh()

    def settle(self):
        deadline = time.monotonic() + 10
        while self.w.jobs and time.monotonic() < deadline: QTest.qWait(10)
        self.assertFalse(self.w.jobs, 'Project scan did not finish')
        self.app.processEvents()

    def close(self):
        if self.w.projects_dialog: self.w.projects_dialog.reject()
        self.settle(); self.w.close(); self.app.processEvents()

    def test_recent_mru_deduplicates_and_only_successful_open_or_save_registers(self):
        a, b = self.root / 'a.djmix', self.root / 'b.djmix'
        self.w.write_project(a); self.w.write_project(b); self.w.open_project_path(a)
        self.assertEqual(recent_paths(self.w.prefs), [str(a), str(b)])
        self.w.open_project_path(self.root / 'absent.djmix')
        self.assertEqual(recent_paths(self.w.prefs), [str(a), str(b)])
        prefs = {'recent_projects': [None, str(a), str(a)], 'last_project': str(b)}
        self.assertEqual(remember_path(prefs, b), [str(b), str(a)])
        self.assertEqual(len(remember_path({'recent_projects': [str(self.root / f'{n}.djmix') for n in range(40)]}, a)), 20)

    def test_import_marks_saved_project_edited_and_switching_preserves_library_changes(self):
        saved = self.root / 'Saved.djmix'; self.w.write_project(saved)
        original = saved.read_bytes()
        source = self.root / 'imported.wav'; source.write_bytes(b'fixture')
        self.w.receive_track(Track('imported', str(source), 'Imported song', 90, 128, cue_out=90))
        self.assertTrue(self.w.dirty)
        self.assertEqual(saved.read_bytes(), original)
        self.w.new_project()
        copies = [json.loads(p.read_text()) for p in self.root.glob('recovery-*.djmix')]
        self.assertTrue(any('imported' in value['tracks'] and len(value['clips']) == 1
                            and value['_recovery_project'] == str(saved) for value in copies))

    def test_import_only_work_remains_unsaved_after_restart_and_can_be_recovered(self):
        track = copy.deepcopy(self.w.session.tracks['a'])
        self.w.session = Session(); self.w.project_path = None; self.w.dirty = False
        self.w.receive_track(track); self.w.autosave(); self.w.close()
        self.w = Studio(self.root)
        self.assertFalse(self.w.session.clips)
        self.assertEqual(set(self.w.session.tracks), {'a'})
        self.assertTrue(self.w.dirty)
        self.w.new_project()
        copies = [json.loads(p.read_text()) for p in self.root.glob('recovery-*.djmix')]
        self.assertTrue(any(set(value['tracks']) == {'a'} and not value['clips'] for value in copies))

    def test_invalid_open_preserves_mix_history_playhead_and_autosave(self):
        broken = self.root / 'broken.djmix'; broken.write_text('{')
        self.w.set_clip_value('cue_in', 4); self.w.mix_cursor = 23
        before = copy.deepcopy(self.w.session.to_dict()), copy.deepcopy(self.w.history)
        autosave = (self.root / 'autosave.djmix').read_bytes()
        with patch.object(self.w, 'reset_listening') as reset:
            self.assertFalse(self.w.open_project_path(broken)); reset.assert_not_called()
        self.assertEqual(before, (self.w.session.to_dict(), self.w.history))
        self.assertEqual(self.w.mix_cursor, 23)
        self.assertEqual((self.root / 'autosave.djmix').read_bytes(), autosave)
        self.assertFalse(list(self.root.glob('recovery-*.djmix')))

    def test_recovery_opens_unsaved_with_original_and_current_work_preserved(self):
        original = self.root / 'Named mix.djmix'; self.w.write_project(original)
        saved_bytes = original.read_bytes()
        self.w.set_clip_value('cue_in', 4)
        recovery = self.w.keep_recovery_copy(); recovery_bytes = recovery.read_bytes()
        self.w.set_clip_value('cue_in', 8)
        self.assertTrue(self.w.open_project_path(recovery))
        self.assertIsNone(self.w.project_path); self.assertTrue(self.w.dirty)
        self.assertEqual(self.w.session.clips[0].cue_in, 4)
        self.assertEqual(original.read_bytes(), saved_bytes); self.assertEqual(recovery.read_bytes(), recovery_bytes)
        prior = [json.loads(p.read_text()) for p in self.root.glob('recovery-*.djmix') if p != recovery]
        self.assertTrue(any(v['clips'][0]['cue_in'] == 8 and v['_recovery_project'] == str(original) for v in prior))
        self.assertIsNone(json.loads((self.root / 'autosave.djmix').read_text())['_recovery_project'])
        with patch.object(self.w, 'save_project_as') as save_as:
            self.w.save_project(); save_as.assert_called_once()

    def test_failed_recovery_write_blocks_new_and_open_without_losing_work(self):
        other = self.root / 'other.djmix'; Session().save(other)
        before = copy.deepcopy(self.w.session.to_dict())
        with patch('studio_projects.write_snapshot', side_effect=OSError('Disk full')):
            self.w.new_project(); self.assertEqual(self.w.session.to_dict(), before)
            self.assertFalse(self.w.open_project_path(other))
            self.assertEqual(self.w.session.to_dict(), before)
        self.assertIn('Disk full', self.w.project_error)

    def test_failed_save_preserves_name_and_reports_autosave_failure(self):
        before = copy.deepcopy(self.w.session.to_dict())
        with patch.object(Session, 'save', side_effect=OSError('Read only')):
            self.assertFalse(self.w.write_project(self.root / 'new name.djmix'))
        self.assertEqual(self.w.session.to_dict(), before); self.assertIsNone(self.w.project_path)
        with patch('studio_projects.write_snapshot', side_effect=OSError('Disk full')): self.w.autosave()
        self.assertIn('Autosave failed', self.w.save_state.text())
        self.assertIn('Autosave failed', QAccessible.queryAccessibleInterface(self.w.save_state).text(QAccessible.Text.Name))
        self.assertIn('Disk full', self.w.project_status_text())
        self.w.autosave(); self.assertIn('Autosaved', self.w.save_state.text())
        self.assertIn('Not saved', QAccessible.queryAccessibleInterface(self.w.save_state).text(QAccessible.Text.Name))

    def test_launching_another_project_preserves_previous_autosave(self):
        previous = self.w.session.to_dict()
        target = self.root / 'Another project.djmix'; Session(name='Another project').save(target)
        self.w.close()
        self.w = Studio(self.root, project=target)
        self.assertEqual(self.w.project_path, target)
        copies = [Session.from_dict(json.loads(p.read_text())).to_dict() for p in self.root.glob('recovery-*.djmix')]
        self.assertIn(previous, copies)

    def test_failed_launch_resumes_previous_mix_and_unpreservable_corruption_is_not_overwritten(self):
        previous = self.w.session.to_dict(); self.w.close()
        self.w = Studio(self.root, project=self.root / 'missing.djmix')
        self.assertEqual(self.w.session.to_dict(), previous)
        self.w.close(); autosave = self.root / 'autosave.djmix'; autosave.write_bytes(b'broken file')
        with patch.object(Path, 'rename', side_effect=OSError('Cannot preserve file')):
            self.w = Studio(self.root)
        self.assertTrue(self.w.autosave_blocked)
        self.w.autosave(); self.assertEqual(autosave.read_bytes(), b'broken file')

    def test_inspection_includes_unreadable_copies_and_missing_audio_without_deleting(self):
        broken = self.root / 'autosave-unreadable-1.djmix'; broken.write_text('[]')
        legacy = self.root / 'recovery-2.djmix'; self.w.session.save(legacy)
        Path(self.w.session.tracks['a'].path).unlink()
        candidates = project_candidates(self.root, {}, 'recovery')
        self.assertEqual(set(candidates), {broken, legacy, self.root / 'autosave.djmix'})
        self.assertFalse(inspect_project(broken, 'recovery')['ready'])
        valid = inspect_project(legacy, 'recovery'); self.assertTrue(valid['ready']); self.assertEqual(valid['missing'], 1)
        self.assertTrue(broken.exists()); self.assertTrue(is_recovery(legacy, self.root))
        self.assertFalse(is_recovery(self.root / 'elsewhere/recovery-2.djmix', self.root))

    def test_browser_loads_pages_filters_and_recovers_without_overwrite(self):
        original = self.root / 'Saved.djmix'; self.w.write_project(original)
        for i in range(23):
            write_snapshot(self.w.session, self.root / f'recovery-{i}.djmix', original, 'Before trying the demo mix')
        self.w.show_projects(); d = self.w.projects_dialog; self.settle()
        self.assertEqual(len(d.records), 1)
        self.assertIn('Project file is saved', d.current.text())
        d.tabs.setCurrentIndex(1); self.settle()
        self.assertEqual(len(d.records), 20); self.assertFalse(d.more.isHidden())
        d.more.click(); self.settle(); self.assertEqual(len(d.records), 24)
        d.search.setText('recovery-5.djmix')
        rows = [r for r in d.rows if not r.isHidden()]; self.assertEqual(len(rows), 1)
        self.assertIn('Before trying the demo mix', rows[0].text.text())
        self.assertIn('Before trying the demo mix', rows[0].open.accessibleName())
        original_bytes = original.read_bytes(); rows[0].open.click(); self.settle()
        self.assertTrue(d.closed); self.assertIsNone(self.w.project_path)
        self.assertEqual(original.read_bytes(), original_bytes)

    def test_cancel_browser_scan_cannot_change_project(self):
        for i in range(23): write_snapshot(self.w.session, self.root / f'recovery-{i}.djmix')
        before = copy.deepcopy(self.w.session.to_dict())
        self.w.show_projects(); d = self.w.projects_dialog
        d.reject(); self.settle()
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertTrue(d.closed); self.assertIsNone(self.w.projects_dialog)


if __name__ == '__main__': unittest.main()
