"""Project-local collections and exact-file duplicate checks for the song library."""
from pathlib import Path

import djmix
from studio_cache import source_signature
from studio_source_identity import valid_fingerprint


def validate_collections(collections, tracks):
    if not isinstance(collections, dict):
        raise djmix.MixError('Invalid library collections.')
    names = set()
    for name, members in collections.items():
        if (not isinstance(name, str) or not name.strip() or len(name) > 80 or any(c in name for c in '\n\r\t') or
                name != name.strip() or name.casefold() in names or
                not isinstance(members, list) or not all(isinstance(v, str) and v in tracks for v in members) or
                len(set(members)) != len(members)):
            raise djmix.MixError('Invalid library collection name or song list.')
        names.add(name.casefold())


def collection_name(text, existing, replacing=None):
    name = text.strip()
    if not name or len(name) > 80 or any(c in name for c in '\n\r\t'):
        raise djmix.MixError('Use a collection name of 1–80 characters on one line.')
    if any(key != replacing and key.casefold() == name.casefold() for key in existing):
        raise djmix.MixError('A collection already has that name. Choose another name.')
    return name


def duplicate_track(track, candidates):
    """Only skip identical file bytes while the existing source is still usable.

    Similar names, different encodings and a missing/changed prior source are not
    duplicates. No file is deleted or silently relinked.
    """
    return duplicate_fingerprint(track.fingerprint, candidates)


def duplicate_fingerprint(value, candidates):
    if not valid_fingerprint(value):
        return None
    for previous in candidates.values():
        if not valid_fingerprint(previous.fingerprint):
            continue
        if previous.fingerprint['sha256'] != value['sha256']:
            continue
        try:
            if source_signature(Path(previous.path)) == previous.fingerprint['source']:
                return previous
        except OSError:
            continue
    return None
