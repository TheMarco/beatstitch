"""Ask the BeatStitch website for the newest release and download it on request.

The check sends one HTTPS request for latest.json and nothing about the user's
songs or mixes. A download is pinned to the size and SHA-256 published there.
"""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import re
import ssl
import sys
import urllib.error
import urllib.request

from studio_version import VERSION

LATEST_URL = 'https://beatstitch.ai-created.com/latest.json'
DOWNLOAD_PAGE = 'https://beatstitch.ai-created.com/#download'
CHECK_INTERVAL = 24 * 60 * 60
LIMIT = 64 * 1024
PACKAGES = {'mac': 'mac-arm64.zip', 'windows': 'windows-x64.zip', 'linux': 'linux-x64.tar.gz'}


def parse_version(text):
    """(0, 1, 2, 0) from '0.1.2', padded so versions of different lengths compare; None if invalid."""
    if not isinstance(text, str) or not re.fullmatch(r'\d{1,4}(\.\d{1,4}){1,3}', text):
        return None
    parts = tuple(int(part) for part in text.split('.'))
    return parts + (0,) * (4 - len(parts))


def is_newer(latest, current=VERSION):
    newest, installed = parse_version(latest), parse_version(current)
    return newest is not None and installed is not None and newest > installed


def due(last_checked, now, interval=CHECK_INTERVAL):
    """At most one automatic check a day; a clock that moved backwards checks again."""
    if not isinstance(last_checked, (int, float)):
        return True
    return now - last_checked >= interval or now < last_checked


def platform_key(platform=sys.platform):
    return 'mac' if platform == 'darwin' else 'windows' if platform == 'win32' else 'linux' if platform.startswith('linux') else None


def package(release, platform=sys.platform):
    """The download for this computer, or None when the release doesn't offer a valid one."""
    key = platform_key(platform)
    entry = (release.get('downloads') or {}).get(key) if key else None
    if not isinstance(entry, dict):
        return None
    url, size, sha256 = entry.get('url'), entry.get('size'), entry.get('sha256')
    if (not isinstance(url, str) or not url.startswith('https://')
            or not isinstance(size, int) or isinstance(size, bool) or not 0 < size < 8 * 10**9
            or not isinstance(sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', sha256)):
        return None
    # The file name comes from the validated version, never from the URL.
    return {'url': url, 'size': size, 'sha256': sha256, 'name': f"BeatStitch-{release['version']}-{PACKAGES[key]}"}


def fetch_latest(opener=None, timeout=10):
    """The published release as {'version', 'downloads'}. Raises OSError or ValueError."""
    request = urllib.request.Request(LATEST_URL, headers={
        'User-Agent': f'BeatStitch/{VERSION} (update check)', 'Accept': 'application/json', 'Cache-Control': 'no-cache'})
    if opener is None:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
        response = urllib.request.urlopen(request, timeout=timeout, context=context)
    else:
        response = opener(request, timeout=timeout)
    with response:
        if not response.geturl().startswith('https://'):
            raise ValueError('The update check was redirected to an insecure address.')
        body = response.read(LIMIT + 1)
    if len(body) > LIMIT:
        raise ValueError('The update information is larger than expected.')
    try:
        release = json.loads(body.decode('utf-8'))
    except ValueError:
        raise ValueError('The update information could not be read.') from None
    if not isinstance(release, dict) or parse_version(release.get('version')) is None:
        raise ValueError('The update information has no valid version.')
    return release


def download_folder():
    from PySide6.QtCore import QStandardPaths
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    return Path(location) if location else Path.home()


def target_path(folder, name, expected):
    """A free name in folder, or one that already holds this exact release. Never another file."""
    from studio_model_download import verified
    stem, suffix = (name[:-7], '.tar.gz') if name.endswith('.tar.gz') else (Path(name).stem, Path(name).suffix)
    for index in range(1, 100):
        candidate = Path(folder) / (name if index == 1 else f'{stem} ({index}){suffix}')
        if not candidate.exists() and not candidate.is_symlink() or verified(candidate, expected):
            return candidate
    raise ValueError(f'Too many files named {name} in {folder}.')


def download_update(entry, folder, progress=lambda received, total: None, *, opener=None):
    """Download into folder, resuming saved bytes, and return the verified file."""
    from studio_model_download import download
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    expected = {'size': entry['size'], 'sha256': entry['sha256']}
    try:
        return download(entry['url'], target_path(folder, entry['name'], expected), expected, progress, opener=opener)
    except urllib.error.HTTPError as error:
        raise ValueError(f'The download server returned HTTP {error.code}.') from None
    except (urllib.error.URLError, ConnectionError, TimeoutError, http.client.HTTPException):
        raise ValueError('The connection to the download server failed. '
                         'Check your internet connection; downloading again resumes the saved part.') from None
