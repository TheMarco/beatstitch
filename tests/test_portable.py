"""Portable projects must work after moving the folder and losing original paths."""
import copy
import json
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
import djmix
from desktop_app import Studio
from studio_engine import Clip, Session, load_track, render_session
from studio_media import create_portable_copy, portable_value
from studio_projects import read_project


class PortableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.cache = self.root / 'cache'; self.cache.mkdir()
        self.source = self.root / 'Original.wav'
        sf.write(self.source, .1 * np.sin(np.arange(8 * 8000) * 2 * np.pi * 440 / 8000), 8000)
        track = load_track(self.source, self.cache); track.bpm = 120; track.key = 'C minor'
        track.timing = {'version': 1, 'state': 'drift', 'source': track.fingerprint['source'],
                        'map': [[0, 0], [4, 4.02], [8, 8]]}
        self.session = Session(tracks={track.id: track}, clips=[Clip(track.id, 0, 8, stabilize=True)])
        self.identity = track.id; self.destination = self.root / 'Portable'

    def test_copy_moves_and_renders_without_original_and_saves_relatively_again(self):
        track = self.session.tracks[self.identity]
        track.content = {'source': copy.deepcopy(track.fingerprint['source']), 'windows': []}
        before = copy.deepcopy(self.session.to_dict())
        result = create_portable_copy(self.session, self.destination)
        self.assertEqual(self.session.to_dict(), before)
        value = json.loads(Path(result['project']).read_text())
        self.assertEqual(value['_portable_paths'], 1)
        self.assertTrue(value['tracks'][self.identity]['path'].startswith('media/'))
        moved = self.root / 'Moved to another machine'; self.destination.rename(moved); self.source.unlink()
        loaded, _ = read_project(moved / 'Mix.djmix')
        self.assertEqual(loaded.clips, self.session.clips)
        track = loaded.tracks[self.identity]
        self.assertTrue(Path(track.path).is_relative_to(moved / 'media'))
        self.assertEqual(track.timing['source'], track.fingerprint['source'])
        self.assertEqual(track.content['source'], track.fingerprint['source'])
        output = self.root / 'Portable render.wav'; rendered = render_session(loaded, output)
        self.assertAlmostEqual(rendered['duration'], 7.5, places=4)
        self.assertLessEqual(rendered['true_peak_db'], -.99)
        encoded = portable_value(loaded, moved / 'Mix.djmix')
        (moved / 'Mix.djmix').write_text(json.dumps(encoded)); again = self.root / 'Moved again'; moved.rename(again)
        read_project(again / 'Mix.djmix')

    def test_changed_audio_and_escape_paths_are_rejected(self):
        result = create_portable_copy(self.session, self.destination); project = Path(result['project'])
        value = json.loads(project.read_text()); media = self.destination / value['tracks'][self.identity]['path']
        with media.open('ab') as handle: handle.write(b'changed')
        with self.assertRaisesRegex(djmix.MixError, 'Bundled audio changed'): read_project(project)
        value['tracks'][self.identity]['path'] = '../Original.wav'; project.write_text(json.dumps(value))
        with self.assertRaisesRegex(djmix.MixError, 'inside its media folder'): read_project(project, verify_media=False)

    def test_cancellation_existing_destination_and_low_space_preserve_inputs(self):
        original = self.source.read_bytes()
        def stop(message):
            if message.startswith('Publishing'): raise djmix.MixError('Operation cancelled.')
        with self.assertRaisesRegex(djmix.MixError, 'cancelled'): create_portable_copy(self.session, self.destination, stop)
        self.assertFalse(self.destination.exists()); self.assertFalse(list(self.root.glob('.dj-portable-*')))
        with patch('studio_storage.require_space', side_effect=djmix.MixError('Not enough free space')):
            with self.assertRaisesRegex(djmix.MixError, 'free space'): create_portable_copy(self.session, self.destination)
        self.assertFalse(self.destination.exists())
        self.destination.mkdir(); sentinel = self.destination / 'keep.txt'; sentinel.write_text('keep')
        with self.assertRaisesRegex(djmix.MixError, 'already exists'): create_portable_copy(self.session, self.destination)
        self.assertEqual(sentinel.read_text(), 'keep'); self.assertEqual(self.source.read_bytes(), original)

    def test_shared_source_copied_once_and_missing_source_stays_relinkable(self):
        twin = copy.deepcopy(self.session.tracks[self.identity]); twin.id = 'second'; self.session.tracks[twin.id] = twin
        result = create_portable_copy(self.session, self.destination)
        self.assertEqual(result['files'], 1); self.assertEqual(len(list((self.destination / 'media').iterdir())), 1)
        for source in (self.destination / 'media').iterdir(): source.unlink()
        loaded, _ = read_project(result['project'])
        self.assertEqual(len(loaded.tracks), 2)
        self.assertFalse(Path(loaded.tracks[self.identity].path).exists())

    def test_replaced_original_is_not_silently_packed_as_the_saved_recording(self):
        sf.write(self.source, np.zeros(8 * 8000), 8000)
        with self.assertRaisesRegex(djmix.MixError, 'saved audio identity'):
            create_portable_copy(self.session, self.destination)
        self.assertFalse(self.destination.exists())

    def test_native_dialog_creates_copy_and_open_can_be_cancelled_before_commit(self):
        with patch.object(Studio, 'load_bands'):
            w = Studio(self.root / 'app-data')
        def close_window():
            w.close()
            end = time.monotonic() + 10
            while (w.jobs or w.band_workers) and time.monotonic() < end: QTest.qWait(10)
            self.assertFalse(w.jobs or w.band_workers)
        self.addCleanup(close_window); w.session = copy.deepcopy(self.session); w.refresh()
        before = w.session.to_dict(); w.show_portable(); d = w.portable_dialog
        d.set_destination(self.root); d.create.click()
        def settle():
            deadline = time.monotonic() + 15
            while w.jobs and time.monotonic() < deadline: QTest.qWait(10)
            self.assertFalse(w.jobs); self.app.processEvents()
        settle(); self.assertIsNotNone(d.result); self.assertEqual(w.session.to_dict(), before)
        target = Path(d.result['project']); d.accept()
        delivered = []
        # Cancellation after the worker has returned but before Qt delivers its result.
        cancel = w.open_project_async(target, finished=delivered.append)
        self.assertTrue(w.pool.waitForDone(10000))
        cancel.set(); settle()
        self.assertEqual(w.session.to_dict(), before); self.assertEqual(delivered, [False])
        w.open_project_async(target, finished=delivered.append); settle(); self.assertTrue(delivered[-1])
        self.assertTrue(w.write_project(target)); self.assertEqual(json.loads(target.read_text())['_portable_paths'], 1)
        w.close()


if __name__ == '__main__': unittest.main()
