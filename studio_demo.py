"""Bundled demo songs copied to a stable source location, never into disposable cache."""
import copy
import hashlib
from pathlib import Path
import sys

import djmix
from studio_media import create_portable_copy
from studio_projects import read_project
from studio_source_identity import rebind_analysis


def bundled_project():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets/demo/Mix.djmix'


def fresh_session(template, stored):
    result = copy.deepcopy(template)
    for identity, track in result.tracks.items():
        found = stored.tracks[identity]
        if not Path(found.path).is_file() or found.fingerprint['sha256'] != track.fingerprint['sha256']:
            raise djmix.MixError('The stored demo audio is incomplete or changed.')
        rebind_analysis(track, track.fingerprint['source'], found.fingerprint['source'])
        track.path, track.fingerprint = found.path, copy.deepcopy(found.fingerprint)
    return result


def prepare_demo(data, progress=lambda message: None):
    source = bundled_project()
    if not source.is_file():
        raise djmix.MixError('The bundled demo is missing. Rebuild the app with its assets/demo folder included.')
    progress('Checking the bundled demo songs')
    template, _ = read_project(source, progress)
    key = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    parent = Path(data) / 'demo-sources'; parent.mkdir(parents=True, exist_ok=True)
    folder = parent / key
    number = 1
    while folder.exists():
        progress('Checking the existing demo copy')
        try:
            stored, _ = read_project(folder / 'Mix.djmix', progress)
            return folder / 'Mix.djmix', fresh_session(template, stored)
        except (OSError, ValueError, TypeError, KeyError, djmix.MixError) as error:
            if str(error) == 'Operation cancelled.': raise
            # Keep edited/damaged copies, including any project referring to them.
            # A fresh copy uses a separate location and cannot replace user audio.
            number += 1
            folder = parent / f'{key}-{number}'
    progress('Copying the demo songs to this computer')
    result = create_portable_copy(template, folder, progress)
    stored, _ = read_project(result['project'], progress)
    return Path(result['project']), fresh_session(template, stored)
