"""Fetch the pinned official PANNs CNN10 weights for offline passage analysis."""
import hashlib
import argparse
import os
import tempfile
from pathlib import Path
from urllib.request import urlopen

from studio_content import MODEL_SHA256, MODEL_SIZE, MODEL_URL, model_path, verify_model


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, help='Checkpoint file for a source/test build; normal user downloads use Downloads & features.')
    options = parser.parse_args(argv)
    destination = options.destination or model_path(); destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        try:
            verify_model(destination); print('Passage detector already verified.'); return
        except ValueError: pass
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        stage = Path(temporary) / destination.name
        digest = hashlib.sha256(); count = 0
        with urlopen(MODEL_URL, timeout=60) as response, stage.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > MODEL_SIZE: raise ValueError('Unexpected model size; existing model kept.')
                output.write(chunk); digest.update(chunk)
        if count != MODEL_SIZE or digest.hexdigest() != MODEL_SHA256:
            raise ValueError('Model verification failed; existing model kept.')
        os.replace(stage, destination)
    print('Passage detector saved and verified:', destination)


if __name__ == '__main__': main()
