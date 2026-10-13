"""Fetch/conversion of the redistributable official Spleeter weights.

Used by the in-app optional download and by source/offline builds.
The pinned TensorBundle contains numeric arrays; no saved graph is executed.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import urllib.request
from studio_process import check_cancelled

from studio_separation_model import MODEL_BYTES, MODEL_FILE, MODEL_SHA256, sha256
from studio_spleeter_checkpoint import FILES, convert_checkpoint

ARCHIVE_URL = 'https://github.com/deezer/spleeter/releases/download/v1.4.0/4stems.tar.gz'
ARCHIVE_BYTES = 146308147
ARCHIVE_SHA256 = '3adb4a50ad4eb18c7c4d65fcf4cf2367a07d48408a5eb7d03cd20067429dfaa8'


def verified(path, size, digest):
    path = Path(path)
    return not path.is_symlink() and path.is_file() and path.stat().st_size == size and sha256(path) == digest


def extract_numeric(archive, destination):
    """Copy only the two pinned numeric files, never checkpoint graph/metadata."""
    destination = Path(destination)
    found = set()
    with tarfile.open(archive, 'r:gz') as package:
        for member in package:
            check_cancelled()
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or '\\' in member.name or member.issym() or member.islnk():
                raise ValueError('Unsafe path in the model archive.')
            if member.name not in FILES:
                continue
            size, digest = FILES[member.name]
            if member.name in found or not member.isfile() or member.size != size:
                raise ValueError('Unexpected numeric checkpoint entry.')
            found.add(member.name)
            stream = package.extractfile(member)
            if stream is None:
                raise ValueError('Missing numeric checkpoint data.')
            with stream, (destination/member.name).open('xb') as output:
                while chunk := stream.read(1024*1024):
                    check_cancelled(); output.write(chunk)
            if not verified(destination/member.name, size, digest):
                raise ValueError('The numeric checkpoint failed verification.')
    if found != set(FILES):
        raise ValueError('The model archive is incomplete.')


def fetch_model(destination, *, archive=None, progress=print):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / MODEL_FILE
    if target.is_symlink():
        raise ValueError('The model destination cannot be a symbolic link.')
    if verified(target, MODEL_BYTES, MODEL_SHA256):
        progress('Verified vocal-removal model is already available.')
        return target
    with tempfile.TemporaryDirectory(prefix='.spleeter-', dir=destination) as temporary:
        stage = Path(temporary)
        if archive is None:
            archive = stage/'official.tar.gz'
            progress('Downloading official Spleeter weights (146 MB)')
            with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response, archive.open('xb') as output:
                if not response.geturl().startswith('https://'):
                    raise ValueError('The publisher redirected to an insecure download.')
                total = 0
                while chunk := response.read(1024*1024):
                    check_cancelled()
                    total += len(chunk)
                    if total > ARCHIVE_BYTES:
                        raise ValueError('Unexpected model archive size.')
                    output.write(chunk)
        archive = Path(archive)
        if not verified(archive, ARCHIVE_BYTES, ARCHIVE_SHA256):
            raise ValueError('The official Spleeter archive failed verification.')
        extract_numeric(archive, stage)
        progress('Converting verified numeric weights without TensorFlow')
        converted = stage/MODEL_FILE
        convert_checkpoint(stage, converted)
        check_cancelled()
        if not verified(converted, MODEL_BYTES, MODEL_SHA256):
            raise ValueError('The converted separation model failed verification.')
        converted.replace(target)
    progress('Vocal-removal model is ready. Inference is offline.')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=Path('assets/separation'))
    parser.add_argument('--archive', type=Path, help='Reuse an already downloaded, pinned official archive.')
    args = parser.parse_args()
    fetch_model(args.destination, archive=args.archive)


if __name__ == '__main__':
    main()
