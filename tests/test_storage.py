"""Disposable cache cleanup must respect live readers, jobs and user files."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from filelock import Timeout

import djmix
from studio_storage import StorageManager, managed_cache, require_space, work_scope


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = StorageManager(self.root)
        self.addCleanup(self.manager.close)

    def file(self, name, content=b'cached audio'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def entry(self, key):
        directory = Path('renders') / (key * 64)
        self.file(directory / 'report.json', b'{}')
        return self.file(directory / 'audio.wav')

    def test_clear_covers_all_owned_families_but_preserves_user_files_and_symlinks(self):
        files = [self.entry('a'), self.file('renders/preview-123.wav')]
        for kind in ('auditions', 'transitions', 'grid-auditions', 'alternatives'):
            directory = Path('audio-cache') / kind / ('b' * 64)
            self.file(directory / 'report.json', b'{}')
            files.append(self.file(directory / 'audio.wav'))
            # An interrupted file-by-file publication has no final report yet.
            files.append(self.file(Path('audio-cache') / kind / ('7' * 64) / 'audio.wav'))
        directory = Path('audio-cache/audio') / ('c' * 64) / ('d' * 32)
        self.file(directory / 'entry.json', b'{}')
        files.append(self.file(directory / 'low.raw'))
        files.append(self.file(Path('audio-cache/assembled') / ('e' * 64 + '.raw')))
        files.append(self.file(Path('audio-cache/effects') / ('3' * 64 + '.raw')))
        files.append(self.file(Path('audio-cache/effects') / ('4' * 64 + '.' + '5' * 32 + '.tmp')))
        for root in ('library-cache', 'structure-cache', 'timing-cache', 'audio-cache/levels'):
            files.append(self.file(root + '/observation.json', b'{}'))
        files.append(self.file('renders/dj-render-interrupted/scratch.raw'))
        files.append(self.file('timing-cache/.pending-timing-interrupted/forward.raw'))
        files.append(self.file('structure-cache/.pending-structure-interrupted/audio.raw'))
        files.append(self.file('timing-cache/' + 'a' * 64 + '.' + 'b' * 32 + '.tmp'))
        user_files = [self.file('renders/my-export.wav'), self.file('autosave.djmix'),
                      self.file('assets/models/model.ckpt'), self.file('songs/original.wav')]
        project_folder = Path('renders') / ('f' * 64)
        user_files.append(self.file(project_folder / 'project.djmix'))
        user_files.append(self.file(project_folder / 'audio.wav'))
        link = self.root / 'renders' / ('9' * 64)
        link.symlink_to(user_files[0].parent.parent / 'songs', target_is_directory=True)
        report = self.manager.prune(clear=True)
        self.assertFalse(report['busy'])
        self.assertGreater(report['removed'], 0)
        self.assertTrue(all(not p.exists() for p in files))
        self.assertTrue(all(p.is_file() for p in user_files))
        self.assertTrue(link.is_symlink())

    def test_live_reader_pin_survives_other_instance_cleanup_and_then_releases(self):
        playing, unused = self.entry('a'), self.entry('b')
        self.manager.pin([playing])
        other = StorageManager(self.root)
        other.prune(clear=True)
        self.assertTrue(playing.exists())
        self.assertFalse(unused.exists())
        self.manager.close()
        other.prune(clear=True)
        self.assertFalse(playing.exists())

    def test_live_work_blocks_cleanup_nested_calls_share_lock_and_wait_is_cancellable(self):
        pending = self.file('renders/dj-render-active/scratch.raw')

        @managed_cache
        def nested(root):
            self.assertTrue(self.manager.prune(clear=True)['busy'])

        with work_scope(self.root):
            nested(self.root / 'renders')
            self.assertTrue(pending.exists())
        self.manager.prune()
        self.assertFalse(pending.exists())
        lock = self.manager.work_lock()
        with lock.acquire(timeout=0):
            def cancel(message):
                raise RuntimeError('cancelled while waiting')
            with self.assertRaisesRegex(RuntimeError, 'cancelled while waiting'):
                with work_scope(self.root, cancel):
                    self.fail('A competing job must wait')
        with self.manager.work_lock().acquire(timeout=0):
            pass

    def test_worker_holds_cache_until_gui_handoff(self):
        from desktop_app import Worker
        lock = self.manager.work_lock()
        results = []
        worker = Worker(lambda progress, emit: self.manager.prune(clear=True)['busy'],
                        threading.Event(), lock, hold_cache=True)
        worker.signals.result.connect(results.append)
        worker.run()
        try:
            self.assertEqual(results, [True])
            with self.assertRaises(Timeout):
                self.manager.work_lock().acquire(timeout=0)
        finally:
            lock.release()
        self.assertFalse(self.manager.prune(clear=True)['busy'])

    def test_cancelled_worker_reaps_audio_process_releases_cache_and_emits_no_result(self):
        import subprocess
        import sys
        from desktop_app import Worker
        event = threading.Event(); results, errors, finished, children = [], [], [], []
        original = subprocess.Popen
        def popen(*args, **kwargs):
            child = original(*args, **kwargs); children.append(child)
            event.set()
            return child
        worker = Worker(lambda progress, emit: djmix.run([sys.executable, '-c', 'import time; time.sleep(30)']),
                        event, self.manager.work_lock())
        worker.signals.result.connect(results.append)
        worker.signals.error.connect(errors.append)
        worker.signals.finished.connect(lambda: finished.append(True))
        with patch('studio_process.subprocess.Popen', side_effect=popen):
            worker.run()
        self.assertEqual(results, []); self.assertEqual(finished, [True])
        self.assertEqual(errors, ['Operation cancelled.'])
        self.assertEqual(len(children), 1); self.assertIsNotNone(children[0].poll())
        self.assertFalse(self.manager.prune(clear=True)['busy'])
        # Already-cancelled work cannot perform even a cached operation.
        with patch.object(worker, 'function', side_effect=AssertionError('Cancelled job ran')):
            worker.run()
        self.assertEqual(errors, ['Operation cancelled.'] * 2)

    def test_oldest_unused_entries_are_evicted_to_limit_and_stale_pins_expire(self):
        import os
        oldest, newest = self.entry('a'), self.entry('b')
        os.utime(oldest.parent / 'report.json', (1, 1))
        stale = self.file('.cache-pins/stale.json', json.dumps([str(oldest)]).encode())
        newest_size = sum(p.stat().st_size for p in newest.parent.iterdir())
        with patch.object(self.manager, 'limit', return_value=newest_size):
            result = self.manager.prune()
        self.assertEqual(result['remaining'], newest_size)
        self.assertFalse(oldest.exists())
        self.assertTrue(newest.exists())
        self.assertFalse(stale.exists())

    def test_low_space_fails_before_render_and_preserves_existing_destination(self):
        from studio_engine import render_session
        from tests.test_studio import project
        from collections import namedtuple
        destination = self.file('export.wav', b'keep existing export')
        usage = namedtuple('Usage', 'total used free')(1000, 999, 1)
        with patch('studio_storage.shutil.disk_usage', return_value=usage), \
             patch('djmix.prepare') as prepare, self.assertRaisesRegex(djmix.MixError, 'Not enough free space'):
            render_session(project(), destination)
        prepare.assert_not_called()
        self.assertEqual(destination.read_bytes(), b'keep existing export')


if __name__ == '__main__':
    unittest.main()
