"""Audio provisioning must verify bytes and stage only the requested files."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from ci.install_windows_audio import install


class WindowsAudioToolsTests(unittest.TestCase):
    def fixture(self, duplicate=False):
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            archive.writestr('release/tool.exe', b'fixture executable')
            archive.writestr('../../unrelated', b'not selected')
            if duplicate:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore')
                    archive.writestr('release/tool.exe', b'duplicate')
        data = content.getvalue()
        spec = {'name': 'fixture', 'url': 'https://example.invalid/pinned.zip',
                'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                'files': {'release/tool.exe': 'bin/tool.exe'}}
        return spec, data

    def test_verified_selected_members_and_manifest_only_existing_files_preserved(self):
        spec, data = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'tools'
            with patch('ci.install_windows_audio.urllib.request.urlopen', return_value=io.BytesIO(data)):
                self.assertEqual(install(target, [spec]), (target/'bin').resolve())
            self.assertEqual((target/'bin/tool.exe').read_bytes(), b'fixture executable')
            self.assertEqual(sorted(p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()),
                             ['bin/tool.exe', 'manifest.json'])
            manifest = json.loads((target/'manifest.json').read_text())
            self.assertEqual(manifest['files']['bin/tool.exe'], hashlib.sha256(b'fixture executable').hexdigest())
            with self.assertRaises(FileExistsError):
                install(target, [spec])

    def test_mismatch_and_ambiguous_archive_leave_no_partial_installation(self):
        cases = []
        spec, data = self.fixture(); cases.append((dict(spec, sha256='0'*64), data))
        cases.append((dict(spec, bytes=len(data)-1), data))
        cases.append(self.fixture(duplicate=True))
        for spec, data in cases:
            with self.subTest(spec=spec), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)/'tools'
                with patch('ci.install_windows_audio.urllib.request.urlopen', return_value=io.BytesIO(data)):
                    with self.assertRaises(ValueError):
                        install(target, [spec])
                self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
