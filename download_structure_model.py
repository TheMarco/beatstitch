"""Fetch the pinned official Beat This model for offline analysis and packaging."""
import hashlib
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen

from studio_structure import MODEL_SHA256, MODEL_URL, model_path


def main():
    destination = model_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == MODEL_SHA256:
        print('Beat This model is already verified.')
        return
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        stage = Path(temporary) / destination.name
        checksum = hashlib.sha256()
        with urlopen(MODEL_URL, timeout=60) as response, stage.open('wb') as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
                checksum.update(chunk)
        if checksum.hexdigest() != MODEL_SHA256:
            raise ValueError('Model checksum mismatch; existing model was kept.')
        os.replace(stage, destination)
    print('Saved and verified:', destination)


if __name__ == '__main__':
    main()
