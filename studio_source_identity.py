"""Content identity for reconnecting moved audio without guessing from filenames."""
import hashlib
from pathlib import Path
import re

import djmix
from studio_cache import source_signature


def valid_fingerprint(value):
    return (isinstance(value, dict) and isinstance(value.get('sha256'), str)
            and re.fullmatch('[0-9a-f]{64}', value['sha256']) is not None
            and isinstance(value.get('source'), list) and len(value['source']) == 3)


def fingerprint(path, progress=lambda message: None):
    path = Path(path)
    before = source_signature(path)
    digest = hashlib.sha256(); count = 0
    progress(f'Checking audio identity: {path.name}')
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk); count += len(chunk)
            if count % (8 * 1024 * 1024) == 0:
                progress(f'Checking audio identity: {path.name} ({count * 100 // max(1, before[1])}%)')
    progress(f'Audio identity checked: {path.name}')
    if source_signature(path) != before:
        raise djmix.MixError(f'{path.name} changed during verification. Choose the file again.')
    return {'sha256': digest.hexdigest(), 'source': before}


def rebind_analysis(track, previous, current):
    """Move only annotations known to belong to the verified bytes."""
    for field in ('timing', 'structure', 'beat_grid', 'content', 'song_sections', 'key_analysis', 'opening_tempo'):
        value = getattr(track, field)
        if value and value.get('source') == previous:
            value['source'] = list(current)
