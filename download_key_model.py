#!/usr/bin/env python3
"""Install the pinned official OpenKeyScan checkpoint and MIT license."""
import hashlib
from pathlib import Path
import urllib.request

from studio_key_model import (MODEL_BYTES, MODEL_FILE, SOURCE_REVISION, MODEL_SHA256,
                              LICENSE_FILE, LICENSE_SHA256)


def main():
    folder = Path(__file__).resolve().parent / 'assets' / 'key'
    folder.mkdir(parents=True, exist_ok=True)
    base = f'https://raw.githubusercontent.com/rekordcloud/openkeyscan-analyzer/{SOURCE_REVISION}/'
    for remote, local, size, digest in (
        ('checkpoints/' + MODEL_FILE, MODEL_FILE, MODEL_BYTES, MODEL_SHA256),
        ('LICENSE', LICENSE_FILE, 1073, LICENSE_SHA256),
    ):
        destination = folder / local
        if destination.exists():
            data = destination.read_bytes()
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f'Existing {destination} differs from the official file; kept unchanged.')
        else:
            with urllib.request.urlopen(base + remote, timeout=60) as response:
                data = response.read(size + 1)
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f'Official download failed verification: {remote}')
            with destination.open('xb') as handle:
                handle.write(data)
        print(f'Verified {destination}')


if __name__ == '__main__':
    main()
