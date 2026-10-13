import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import djmix
import studio_model_download as fetch
import studio_model_store as store
from studio_process import cancel_on


class Response(io.BytesIO):
    def __init__(self, data, *, status=200, headers=None, url='https://publisher.example/model'):
        super().__init__(data)
        self.status, self.headers, self.url = status, headers or {}, url

    def geturl(self): return self.url


def expected(data):
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name)/'model'
        self.data = b'abcdefghi'
        self.spec = expected(self.data)

    def test_resume_range_and_publish_only_verified_bytes(self):
        self.target.with_name('model.partial').write_bytes(b'abc')
        requests = []
        def opener(request, **kwargs):
            requests.append(request)
            return Response(b'defghi', status=206, headers={'Content-Range':'bytes 3-8/9', 'Content-Length':'6'})
        fetch.download('https://publisher.example/model', self.target, self.spec, opener=opener)
        self.assertEqual(requests[0].headers['Range'], 'bytes=3-')
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertFalse(self.target.with_name('model.partial').exists())

    def test_server_ignoring_range_restarts_safely(self):
        self.target.with_name('model.partial').write_bytes(b'abc')
        fetch.download('https://publisher.example/model', self.target, self.spec,
                       opener=lambda *a, **k: Response(self.data, headers={'Content-Length':'9'}))
        self.assertEqual(self.target.read_bytes(), self.data)

    def test_truncated_download_keeps_partial_and_existing_target(self):
        self.target.write_bytes(b'old')
        with self.assertRaisesRegex(ValueError, 'stopped early'):
            fetch.download('https://publisher.example/model', self.target, self.spec,
                           opener=lambda *a, **k: Response(b'abc'))
        self.assertEqual(self.target.read_bytes(), b'old')
        self.assertEqual(self.target.with_name('model.partial').read_bytes(), b'abc')

    def test_bad_digest_never_publishes_and_discards_corrupt_partial(self):
        self.target.write_bytes(b'old')
        with self.assertRaisesRegex(ValueError, 'verification failed'):
            fetch.download('https://publisher.example/model', self.target, self.spec,
                           opener=lambda *a, **k: Response(b'badbadbad'))
        self.assertEqual(self.target.read_bytes(), b'old')
        self.assertFalse(self.target.with_name('model.partial').exists())

    def test_cancellation_keeps_bytes_for_retry_without_publishing(self):
        event = threading.Event()
        def progress(count, total):
            if count: event.set()
        with patch.object(fetch, 'CHUNK', 3), cancel_on(event):
            with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                fetch.download('https://publisher.example/model', self.target, self.spec, progress,
                               opener=lambda *a, **k: Response(self.data))
        self.assertFalse(self.target.exists())
        self.assertEqual(self.target.with_name('model.partial').read_bytes(), b'abc')

    def test_wrong_range_and_insecure_redirect_are_refused(self):
        self.target.with_name('model.partial').write_bytes(b'abc')
        with self.assertRaisesRegex(ValueError, 'invalid download range'):
            fetch.download('https://publisher.example/model', self.target, self.spec,
                           opener=lambda *a, **k: Response(b'defghi', status=206, headers={'Content-Range':'bytes 2-8/9'}))
        with self.assertRaisesRegex(ValueError, 'insecure'):
            fetch.download('https://publisher.example/model', self.target, self.spec,
                           opener=lambda *a, **k: Response(self.data, url='http://publisher.example/model'))

    def test_symlink_is_not_followed(self):
        outside = Path(self.temp.name)/'outside'; outside.write_bytes(b'unchanged')
        self.target.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            fetch.download('https://publisher.example/model', self.target, self.spec)
        self.assertEqual(outside.read_bytes(), b'unchanged')

    def test_verified_file_needs_no_network(self):
        self.target.write_bytes(self.data)
        with patch.object(fetch.urllib.request, 'urlopen', side_effect=AssertionError('Network used')):
            fetch.download('https://publisher.example/model', self.target, self.spec)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()/'user-models'
        data = b'a tiny verified model'
        self.data = data
        spec = {'title':'Test detector', 'legacy':'test', 'description':'Test', 'recommended':False, 'license':'MIT',
                'files':[dict(name='model.pt', url='https://publisher.example/model', **expected(data))],
                'download_bytes':len(data), 'installed_bytes':len(data)}
        self.catalog = {'test':spec}
        self.env = patch.dict(os.environ, {store.MODEL_ENV:str(self.root), 'BEATSTITCH_NO_BUNDLED_MODELS':'1'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.packs = patch.object(store, 'packs', return_value=self.catalog)
        self.packs.start(); self.addCleanup(self.packs.stop)
        self.marker = patch.object(store, 'ROOT', Path(self.temp.name)/'application')
        self.marker.start(); self.addCleanup(self.marker.stop)

    def install(self):
        with patch.object(fetch.urllib.request, 'urlopen', side_effect=lambda *a, **k: Response(self.data)):
            return store.install('test')

    def test_user_store_is_atomic_ready_and_survives_app_change(self):
        self.assertEqual(store.status('test'), 'missing')
        folder = self.install()
        self.assertEqual(folder, self.root/'test')
        self.assertEqual(store.status('test'), 'ready')
        with patch.object(store, 'ROOT', Path(self.temp.name)/'upgraded-app'):
            self.assertEqual(store.directory('test'), folder)
            self.assertTrue(store.available('test'))
        with patch.object(fetch.urllib.request, 'urlopen', side_effect=AssertionError('Repeated network download')):
            store.install('test')

    def test_modified_file_is_repairable_and_does_not_keep_ready_label(self):
        folder = self.install(); target=folder/'model.pt'; original=target.stat()
        target.write_bytes(b'x'*len(self.data))
        real_stat=Path.stat
        def same_timestamp(path, *args, **kwargs):
            return original if path == target else real_stat(path, *args, **kwargs)
        with patch.object(Path, 'stat', same_timestamp):
            self.assertEqual(store.status('test'), 'verify')
        self.install(); self.assertEqual(store.status('test'), 'ready')

    def test_remove_only_owned_model_pack_and_saved_bytes(self):
        self.install()
        song = self.root.parent/'song.wav'; song.write_bytes(b'private song')
        project = self.root.parent/'autosave.djmix'; project.write_text('{}')
        store.remove('test')
        self.assertEqual(store.status('test'), 'missing')
        self.assertEqual(song.read_bytes(), b'private song'); self.assertEqual(project.read_text(), '{}')

    def test_pack_lock_refuses_overlapping_install_or_remove(self):
        from filelock import FileLock
        self.root.mkdir(); locks=self.root/'.locks'; locks.mkdir()
        with FileLock(locks/'test.lock'):
            with self.assertRaisesRegex(ValueError, 'another BeatStitch'):
                store.install('test')
            with self.assertRaisesRegex(ValueError, 'another BeatStitch'):
                store.remove('test')

    def test_removal_refuses_analysis_using_the_same_data_folder(self):
        from filelock import FileLock
        self.install()
        with FileLock(self.root.parent/'.cache-work.lock'):
            with self.assertRaisesRegex(ValueError, 'analysis job'):
                store.remove('test')
        self.assertEqual(store.status('test'), 'ready')

    def test_storage_symlink_cannot_redirect_install_or_removal(self):
        self.root.mkdir(); outside=self.root.parent/'outside'; outside.mkdir()
        (outside/'important').write_text('keep')
        (self.root/'test').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symbolic links'):
            store.install('test')
        with self.assertRaisesRegex(ValueError, 'symbolic links'):
            store.remove('test')
        self.assertEqual((outside/'important').read_text(), 'keep')


if __name__ == '__main__': unittest.main()
