"""Never package a Cocoa repair against an unverified private Qt ABI or asset."""
import json
from pathlib import Path
import tempfile
import unittest

from macos_qt_patch import apply_patch, sha256, verify_patch


class CocoaPatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'vendor/qt-cocoa'
        self.folder.mkdir(parents=True)
        self.runtime = self.root / 'runtime'
        runtime_hashes = {}
        for name in ('Core', 'Gui'):
            path = self.runtime / f'Qt{name}.framework/Versions/A/Qt{name}'
            path.parent.mkdir(parents=True)
            path.write_bytes(name.encode())
            runtime_hashes[name] = sha256(path)
        (self.folder / 'libqcocoa.dylib').write_bytes(b'verified candidate')
        archive = self.root / 'source.tar.gz'; archive.write_bytes(b'verified source')
        self.manifest = {'qt_version': '6.11.2', 'architecture': 'arm64',
                         'runtime_sha256': runtime_hashes,
                         'files_sha256': {'libqcocoa.dylib': sha256(self.folder / 'libqcocoa.dylib')},
                         'source_archive': 'source.tar.gz', 'source_archive_sha256': sha256(archive)}
        (self.folder / 'manifest.json').write_text(json.dumps(self.manifest))

    def verify(self, **kwargs):
        options = dict(runtime=self.runtime, version='6.11.2', architecture='arm64')
        options.update(kwargs)
        return verify_patch(self.root, **options)

    def test_matching_runtime_and_source_accept_only_the_pinned_plugin(self):
        self.assertEqual(self.verify(), self.folder / 'libqcocoa.dylib')

    def test_changed_or_missing_plugin_stops_packaging(self):
        plugin = self.folder / 'libqcocoa.dylib'
        plugin.write_bytes(b'other binary')
        with self.assertRaisesRegex(RuntimeError, 'asset is missing or changed'):
            self.verify()
        plugin.unlink()
        with self.assertRaisesRegex(RuntimeError, 'asset is missing or changed'):
            self.verify()

    def test_another_qt_version_architecture_or_runtime_requires_review(self):
        for changes in ({'version': '6.12.0'}, {'architecture': 'x86_64'}):
            with self.subTest(changes=changes), self.assertRaisesRegex(RuntimeError, 'Review/rebuild'):
                self.verify(**changes)
        (self.runtime / 'QtGui.framework/Versions/A/QtGui').write_bytes(b'other private ABI')
        with self.assertRaisesRegex(RuntimeError, 'QtGui differs'):
            self.verify()

    def test_missing_or_changed_source_cannot_produce_a_distribution(self):
        archive = self.root / 'source.tar.gz'
        archive.write_bytes(b'different source')
        with self.assertRaisesRegex(RuntimeError, 'source archive is missing or changed'):
            self.verify()
        archive.unlink()
        with self.assertRaisesRegex(RuntimeError, 'source archive is missing or changed'):
            self.verify()

    def test_a_plugin_symlink_outside_staging_is_not_modified(self):
        app = self.root / 'staging.app'
        target = app / 'Contents/Frameworks/PySide6/Qt/plugins/platforms/libqcocoa.dylib'
        target.parent.mkdir(parents=True)
        external = self.root / 'keep.dylib'; external.write_bytes(b'unchanged')
        target.symlink_to(external)
        with self.assertRaisesRegex(RuntimeError, 'no contained Cocoa'):
            apply_patch(app, self.root, self.folder / 'libqcocoa.dylib')
        self.assertEqual(external.read_bytes(), b'unchanged')


if __name__ == '__main__':
    unittest.main()
