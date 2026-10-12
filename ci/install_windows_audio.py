"""Stage pinned Windows audio tools for CI; never change system installations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile

ARCHIVES = (
    {'name': 'ffmpeg-9.0.2',
     'url': 'https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip',
     'bytes': 114768076,
     'sha256': '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba',
     'files': {'ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe': 'bin/ffmpeg.exe',
               'ffmpeg-9.0.2-essentials_build/bin/ffprobe.exe': 'bin/ffprobe.exe',
               'ffmpeg-9.0.2-essentials_build/LICENSE': 'licenses/ffmpeg-LICENSE.txt'}},
    {'name': 'rubberband-4.0.0',
     'url': 'https://www.breakfastquay.com/files/releases/rubberband-4.0.0-gpl-executable-windows.zip',
     'bytes': 1765785,
     'sha256': 'f2d47fc64dbb42f6cc62edf7933ac4fa89d8f0ef8b9cf97b6afc263a7fe05644',
     'files': {'rubberband-4.0.0-gpl-executable-windows/rubberband.exe': 'bin/rubberband.exe',
               'rubberband-4.0.0-gpl-executable-windows/sndfile.dll': 'bin/sndfile.dll',
               'rubberband-4.0.0-gpl-executable-windows/COPYING.txt': 'licenses/rubberband-COPYING.txt',
               'rubberband-4.0.0-gpl-executable-windows/README.txt': 'licenses/rubberband-README.txt'}},
)


def download(spec, target):
    digest, count = hashlib.sha256(), 0
    with urllib.request.urlopen(spec['url'], timeout=60) as response, target.open('xb') as handle:
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > spec['bytes']:
                raise ValueError('Audio-tool download exceeds its pinned size.')
            digest.update(chunk); handle.write(chunk)
    if count != spec['bytes'] or digest.hexdigest() != spec['sha256']:
        raise ValueError('Audio-tool archive differs from the pinned release.')


def extract(spec, archive_path, destination):
    """Copy only exact allowlisted members to paths supplied by this program."""
    with zipfile.ZipFile(archive_path) as archive:
        for member, relative in spec['files'].items():
            if archive.namelist().count(member) != 1:
                raise ValueError('Expected one pinned archive member: ' + member)
            if archive.getinfo(member).file_size > 256 * 1024**2:
                raise ValueError('Unexpected audio-tool executable size.')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)


def install(destination, archives=ARCHIVES):
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError('Choose a new directory; existing tools are kept unchanged.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dj-audio-tools-', dir=destination.parent) as temp:
        stage = Path(temp) / 'tools'; stage.mkdir()
        for spec in archives:
            archive = Path(temp) / (spec['name'] + '.zip')
            download(spec, archive)
            extract(spec, archive, stage)
            archive.unlink()
        manifest = {'archives': archives, 'files': {
            p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(stage.rglob('*')) if p.is_file()}}
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        stage.rename(destination)
    return destination / 'bin'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--github-path', action='store_true')
    args = parser.parse_args()
    if args.github_path and 'GITHUB_PATH' not in os.environ:
        parser.error('--github-path requires the Actions environment.')
    binary = install(args.destination)
    if args.github_path:
        with Path(os.environ['GITHUB_PATH']).open('a', encoding='utf-8') as handle:
            handle.write(str(binary)+'\n')
    print('Verified audio tools:', binary)


if __name__ == '__main__':
    main()
