"""Pinned, offline source-key model and its redistribution attribution."""
from __future__ import annotations
import hashlib
from importlib import metadata
from pathlib import Path
import sys

import djmix

SOURCE_REVISION = 'd5cac64bc7195826b6eb10c55b9b0c97fb3287c0'
MODEL_FILE = 'openkeyscan3.pt'
MODEL_SHA256 = '5610691be2b3aa21764c5978ca0c2524fcdbf600f09dcbda6c2da937745db600'
MODEL_BYTES = 18830452
LICENSE_FILE = 'LICENSE.openkeyscan'
LICENSE_SHA256 = 'b067c3dcb70970499fa58350e6327aa806ce3c92bfb01e77ea5df5bb951b125b'
NETWORK_SHA256 = '729c7fe571be03008a59c370f9ef6caf74526413e9a7aaf6adaafd340da39641'
LIBRARY_VERSIONS = {'torch': '2.11.0', 'librosa': '0.11.0'}
RUNTIME_PACKAGES = ('torch', 'librosa', 'numpy', 'scipy', 'numba')


def verify_runtime():
    """Accept the pinned release and its official CPU-only wheel spelling."""
    try:
        installed = {name: metadata.version(name) for name in LIBRARY_VERSIONS}
    except metadata.PackageNotFoundError:
        installed = {}
    allowed = {name: {version} for name, version in LIBRARY_VERSIONS.items()}
    allowed['torch'].add(LIBRARY_VERSIONS['torch'] + '+cpu')
    if any(installed.get(name) not in versions for name, versions in allowed.items()):
        raise djmix.MixError('Key detection is not installed in this build. You can still set the key yourself.')


def model_directory():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets' / 'key'


def verify_model():
    path = model_directory() / MODEL_FILE
    if (not path.is_file() or path.stat().st_size != MODEL_BYTES
            or hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256):
        raise djmix.MixError('Key detection files are missing or damaged. Reinstall DJ Studio; you can still set the key yourself.')
    license_path = model_directory() / LICENSE_FILE
    if not license_path.is_file() or hashlib.sha256(license_path.read_bytes()).hexdigest() != LICENSE_SHA256:
        raise djmix.MixError('The key model installation is incomplete. Reinstall DJ Studio.')
    verify_runtime()
    return path
