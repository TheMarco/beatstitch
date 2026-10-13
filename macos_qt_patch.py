"""Package the reviewed Qt 6.11.2 Cocoa accessibility fix for Apple Silicon.

Keep this build-time only. Unknown Qt runtimes must be reviewed, never silently
mixed with a plugin using a different private Qt ABI.
"""
import hashlib
import json
import platform
from pathlib import Path
import shutil
import subprocess


def sha256(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def verify_patch(root, *, runtime=None, version=None, architecture=None):
    root = Path(root)
    folder = root / 'vendor/qt-cocoa'
    manifest = json.loads((folder / 'manifest.json').read_text())
    if runtime is None or version is None:
        from PySide6.QtCore import QLibraryInfo, qVersion
        runtime = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.LibrariesPath))
        version = qVersion()
    if (version, architecture or platform.machine()) != (manifest['qt_version'], manifest['architecture']):
        raise RuntimeError('The macOS accessibility fix is verified for Qt 6.11.2 arm64. '
                           'Review/rebuild the plugin for this Qt runtime before packaging. '
                           'See vendor/qt-cocoa/README.md.')
    for module, expected in manifest['runtime_sha256'].items():
        library = Path(runtime) / f'Qt{module}.framework/Versions/A/Qt{module}'
        if not library.is_file() or sha256(library) != expected:
            raise RuntimeError(f'Qt{module} differs from the runtime verified with the Cocoa fix.')
    for name, expected in manifest['files_sha256'].items():
        path = folder / name
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f'The verified Cocoa fix asset is missing or changed: {name}')
    archive = root / manifest['source_archive']
    if not archive.is_file() or sha256(archive) != manifest['source_archive_sha256']:
        raise RuntimeError('The matching Qt source archive is missing or changed. '
                           'Run rebuild_qt_cocoa.py to fetch and verify it.')
    return folder / 'libqcocoa.dylib'


SOURCE_COMPANION_NOTE = (
    'Matching Qt source\n'
    '==================\n\n'
    'The matching Qt 6.11.2 source archive (qtbase-v6.11.2.tar.gz) is not\n'
    'included in this application bundle. It is delivered in the separate\n'
    'BeatStitch source companion for this release. The patch, licenses,\n'
    'manifest, and rebuild instructions in this folder are unchanged.\n'
    'Build-time verification of the matching source archive is unchanged:\n'
    'packaging still verifies it in the checkout before building. To rebuild,\n'
    'use rebuild_qt_cocoa.py with the source companion archive available.\n')


def copied_readme(text):
    """Adjust only the copied notice so it does not claim bundled source."""
    return (text
            .replace('includes this\nnotice, licenses and source, then signs',
                     'includes this\nnotice, licenses, patch and rebuild instructions, then signs')
            .replace('includes this notice, licenses and source, then signs',
                     'includes this notice, licenses, patch and rebuild instructions, then signs')
            .replace('From the bundled `Contents/Resources/qt-cocoa-fix` source package, run its\n'
                     '`rebuild_qt_cocoa.py` using a Python environment with that same PySide6 runtime,\n'
                     'passing `--work-dir` for a writable build directory. The bundled source archive\n'
                     'is used without downloading.',
                     'From `Contents/Resources/qt-cocoa-fix`, run its `rebuild_qt_cocoa.py`\n'
                     'using a Python environment with that same PySide6 runtime, passing\n'
                     '`--work-dir` for a writable build directory, with the matching source\n'
                     'archive from the separate source companion for this release.')
            .replace('The original Qt sources are supplied as `qtbase-v6.11.2.tar.gz` in the app\'s\n'
                     'source package; the checkout keeps that verified download under `assets/qt/`.',
                     'The original Qt sources are supplied as `qtbase-v6.11.2.tar.gz` in the\n'
                     'separate source companion for this release; the checkout keeps that\n'
                     'verified download under `assets/qt/` and packaging verifies it there.'))


def apply_patch(app, root, plugin):
    """Change a completed staging bundle, then verify its new ad-hoc signature."""
    app, root, plugin = Path(app).resolve(), Path(root).resolve(), Path(plugin).resolve()
    destination = app / 'Contents/Frameworks/PySide6/Qt/plugins/platforms/libqcocoa.dylib'
    if not destination.is_file() or not destination.resolve().is_relative_to(app):
        raise RuntimeError('The staging app has no contained Cocoa platform plugin.')
    # Recheck immediately before copying; a build may take several minutes.
    verified = verify_patch(root)
    if plugin != verified.resolve():
        raise RuntimeError('Refusing an unverified Cocoa plugin path.')
    shutil.copyfile(plugin, destination)
    notice = app / 'Contents/Resources/qt-cocoa-fix'
    notice.mkdir(parents=True, exist_ok=True)
    folder = root / 'vendor/qt-cocoa'
    for name in ('manifest.json', 'upstream.patch', 'CMakeLists.txt'):
        shutil.copyfile(folder / name, notice / name)
    (notice / 'README.md').write_text(
        copied_readme((folder / 'README.md').read_text(encoding='utf-8')), encoding='utf-8')
    shutil.copytree(folder / 'LICENSES', notice / 'LICENSES', dirs_exist_ok=True)
    shutil.copyfile(root / 'rebuild_qt_cocoa.py', notice / 'rebuild_qt_cocoa.py')
    (notice / 'SOURCE-COMPANION.txt').write_text(SOURCE_COMPANION_NOTE, encoding='utf-8')
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
