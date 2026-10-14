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

from ci.install_windows_audio import install, SOURCE_IDENTITIES


class WindowsAudioToolsTests(unittest.TestCase):
    def source_fixture(self, root):
        source = root / 'source-build'; (source / 'bin').mkdir(parents=True)
        (source / 'licenses').mkdir()
        members = ['bin/ffmpeg.exe', 'bin/ffprobe.exe', 'licenses/test-copyright.txt']
        files = []
        for name in members:
            data = ('source-built ' + name).encode(); (source / name).write_bytes(data)
            files.append({'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        receipt = {'compiler_target': 'x86_64-w64-mingw32',
                   'sources': [{'url': url, 'sha256': sha} for url, sha in SOURCE_IDENTITIES.items()],
                   'binaries': files[:2], 'licenses': files[2:]}
        (source / 'receipt.json').write_text(json.dumps(receipt))
        # Compressed sources belong in a separate release asset, not the app.
        (source / 'sources').mkdir(); (source / 'sources/compiler.tar.xz').write_bytes(b'source fixture')
        return source

    def test_source_built_receipt_verified_and_source_archives_not_bundled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = self.source_fixture(root); target = root / 'tools'
            install(target, archives=[], source_ffmpeg=source)
            self.assertEqual((target / 'bin/ffmpeg.exe').read_bytes(), (source / 'bin/ffmpeg.exe').read_bytes())
            self.assertFalse((target / 'sources').exists())
            self.assertIsNotNone(json.loads((target / 'manifest.json').read_text())['source_ffmpeg'])

    def test_tampered_source_built_executable_leaves_no_partial_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = self.source_fixture(root); target = root / 'tools'
            (source / 'bin/ffprobe.exe').write_bytes(b'changed executable')
            with self.assertRaises(ValueError): install(target, archives=[], source_ffmpeg=source)
            self.assertFalse(target.exists())
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['source-build'])

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
