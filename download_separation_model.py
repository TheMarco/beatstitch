"""Fetch the pinned official Demucs model; inference itself is offline."""
import argparse
from pathlib import Path
import urllib.request

from prepare_separation_trial import MODEL_BYTES, MODEL_FILE, MODEL_REPOSITORY, MODEL_REVISION, MODEL_SHA256, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=Path('outputs/stem-separation-trial/models'))
    folder = parser.parse_args().destination
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / MODEL_FILE
    if target.is_file() and target.stat().st_size == MODEL_BYTES and sha256(target) == MODEL_SHA256:
        print('Verified separation model is already available.'); return
    pending = folder / (MODEL_FILE + '.pending')
    url = f'https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/{MODEL_FILE}'
    try:
        with urllib.request.urlopen(url, timeout=60) as response, pending.open('wb') as handle:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MODEL_BYTES: raise ValueError('Unexpected model size.')
                handle.write(chunk)
        if pending.stat().st_size != MODEL_BYTES or sha256(pending) != MODEL_SHA256:
            raise ValueError('The downloaded model failed verification.')
        pending.replace(target)
    finally:
        pending.unlink(missing_ok=True)
    print('Verified separation model is ready for local use.')


if __name__ == '__main__': main()
