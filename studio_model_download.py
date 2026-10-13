"""Pinned, resumable model downloads. No analysis code runs downloaded graphs."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import ssl
import urllib.error
import urllib.request

from studio_process import check_cancelled

CHUNK = 1024 * 1024


def verified(path, expected, progress=lambda message: None):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected['size']:
        return False
    checksum = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(CHUNK):
            check_cancelled()
            checksum.update(chunk)
    return checksum.hexdigest() == expected['sha256']


def download(url, target, expected, progress=lambda received, total: None, *, opener=None):
    """Keep partial bytes for retry; publish only the exact pinned file.

    Range support is optional. A server returning 200 to a Range request starts
    over safely. An invalid range, size, digest or insecure redirect is refused.
    """
    if not url.startswith('https://'):
        raise ValueError('Model downloads require HTTPS.')
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.partial')
    if target.is_symlink() or partial.is_symlink():
        raise ValueError('The model download destination cannot be a symbolic link.')
    check_cancelled()
    if verified(target, expected):
        progress(expected['size'], expected['size'])
        return target
    offset = partial.stat().st_size if partial.is_file() else 0
    if offset > expected['size']:
        partial.unlink(); offset = 0
    if offset == expected['size']:
        if verified(partial, expected):
            check_cancelled(); os.replace(partial, target)
            progress(offset, offset)
            return target
        partial.unlink(); offset = 0
    headers = {'User-Agent': 'BeatStitch/1 (model download)', 'Accept-Encoding': 'identity'}
    if offset:
        headers['Range'] = f'bytes={offset}-'
    request = urllib.request.Request(url, headers=headers)
    if opener is None:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
        response = urllib.request.urlopen(request, timeout=15, context=context)
    else:
        response = opener(request, timeout=15)
    with response:
        if not response.geturl().startswith('https://'):
            raise ValueError('The model publisher redirected to an insecure download.')
        status = response.status
        if status == 206:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if (not match or int(match[1]) != offset or int(match[2]) != expected['size'] - 1
                    or int(match[3]) != expected['size']):
                raise ValueError('The model server returned an invalid download range.')
        elif status == 200:
            offset = 0
        else:
            raise ValueError(f'The model server returned HTTP {status}.')
        remaining = response.headers.get('Content-Length')
        if remaining is not None and int(remaining) != expected['size'] - offset:
            raise ValueError('The model server returned an unexpected file size.')
        digest = hashlib.sha256()
        if offset:
            with partial.open('rb') as prior:
                while chunk := prior.read(CHUNK):
                    check_cancelled(); digest.update(chunk)
        count = offset
        progress(count, expected['size'])
        with partial.open('ab' if offset else 'wb') as output:
            while True:
                check_cancelled()
                chunk = response.read(CHUNK)
                check_cancelled()
                if not chunk:
                    break
                count += len(chunk)
                if count > expected['size']:
                    output.close(); partial.unlink(missing_ok=True)
                    raise ValueError('The downloaded model exceeds its pinned size.')
                output.write(chunk); digest.update(chunk)
                progress(count, expected['size'])
        if count != expected['size']:
            raise ValueError('The download stopped early. Retry to resume the saved bytes.')
        if digest.hexdigest() != expected['sha256']:
            partial.unlink(missing_ok=True)
            raise ValueError('Model verification failed. Retry to download a fresh copy.')
        check_cancelled()
        os.replace(partial, target)
    return target
