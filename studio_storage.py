"""Bounded disposable storage with live-job and playback protection."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import math
import uuid

from filelock import FileLock, Timeout

import djmix

GIB = 1024 ** 3
RESERVE = 256 * 1024 ** 2
_HEX = re.compile(r'^[0-9a-f]{32,64}$')
_WORK = ContextVar('dj_storage_work', default=())


def mark_work(lock):
    return _WORK.set((*_WORK.get(), str(Path(lock.lock_file).parent.resolve())))


def unmark_work(token):
    _WORK.reset(token)


def cache_root(path):
    path = Path(path).resolve()
    return next((p.parent for p in (path, *path.parents)
                 if p.name in ('audio-cache', 'library-cache', 'timing-cache', 'structure-cache', 'renders')), path)


@contextmanager
def work_scope(root, progress=lambda message: None):
    root = Path(root).resolve()
    if str(root) in _WORK.get():
        yield
        return
    lock = StorageManager(root).work_lock()
    while True:
        try:
            lock.acquire(timeout=.1)
            break
        except Timeout:
            progress('Waiting for another audio or analysis job')
    token = mark_work(lock)
    try:
        yield
    finally:
        unmark_work(token)
        lock.release()


def managed_cache(function):
    """Share the app's work lock in nested cache calls and standalone API use."""
    signature = inspect.signature(function)

    @wraps(function)
    def guarded(*args, **kwargs):
        values = signature.bind(*args, **kwargs).arguments
        path = values.get('cache_dir') or values.get('audio_cache') or values.get('root')
        if path is None:
            return function(*args, **kwargs)
        with work_scope(cache_root(path), values.get('progress', lambda message: None)):
            return function(*args, **kwargs)
    return guarded


def size_text(value):
    return f'{value / GIB:.2f} GB' if value >= GIB else f'{value / 1024 ** 2:.1f} MB'


def require_space(folder, required, purpose='prepare audio'):
    folder = Path(folder)
    while not folder.exists():
        folder = folder.parent
    free = shutil.disk_usage(folder).free
    if free < int(required) + RESERVE:
        raise djmix.MixError(f'Not enough free space to {purpose}. About {size_text(int(required) + RESERVE)} is needed '
                            f'on this drive; {size_text(free)} is free. Open File → Storage and cache, '
                            'clear unused files, or choose another export drive.')


class StorageManager:
    def __init__(self, data):
        self.data = Path(data).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.pin_lock = None
        self.pin_path = None

    def work_lock(self):
        return FileLock(self.data / '.cache-work.lock', thread_local=False)

    def gate(self):
        return FileLock(self.data / '.cache-control.lock', timeout=5)

    def limit(self):
        try:
            preferences = json.loads((self.data / 'preferences.json').read_text())
            gb = float(preferences.get('cache_gb', 2)) if isinstance(preferences, dict) else 2
            if not math.isfinite(gb):
                return 2 * GIB
            return max(.25, min(64, gb)) * GIB
        except (OSError, ValueError, TypeError):
            return 2 * GIB

    def pin(self, paths, gate_held=False):
        """Keep source files and the currently loaded player file out of eviction."""
        with nullcontext() if gate_held else self.gate():
            if self.pin_lock is None:
                folder = self.data / '.cache-pins'; folder.mkdir(exist_ok=True)
                self.pin_path = folder / (uuid.uuid4().hex + '.json')
                self.pin_lock = FileLock(self.pin_path.with_suffix('.lock'), thread_local=False)
                self.pin_lock.acquire(timeout=0)
            temporary = self.pin_path.with_suffix('.tmp')
            temporary.write_text(json.dumps([str(Path(p).resolve()) for p in paths]))
            os.replace(temporary, self.pin_path)

    def close(self):
        if self.pin_lock is not None:
            with self.gate():
                self.pin_lock.release()
                self.pin_path.unlink(missing_ok=True)
                self.pin_path.with_suffix('.lock').unlink(missing_ok=True)
                self.pin_lock, self.pin_path = None, None

    def _protected(self):
        protected = set()
        for marker in (self.data / '.cache-pins').glob('*.json'):
            lock = FileLock(marker.with_suffix('.lock'))
            try:
                lock.acquire(timeout=0)
            except Timeout:
                try:
                    protected.update(Path(p) for p in json.loads(marker.read_text()))
                except (OSError, ValueError, TypeError):
                    # An unreadable live pin is not permission to remove audio.
                    return {self.data}
            else:
                lock.release()
                marker.unlink(missing_ok=True)
                marker.with_suffix('.lock').unlink(missing_ok=True)
        return protected

    def entries(self):
        """Only app-owned cache layouts; projects, exports and model assets are excluded."""
        entries, covered = [], []

        def add(path, group, marker=None):
            if any(path == p or p in path.parents for p in covered):
                return
            try:
                if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != self.data and self.data in p.parents):
                    return
                paths = list(path.rglob('*')) if path.is_dir() else [path]
                if any(p.suffix == '.djmix' for p in paths):
                    return
                files = [p for p in paths if p.is_file() and not p.is_symlink()]
                size = sum(p.stat().st_size for p in files)
                stamp = (marker or path).stat().st_mtime
                entries.append({'path': path, 'group': group, 'bytes': size, 'stamp': stamp})
                covered.append(path)
            except OSError:
                pass

        audio = self.data / 'audio-cache'
        for marker in (audio / 'audio').glob('*/*/entry.json'):
            if _HEX.fullmatch(marker.parent.name) and _HEX.fullmatch(marker.parent.parent.name):
                add(marker.parent, 'Processed songs', marker)
        for kind in ('auditions', 'transitions', 'grid-auditions', 'alternatives'):
            for marker in (audio / kind).glob('*/report.json'):
                if _HEX.fullmatch(marker.parent.name):
                    add(marker.parent, 'Listening audio', marker)
            for folder in (audio / kind).glob('*'):
                if folder.is_dir() and _HEX.fullmatch(folder.name) and not (folder / 'report.json').exists():
                    add(folder, 'Working files')
        for path in (audio / 'assembled').glob('*.raw'):
            if _HEX.fullmatch(path.stem):
                add(path, 'Assembled audio')
        for path in (audio / 'effects').glob('*.raw'):
            if _HEX.fullmatch(path.stem):
                add(path, 'Transition effects')
        for path in (audio / 'effects').glob('*.tmp'):
            if re.fullmatch(r'[0-9a-f]{64}\.[0-9a-f]{32}\.tmp', path.name):
                add(path, 'Working files')
        for marker in (self.data / 'renders').glob('*/report.json'):
            if _HEX.fullmatch(marker.parent.name):
                add(marker.parent, 'Prepared mixes', marker)
        for folder in (self.data / 'renders').glob('*'):
            if folder.is_dir() and _HEX.fullmatch(folder.name) and not (folder / 'report.json').exists():
                add(folder, 'Working files')
        for path in (self.data / 'renders').glob('*.wav'):
            if re.fullmatch(r'(mix|preview)-\d+\.wav', path.name):
                add(path, 'Prepared mixes')
        for root in (self.data / 'library-cache', self.data / 'timing-cache', self.data / 'structure-cache', audio / 'levels', audio / 'energy'):
            for path in root.glob('*'):
                if path.is_file() and path.suffix in ('.json', '.npz', '.npy'):
                    add(path, 'Analysis and waveforms')
        for path in (audio / 'energy').glob('*.tmp'):
            if re.fullmatch(r'[0-9a-f]{64}\.[a-z0-9_]{8}\.tmp', path.name):
                add(path, 'Working files')
        for path in (self.data / 'timing-cache').glob('*.tmp'):
            if re.fullmatch(r'[0-9a-f]{64}\.[0-9a-f]{32}\.tmp', path.name):
                add(path, 'Working files')
        # Live work holds the cross-process work lock. Once acquired for cleanup,
        # leftover app-owned working folders belong to interrupted jobs.
        for root in (audio, self.data / 'renders', self.data / 'structure-cache', self.data / 'timing-cache'):
            if root.is_symlink():
                continue
            for path in root.rglob('*'):
                if path.is_dir() and path.name.startswith(('.pending-', '.blend-', '.audition-', 'dj-render-', 'section-work-')):
                    add(path, 'Working files')
        return entries

    def inspect(self):
        entries = self.entries()
        groups = {}
        for entry in entries:
            groups[entry['group']] = groups.get(entry['group'], 0) + entry['bytes']
        return {'groups': groups, 'bytes': sum(groups.values()), 'limit': self.limit(),
                'free': shutil.disk_usage(self.data).free}

    def prune(self, clear=False):
        removed, count = 0, 0
        try:
            with self.gate(), self.work_lock().acquire(timeout=0):
                protected = self._protected()
                entries = self.entries()
                total = sum(e['bytes'] for e in entries)
                limit = 0 if clear else self.limit()
                for entry in sorted(entries, key=lambda e: e['stamp']):
                    if total <= limit and entry['group'] != 'Working files':
                        continue
                    path = entry['path']
                    if any(path == p or path in p.parents or p in path.parents for p in protected):
                        continue
                    try:
                        shutil.rmtree(path) if path.is_dir() else path.unlink()
                    except OSError:
                        continue
                    total -= entry['bytes']; removed += entry['bytes']; count += 1
                return {'removed': removed, 'count': count, 'remaining': total, 'busy': False}
        except Timeout:
            return {'removed': 0, 'count': 0, 'remaining': None, 'busy': True}
