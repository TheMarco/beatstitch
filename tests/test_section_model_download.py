import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from download_section_models import convert_checkpoint, install


class SectionModelDownloadTests(unittest.TestCase):
    @staticmethod
    def spec(data):
        return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def test_verified_files_are_reused_without_network_and_nested_files_are_installed(self):
        data = b'exact model bytes'
        manifest = {'muq/fixture.bin': self.spec(data)}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'models'
            with patch('download_section_models.urllib.request.urlopen', return_value=io.BytesIO(data)) as request:
                result = install(root, manifest, {'muq/fixture.bin': 'https://example.invalid/model'}, lambda _: None)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(result[0]['state'], 'installed')
            self.assertEqual((root/'muq/fixture.bin').read_bytes(), data)
            with patch('download_section_models.urllib.request.urlopen', side_effect=AssertionError('Unnecessary download')):
                result = install(root, manifest, {}, lambda _: None)
            self.assertEqual(result[0]['state'], 'reused')

    def test_failed_size_or_hash_keeps_existing_file_and_removes_staging(self):
        expected = self.spec(b'verified model')
        for data in (b'too short', b'x' * expected['size'], b'x' * (expected['size'] + 1)):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); target = root/'model'; target.write_bytes(b'existing file')
                with patch('download_section_models.urllib.request.urlopen', return_value=io.BytesIO(data)):
                    with self.assertRaises(ValueError):
                        install(root, {'model': expected}, {'model': 'https://example.invalid/model'}, lambda _: None)
                self.assertEqual(target.read_bytes(), b'existing file')
                self.assertEqual(list(root.iterdir()), [target])

    def test_later_failure_retains_completed_files_for_resume(self):
        manifest = {'first': self.spec(b'first'), 'second': self.spec(b'second')}
        sources = {'first': 'https://example.invalid/first', 'second': 'https://example.invalid/second'}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('download_section_models.urllib.request.urlopen', side_effect=[io.BytesIO(b'first'), OSError('offline')]):
                with self.assertRaisesRegex(OSError, 'offline'):
                    install(root, manifest, sources, lambda _: None)
            self.assertEqual((root/'first').read_bytes(), b'first')
            self.assertEqual(len(list(root.iterdir())), 1)
            with patch('download_section_models.urllib.request.urlopen', return_value=io.BytesIO(b'second')) as request:
                result = install(root, manifest, sources, lambda _: None)
            self.assertEqual(request.call_count, 1)
            self.assertEqual([r['state'] for r in result], ['reused', 'installed'])

    def test_low_disk_space_prevents_download_without_touching_existing_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); target = root/'model'; target.write_bytes(b'existing')
            with patch('download_section_models.shutil.disk_usage', return_value=SimpleNamespace(free=10)), \
                    patch('download_section_models.urllib.request.urlopen') as request:
                with self.assertRaisesRegex(OSError, 'free space'):
                    install(root, {'model': self.spec(b'correct')}, {'model': 'https://example.invalid'}, lambda _: None)
            request.assert_not_called()
            self.assertEqual(target.read_bytes(), b'existing')

    def test_unverified_checkpoint_is_rejected_before_loading_or_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root/'source.pt'; target = root/'model.safetensors'
            source.write_bytes(b'untrusted checkpoint')
            with self.assertRaisesRegex(ValueError, 'Unverified'):
                convert_checkpoint(source, target)
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
