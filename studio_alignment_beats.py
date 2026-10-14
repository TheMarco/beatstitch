"""Optional local beat observations for a reusable alignment excerpt."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid

import numpy as np

from studio_alignment_audio import RATE
from studio_storage import managed_cache


def available():
    from studio_structure import model_path
    return model_path().is_file()


@managed_cache
def observations(report, progress, cache_dir=None):
    """Infer once per prepared pair; manual offsets only move these observations."""
    from studio_structure import MODEL_SHA256, model_path
    from studio_process import check_cancelled
    saved = report.get('beat_observations')
    if saved and saved.get('model_sha256') == MODEL_SHA256:
        report['markers'] = [np.asarray(row) for row in saved['beats']]
        report['marker_label'] = 'detected beats'
        return saved['beats']
    from studio_cache import digest
    key = [1, MODEL_SHA256, report['identity'], report['cue'], report['frames'], report['margin']]
    destination = Path(cache_dir) / 'alignment-beats' / (digest(key) + '.json') if cache_dir else None
    if destination and destination.is_file() and destination.stat().st_size < 128 * 1024:
        try:
            cached = json.loads(destination.read_text())
            rows = cached['beats']
            if cached['key'] != key or len(rows) != 2:
                raise ValueError('Different beat observations')
            for row in rows:
                values = np.asarray(row, dtype=float)
                if (values.ndim != 1 or not np.isfinite(values).all() or np.any(np.diff(values) <= 0)
                        or np.any(values < 0) or np.any(values >= report['frames'] / RATE)):
                    raise ValueError('Invalid cached beat positions')
            report['beat_observations'] = {'model_sha256': MODEL_SHA256, 'beats': rows}
            report['markers'] = [np.asarray(row) for row in rows]
            report['marker_label'] = 'detected beats'
            progress('Reusing measured beat speeds and positions')
            return rows
        except (OSError, ValueError, KeyError, TypeError):
            pass
    path = model_path()
    if not path.is_file():
        return None
    check_cancelled()
    if hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError('The beat detector differs from its verified download. Download it again.')
    import torch
    from beat_this.inference import Audio2Beats
    threads = torch.get_num_threads()
    torch.set_num_threads(4)
    try:
        progress('Measuring the actual beat rate and positions in A and B')
        runner = Audio2Beats(str(path), device='cpu')
        rows = []
        for side in report['sides']:
            check_cancelled()
            beats, _ = runner(side['full'], RATE)
            shifted = np.asarray(beats, dtype=float) - report['margin'] / RATE
            rows.append(shifted[(shifted >= 0) & (shifted < report['frames'] / RATE)].tolist())
        check_cancelled()
    finally:
        torch.set_num_threads(threads)
    report['beat_observations'] = {'model_sha256': MODEL_SHA256, 'beats': rows}
    report['markers'] = [np.asarray(row) for row in rows]
    report['marker_label'] = 'detected beats'
    if destination:
        from studio_storage import require_space
        data = json.dumps({'key': key, 'beats': rows}, allow_nan=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        require_space(destination.parent, len(data.encode()) + 65536, 'save reusable beat observations')
        temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(data)
            check_cancelled()
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return rows
