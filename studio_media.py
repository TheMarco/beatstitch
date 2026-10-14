"""Reviewable source relocation and portable project copies."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import djmix
from studio_cache import source_signature
from studio_engine import layout, load_track
from studio_source_identity import fingerprint, rebind_analysis, valid_fingerprint


def portable_value(session, project):
    """Serialize contained media relatively; runtime/session snapshots stay absolute."""
    value = session.to_dict()
    root = Path(project).resolve().parent
    if not session.tracks: return value
    for track in value['tracks'].values():
        path = Path(track['path']).resolve()
        try: relative = path.relative_to(root).as_posix()
        except ValueError: return session.to_dict()
        if not relative.startswith('media/') or not valid_fingerprint(track['fingerprint']):
            return session.to_dict()
        previous = track['fingerprint']['source']
        current = [relative, *previous[1:]]
        for field in ('timing', 'structure', 'beat_grid', 'content'):
            if track[field] and track[field].get('source') == previous:
                track[field]['source'] = current
        track['path'] = relative; track['fingerprint']['source'] = current
    value['_portable_paths'] = 1
    return value


def resolve_portable(value, project, progress=lambda message: None, verify=True):
    """Resolve only explicitly bundled paths; never reinterpret foreign absolute paths."""
    if value.get('_portable_paths') != 1: return value
    from pathlib import PurePosixPath
    root = Path(project).resolve().parent
    value = copy.deepcopy(value); identities = {}
    for track in value.get('tracks', {}).values():
        path = track.get('path'); known = track.get('fingerprint')
        if not isinstance(path, str) or not valid_fingerprint(known):
            raise djmix.MixError('This portable project is missing a source identity.')
        relative = PurePosixPath(path)
        if relative.is_absolute() or '..' in relative.parts or '\\' in path or not relative.parts or relative.parts[0] != 'media':
            raise djmix.MixError('A portable audio path must stay inside its media folder.')
        actual = (root / path).resolve()
        if not actual.is_relative_to(root / 'media'):
            raise djmix.MixError('A portable audio path points outside its media folder.')
        track['path'] = str(actual)
        if not verify or not actual.is_file(): continue
        if actual not in identities: identities[actual] = fingerprint(actual, progress)
        found = identities[actual]
        if found['sha256'] != known['sha256']:
            raise djmix.MixError(f'Bundled audio changed: {relative.name}. Restore an unchanged copy before opening this portable project.')
        for field in ('timing', 'structure', 'beat_grid', 'content'):
            if track.get(field) and track[field].get('source') == known['source']:
                track[field]['source'] = found['source']
        track['fingerprint'] = found
    return value


def portable_size(session):
    unique = {Path(track.path).resolve() for track in session.tracks.values()}
    missing = [p.name for p in unique if not p.is_file()]
    if missing:
        raise djmix.MixError('Reconnect missing source audio before making a portable copy: ' + ', '.join(missing[:5]))
    return sum(path.stat().st_size for path in unique), len(unique)


def portable_folder_name(name):
    name = re.sub('[^A-Za-z0-9 _-]', '-', name).strip(' .-')[:64] or 'Untitled'
    return 'DJ Mix - ' + name


def create_portable_copy(session, destination, progress=lambda message: None):
    """Copy all project sources, verify identity, and publish only into a new folder."""
    from studio_storage import require_space
    layout(session)
    destination = Path(destination).resolve()
    if destination.exists(): raise djmix.MixError('That folder already exists. Choose a new folder name; existing folders are never replaced.')
    total, count = portable_size(session)
    require_space(destination.parent, total + 16 * 1024 ** 2, 'copy this project and its source audio')
    draft = copy.deepcopy(session); copied = {}; created = []
    with tempfile.TemporaryDirectory(prefix='.dj-portable-', dir=destination.parent) as directory:
        stage = Path(directory); (stage / 'media').mkdir()
        for identity, track in draft.tracks.items():
            source = Path(track.path).resolve()
            if source not in copied:
                progress(f'Copying source {len(copied) + 1} of {count}: {track.name}')
                before = source_signature(source)
                name = re.sub('[^A-Za-z0-9_-]', '-', source.stem)[:70].strip('-') or 'song'
                relative = f'media/{len(copied) + 1:03d}-{name}{source.suffix.lower()}'
                digest = hashlib.sha256(); done = 0
                with source.open('rb') as src, (stage / relative).open('xb') as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk); digest.update(chunk); done += len(chunk)
                        if done % (8 * 1024 ** 2) == 0: progress(f'Copying {track.name}: {done * 100 // max(1, before[1])}%')
                if source_signature(source) != before:
                    raise djmix.MixError(f'{source.name} changed during copying. Try again after it stops changing.')
                found = {'sha256': digest.hexdigest(), 'source': [relative, *source_signature(stage / relative)[1:]]}
                copied[source] = relative, before, found
            relative, previous, found = copied[source]
            known = track.fingerprint
            if valid_fingerprint(known):
                if known['sha256'] != found['sha256']:
                    raise djmix.MixError(f'{track.name} no longer matches its saved audio identity. Reconnect its original before copying.')
                rebind_analysis(track, known['source'], found['source'])
            rebind_analysis(track, previous, found['source'])
            track.path, track.fingerprint = relative, copy.deepcopy(found)
        value = draft.to_dict(); value['_portable_paths'] = 1
        (stage / 'Mix.djmix').write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
        (stage / 'OPEN ME.txt').write_text('Open Mix.djmix in BeatStitch. Keep the media folder next to it.\n'
                                          'This folder contains the editable mix and copies of all its source songs.\n', encoding='utf-8')
        progress('Publishing the portable project')
        destination.mkdir()  # Exclusive creation closes the check/publish race.
        try:
            for name in ('media', 'OPEN ME.txt', 'Mix.djmix'):
                os.replace(stage / name, destination / name); created.append(destination / name)
        except Exception:
            for path in reversed(created):
                if path.is_dir(): shutil.rmtree(path)
                else: path.unlink(missing_ok=True)
            try: destination.rmdir()
            except OSError: pass
            raise
    return {'project': str(destination / 'Mix.djmix'), 'folder': str(destination), 'bytes': total, 'files': count}


def find_relinks(session, identities, folder, cache, progress=lambda message: None):
    from studio_process import check_cancelled
    wanted, matches, issues = {}, [], []
    for identity in identities:
        track = session.tracks[identity]
        known = track.fingerprint
        if not valid_fingerprint(known) and Path(track.path).is_file():
            known = fingerprint(track.path, progress)
        if not valid_fingerprint(known):
            issues.append(track.name + ': no saved content identity; choose and review its recording individually.')
            continue
        wanted.setdefault(known['source'][1], []).append((identity, known['sha256']))
    found = {}
    for path in Path(folder).rglob('*'):
        check_cancelled()
        if not path.is_file(): continue
        try:
            candidates = wanted.get(path.stat().st_size, [])
            if not candidates: continue
            actual = fingerprint(path, progress)['sha256']
            for identity, expected in candidates:
                if actual == expected: found.setdefault(identity, path)
        except OSError as error:
            issues.append(path.name + ': ' + str(error))
    for index, identity in enumerate(identities):
        check_cancelled()
        if identity not in found:
            if any(identity == item[0] for items in wanted.values() for item in items):
                issues.append(session.tracks[identity].name + ': no identical original found.')
            continue
        progress(f'Verifying {index + 1} of {len(identities)}: {session.tracks[identity].name}')
        try:
            draft = prepare_relink(session, identity, found[identity], cache, progress)
            if draft['verified']: matches.append(draft)
        except (OSError, djmix.MixError) as error:
            if str(error) == 'Operation cancelled.': raise
            issues.append(session.tracks[identity].name + ': ' + str(error))
    return {'matches': matches, 'issues': issues}


def prepare_relink(session, identity, candidate, cache, progress=lambda message: None):
    original = session.tracks[identity]
    candidate = Path(candidate).resolve()
    known = original.fingerprint
    if not valid_fingerprint(known) and Path(original.path).is_file():
        known = fingerprint(original.path, progress)
    found = fingerprint(candidate, progress)
    verified = valid_fingerprint(known) and known['sha256'] == found['sha256']
    if valid_fingerprint(known) and not verified:
        raise djmix.MixError('This file does not match the saved source. It may be a different recording, encoding or tag edit. '
                             'Choose an unchanged original or copy. Import a different version as a new song.')
    replacement = load_track(candidate, cache, verified=found, progress=progress)
    if abs(replacement.duration - original.duration) > .05:
        raise djmix.MixError(f'The chosen file is {replacement.duration:.2f} seconds; the source was {original.duration:.2f}. '
                             'Choose the same recording and version so existing cues stay correct.')
    draft = copy.deepcopy(session); track = draft.tracks[identity]
    track.path, track.duration = str(candidate), replacement.duration
    track.fingerprint, track.waveform = found, replacement.waveform
    reset = []
    if verified:
        rebind_analysis(track, known['source'], found['source'])
    else:
        track.timing, track.structure, track.beat_grid, track.content, track.song_sections = {}, {}, {}, {}, {}
        track.status = 'Relinked without a stored identity; check BPM, key and timing'
        for clip in draft.clips:
            if clip.track_id == identity and clip.stabilize:
                clip.stabilize = False; reset.append(clip.id)
    layout(draft)
    progress('Relink draft ready')
    return {'draft': draft, 'identity': identity, 'original_path': original.path,
            'verified': verified, 'reset_correction': reset, 'fingerprint': found}
