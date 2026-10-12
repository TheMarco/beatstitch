"""Copy the pinned local research weights into an optional application model pack."""
import argparse
import hashlib
from pathlib import Path
import shutil

from studio_song_sections import MANIFEST, model_directory, verify_models
from studio_storage import require_space


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research', type=Path, required=True)
    args = parser.parse_args()
    destination = model_directory()
    pending = []
    for name, item in MANIFEST.items():
        source, target = args.research / item['research_source'], destination / name
        with source.open('rb') as handle: digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        if digest != item['sha256']: raise SystemExit('Unexpected source bytes: ' + str(source))
        if target.is_file():
            with target.open('rb') as handle: current = hashlib.file_digest(handle, 'sha256').hexdigest()
            if current == digest: continue
        pending.append((source, target))
    require_space(destination, sum(source.stat().st_size for source, _ in pending), 'install section models')
    for source, target in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.pending')
        try:
            shutil.copyfile(source, temporary); temporary.replace(target)
        finally: temporary.unlink(missing_ok=True)
    verify_models(print)


if __name__ == '__main__': main()
