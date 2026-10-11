"""Persistent, disposable audio cache; original media and projects are never stored here."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

import djmix


PIPELINE_VERSION = 1  # Increment when processing, gain matching, or crossover semantics change.


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def source_signature(path):
    path = Path(path).resolve(); stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns]


class AudioCache:
    def __init__(self, root: Path, max_bytes=8 * 1024**3):
        self.root = root; self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.engine = [PIPELINE_VERSION, djmix.SR]
        for name in ('ffmpeg', 'ffprobe', 'rubberband'):
            executable = djmix.tool_path(name) if hasattr(djmix, 'tool_path') else shutil.which(name)
            self.engine.append(source_signature(executable) if executable else name)

    def measurement(self, source: Path):
        signature = source_signature(source)
        folder = self.root / 'levels'; folder.mkdir(exist_ok=True)
        path = folder / (digest([signature, self.engine]) + '.json')
        try:
            result = json.loads(path.read_text())
            if 'lufs' in result and 'true_peak_db' in result:
                return result
        except (OSError, ValueError, TypeError):
            pass
        result = djmix.meter(source)
        temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(result, allow_nan=False)); os.replace(temporary, path)
        return result

    def family(self, track, clip, target, gain):
        # Timeline position, overlap length, key labels, and bass envelopes do not
        # affect the transformed source audio. A disabled pitch setting is always zero.
        from studio_timing import mapping, RENDER_VERSION
        points = mapping(track, clip)
        settings = [self.engine, source_signature(track.path),
                    track.bpm, target, clip.shift, clip.cue_in, gain, True]
        if points is not None:
            settings.append(['timing', RENDER_VERSION, points.tolist(), clip.cue_out])
        return self.root / 'audio' / digest(settings)

    def get(self, family, start, end):
        candidates = []
        for path in family.glob('*/entry.json'):
            try:
                entry = json.loads(path.read_text())
                left, right = entry['start'], entry['end']
                if left <= start < end <= right and all(
                    (path.parent / name).stat().st_size == (right-left)*8
                    for name in ('low.raw', 'high.raw')):
                    candidates.append((right-left, left, path.parent))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if not candidates:
            return None
        _, left, directory = min(candidates)
        os.utime(directory / 'entry.json', None)
        return directory / 'low.raw', directory / 'high.raw', start-left

    def put(self, family, start, end, low, high):
        family.mkdir(parents=True, exist_ok=True)
        # Publish only complete entries; concurrent app instances cannot read half a file.
        with tempfile.TemporaryDirectory(prefix='.pending-', dir=family) as temporary:
            stage = Path(temporary)
            shutil.copyfile(low, stage / 'low.raw'); shutil.copyfile(high, stage / 'high.raw')
            (stage / 'entry.json').write_text(json.dumps({'start':start, 'end':end}))
            os.replace(stage, family / uuid.uuid4().hex)

    def prune(self):
        entries = []
        for path in (self.root / 'audio').glob('*/*/entry.json'):
            try:
                size = sum((path.parent / name).stat().st_size for name in ('low.raw','high.raw'))
                entries.append((path.stat().st_mtime_ns, size, path.parent))
            except OSError:
                continue
        for path in (self.root / 'auditions').glob('*/report.json'):
            try:
                size = sum(p.stat().st_size for p in path.parent.glob('*.wav'))
                entries.append((path.stat().st_mtime_ns, size, path.parent))
            except OSError:
                continue
        total = sum(item[1] for item in entries)
        for _, size, directory in sorted(entries):
            if total <= self.max_bytes:
                break
            try:
                shutil.rmtree(directory); total -= size
            except OSError:
                pass  # A concurrent Windows reader can temporarily keep an entry open.
