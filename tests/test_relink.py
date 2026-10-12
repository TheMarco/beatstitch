"""Relinking is a reviewable source edit, including undo and late-file checks."""
import copy
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import soundfile as sf
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop_app import Studio
from studio_engine import Clip, Session, load_track

LOAD_BANDS = Studio.load_bands


class RelinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(self.root); self.addCleanup(self.close)
        old = self.root / 'Song.wav'; sf.write(old, .1 * np.sin(np.arange(12 * 8000) * 2 * np.pi * 440 / 8000), 8000)
        track = load_track(old, self.root / 'library-cache'); track.bpm = 120; track.key = 'C minor'
        self.identity = track.id; self.moved = self.root / 'Moved.wav'; old.rename(self.moved)
        self.w.session = Session(tracks={track.id: track}, clips=[Clip(track.id, 1, 11, semitones=2, transpose=True,
            protected_moments=[{'start': 3, 'end': 8, 'kind': 'vocal', 'label': 'Vocal'}])])
        self.w.selected_id = self.w.session.clips[0].id; self.w.refresh()

    def settle(self):
        end = time.monotonic() + 10
        while (self.w.jobs or self.w.band_workers) and time.monotonic() < end: QTest.qWait(10)
        self.assertFalse(self.w.jobs or self.w.band_workers); self.app.processEvents()

    def close(self):
        if self.w.relink_dialog: self.w.relink_dialog.reject()
        self.settle(); self.w.close(); self.app.processEvents()

    def check(self):
        self.w.show_relink(self.identity); d = self.w.relink_dialog
        d.check_file(self.moved); self.settle(); return d

    def test_verified_apply_undo_and_redo_preserve_clip_edits(self):
        track = self.w.session.tracks[self.identity]
        track.content = {'source': copy.deepcopy(track.fingerprint['source']), 'windows': []}
        track.song_sections = {'source': copy.deepcopy(track.fingerprint['source']), 'regions': []}
        before = copy.deepcopy(self.w.session.to_dict()); d = self.check()
        self.assertEqual(self.w.session.to_dict(), before)
        self.assertTrue(d.apply.isEnabled()); self.assertTrue(d.confirm.isHidden())
        d.apply.click()
        self.assertEqual(self.w.session.tracks[self.identity].path, str(self.moved))
        self.assertEqual(self.w.session.tracks[self.identity].content['source'], self.w.session.tracks[self.identity].fingerprint['source'])
        self.assertEqual(self.w.session.tracks[self.identity].song_sections['source'], self.w.session.tracks[self.identity].fingerprint['source'])
        self.assertEqual(self.w.session.clips, Session.from_dict(before).clips)
        self.assertEqual(len(self.w.history), 1); self.assertIn('Reconnect audio', self.w.history[-1]['action'])
        connected = copy.deepcopy(self.w.session.to_dict())
        self.w.undo(); self.assertEqual(self.w.session.to_dict(), before)
        self.w.redo(); self.assertEqual(self.w.session.to_dict(), connected)

    def test_legacy_confirmation_and_cancel(self):
        self.w.session.tracks[self.identity].fingerprint = {}
        before = copy.deepcopy(self.w.session.to_dict()); d = self.check()
        self.assertFalse(d.apply.isEnabled()); self.assertFalse(d.confirm.isHidden())
        d.confirm.setChecked(True); self.assertTrue(d.apply.isEnabled())
        d.reject(); self.assertEqual(self.w.session.to_dict(), before)

    def test_changed_candidate_cannot_be_applied(self):
        before = copy.deepcopy(self.w.session.to_dict()); d = self.check()
        with self.moved.open('ab') as handle: handle.write(b'changed')
        d.apply.click()
        self.assertIn('changed after verification', d.status.text())
        self.assertEqual(self.w.session.to_dict(), before); self.assertFalse(self.w.history)

    def test_late_waveform_from_previous_location_is_ignored(self):
        d = self.check(); old_path = self.w.session.tracks[self.identity].path; d.apply.click()
        self.w.band_pending.add(self.identity)
        self.w.receive_bands((self.identity, old_path, np.ones((3, 3))))
        self.assertNotIn(self.identity, self.w.bands); self.assertIn(self.identity, self.w.band_pending)
        self.w.receive_bands((self.identity, str(self.moved), np.zeros((3, 3))))
        self.assertNotIn(self.identity, self.w.band_pending); self.assertEqual(self.w.bands[self.identity].shape, (3, 3))

    def test_close_keeps_workers_alive_until_waveform_load_finishes(self):
        self.w.session.tracks[self.identity].path = str(self.moved)
        started, release = threading.Event(), threading.Event()
        def waveform(*args):
            started.set(); release.wait(5); return np.zeros((3, 3))
        with patch('desktop_app.band_envelope', side_effect=waveform):
            LOAD_BANDS(self.w)
            self.assertTrue(started.wait(2)); self.w.close()
            self.assertTrue(self.w.closing); self.assertTrue(self.w.band_workers)
            self.assertIsNotNone(self.w.storage.pin_lock)
            release.set(); self.settle()
        self.assertIsNone(self.w.storage.pin_lock)


if __name__ == '__main__': unittest.main()
