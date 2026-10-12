"""Rebuild the pinned Cocoa fix with a matching Qt SDK; never installs the result."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, default=Path('/opt/homebrew/opt/qtbase'))
    parser.add_argument('--work-dir', type=Path, default=root / 'build/qt-cocoa')
    parser.add_argument('--assets-dir', type=Path,
                        default=root if (root / 'manifest.json').is_file() else root / 'vendor/qt-cocoa')
    args = parser.parse_args()
    assets, work, sdk = args.assets_dir.resolve(), args.work_dir.resolve(), args.sdk.resolve()
    manifest = json.loads((assets / 'manifest.json').read_text())
    from PySide6.QtCore import QLibraryInfo, qVersion
    import platform
    if (qVersion(), platform.machine()) != (manifest['qt_version'], manifest['architecture']):
        raise SystemExit('This reviewed build recipe requires PySide6 Qt 6.11.2 on arm64.')
    runtime = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.LibrariesPath))
    for name, expected in manifest['runtime_sha256'].items():
        if digest(runtime / f'Qt{name}.framework/Versions/A/Qt{name}') != expected:
            raise SystemExit(f'The Qt{name} runtime differs from the verified build.')
    config = sdk / 'lib/QtGui.framework/Headers/qtgui-config.h'
    if digest(config) != manifest['sdk_gui_config_sha256']:
        raise SystemExit('Use the matching Qt 6.11.2 SDK configuration described in README.md.')
    archive = assets / 'qtbase-v6.11.2.tar.gz'
    if not archive.exists():
        archive = root / manifest['source_archive']
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        stage = archive.with_suffix('.download')
        try:
            with urllib.request.urlopen(manifest['source_url'], timeout=60) as source, stage.open('wb') as target:
                shutil.copyfileobj(source, target)
            if digest(stage) != manifest['source_archive_sha256']:
                raise SystemExit('The downloaded Qt archive failed its pinned SHA-256 check.')
            stage.replace(archive)
        finally:
            stage.unlink(missing_ok=True)
    if digest(archive) != manifest['source_archive_sha256']:
        raise SystemExit('The local Qt source archive failed its pinned SHA-256 check.')
    work.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        members = [entry for entry in source.getmembers()
                   if entry.name.startswith('qtbase-6.11.2/src/plugins/platforms/cocoa/')]
        source.extractall(work, members=members, filter='data')
    path = work / 'qtbase-6.11.2/src/plugins/platforms/cocoa/qcocoaaccessibilityelement.mm'
    if digest(path) != manifest['original_source_sha256']:
        raise SystemExit('Unexpected accessibility source; refusing to apply the patch.')
    original = path.read_text()
    patched = original.replace(
        "if (cell->axid) { // it's a proper cell, remove from cache",
        "if (cell->axid && !cell.isManagedByParent) {\n"
        "            // Proper cells own their interface. Synthesized elements use their\n"
        "            // parent table's ID and must not remove that interface.")
    patched = patched.replace(
        '    QAccessibleCache::instance()->deleteInterface(axid);\n    [super dealloc];',
        '    if (axid && !self.isManagedByParent)\n'
        '        QAccessibleCache::instance()->deleteInterface(axid);\n    [super dealloc];')
    path.write_text(patched)
    if digest(path) != manifest['patched_source_sha256']:
        raise SystemExit('Patched source differs from the reviewed upstream ownership fix.')
    overlay = work / 'headers/QtGui/qtgui-config.h'
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(config.read_text().replace('#define QT_FEATURE_vulkan 1', '#define QT_FEATURE_vulkan -1'))
    shutil.copyfile(assets / 'CMakeLists.txt', work / 'CMakeLists.txt')
    build = work / 'build'
    subprocess.run(['cmake', '-S', str(work), '-B', str(build), f'-DCMAKE_PREFIX_PATH={sdk}',
                    f'-DDJ_QT_RUNTIME={runtime}', '-DCMAKE_BUILD_TYPE=Release',
                    '-DCMAKE_OSX_DEPLOYMENT_TARGET=13.0'], check=True)
    subprocess.run(['cmake', '--build', str(build), '-j', '4'], check=True)
    plugin = build / 'platforms/libqcocoa.dylib'
    subprocess.run(['install_name_tool', '-delete_rpath', str(runtime), '-add_rpath',
                    '@loader_path/../../../..', str(plugin)], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(plugin)], check=True)
    print(f'Rebuilt plugin (not installed): {plugin}\nSHA-256: {digest(plugin)}')
    print('Re-run the documented native regression and package checks before promoting this binary.')


if __name__ == '__main__':
    main()
