"""Recent project locations and non-destructive recovery snapshots."""
import json
from pathlib import Path

import djmix
from studio_engine import Session, layout

RECENT_LIMIT = 20
PAGE_SIZE = 20


def recent_paths(preferences):
    values = preferences.get('recent_projects', [])
    if not isinstance(values, list):
        values = []
    values = [*values, preferences.get('last_project')]
    result = []
    for value in values:
        if isinstance(value, str) and value:
            path = str(Path(value).expanduser().resolve())
            if path not in result:
                result.append(path)
    return result[:RECENT_LIMIT]


def remember_path(preferences, path):
    path = str(Path(path).resolve())
    return [path, *(p for p in recent_paths(preferences) if p != path)][:RECENT_LIMIT]


def is_recovery(path, data):
    path, data = Path(path).resolve(), Path(data).resolve()
    return path.parent == data and (path.name == 'autosave.djmix' or
            path.name.startswith(('recovery-', 'autosave-unreadable-')) and path.suffix == '.djmix')


def read_project(path, progress=lambda message: None, *, verify_media=True):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise djmix.MixError('This project file is incomplete or damaged. Try another recovery copy; this file has been kept.') from error
    if not isinstance(value, dict):
        raise djmix.MixError('This file does not contain a DJ Studio project.')
    from studio_media import resolve_portable
    resolved = resolve_portable(value, path, progress, verify=verify_media)
    return Session.from_dict(resolved), value


def write_snapshot(session, path, owner=None, reason=None):
    path = Path(path)
    value = session.to_dict()
    value['_recovery_project'] = str(Path(owner).resolve()) if owner else None
    if reason:
        value['_recovery_reason'] = reason
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def project_candidates(data, preferences, kind):
    """List locations without loading project JSON; recovery pages load on demand."""
    data = Path(data)
    if kind == 'recent':
        return [Path(p) for p in recent_paths(preferences) if not is_recovery(p, data)]
    paths = set(data.glob('recovery-*.djmix')) | set(data.glob('autosave-unreadable-*.djmix'))
    if (data / 'autosave.djmix').exists():
        paths.add(data / 'autosave.djmix')
    def modified(path):
        try: return path.stat().st_mtime_ns
        except OSError: return 0
    return sorted(paths, key=modified, reverse=True)


def inspect_project(path, kind):
    path = Path(path)
    record = dict(path=str(path), name=path.stem, kind=kind, modified=None, songs=0,
                  duration=0, missing=0, ready=False, error='', owner=None, reason='')
    try:
        record['modified'] = path.stat().st_mtime
        session, value = read_project(path, verify_media=False)
        positions = layout(session)
        from studio_tempo_curve import session_curve, output_positions
        positions = output_positions(session_curve(session), positions)
        record.update(name=session.name or path.stem, songs=len(session.clips),
                      duration=positions[-1]['end'] / djmix.SR if positions else 0, ready=True,
                      owner=value.get('_recovery_project'), reason=value.get('_recovery_reason', ''))
        used = {c.track_id for c in session.clips}
        record['missing'] = sum(not Path(session.tracks[k].path).is_file() for k in used)
    except FileNotFoundError:
        record['error'] = 'Project file is missing. Use Open a file… if you moved it.'
    except Exception as error:
        record['error'] = f'Cannot open this project: {error}'
    return record


def inspect_page(paths, kind, progress=lambda message: None):
    records = []
    for index, path in enumerate(paths):
        progress(f'Reading project {index + 1} of {len(paths)}')
        records.append(inspect_project(path, kind))
    progress('Project list ready')
    return records
