"""Conservative, overlap-local level matching without dynamic compression."""
from __future__ import annotations

import json
import math
import os
import uuid

import numpy as np
import soundfile as sf

import djmix
from studio_cache import digest
from studio_storage import require_space

VERSION = 1
LIMIT_DB = 3.0
DEADBAND_DB = 1.0
MIN_LUFS = -45.0


def active_overlaps(session, positions, start, end):
    return [(i, pos['start'], positions[i - 1]['end'])
            for i, pos in enumerate(positions) if i and session.clips[i].balance_levels
            and pos['start'] < end and positions[i - 1]['end'] > start]


def level(low, high, offset, frames, work, cache, family, local_start, progress):
    """Measure the same full overlap even when only part of it is being previewed."""
    path = None
    if cache:
        path = cache.root / 'levels' / (digest(['overlap-level', VERSION, family, local_start, frames]) + '.json')
        try:
            value = json.loads(path.read_text())['overlap_lufs']
            if value is None or isinstance(value, (int, float)) and math.isfinite(value):
                os.utime(path, None)
                return value
        except (OSError, ValueError, TypeError, KeyError):
            pass
    require_space(work, frames * 8 + 4096, 'measure the blend levels')
    temporary = work / ('overlap-' + uuid.uuid4().hex + '.wav')
    try:
        with sf.SoundFile(temporary, 'w', samplerate=djmix.SR, channels=2, subtype='FLOAT') as output:
            for at in range(0, frames, djmix.BLOCK):
                if at % (64 * djmix.BLOCK) == 0:
                    progress('Measuring the audio inside this overlap')
                selected = slice(offset + at, offset + min(frames, at + djmix.BLOCK))
                output.write(low[selected] + high[selected])
        measured = djmix.meter(temporary, require_loudness=False)['lufs']
        value = measured if math.isfinite(measured) else None
    finally:
        temporary.unlink(missing_ok=True)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            pending.write_text(json.dumps({'overlap_lufs': value}, allow_nan=False))
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)
    return value


def decision(outgoing, incoming, seconds):
    """Only attenuate the louder side; quiet passages never get amplified."""
    result = {'outgoing_lufs': outgoing, 'incoming_lufs': incoming,
              'outgoing_db': 0.0, 'incoming_db': 0.0, 'state': 'unchanged'}
    if seconds < 1:
        return {**result, 'state': 'uncertain', 'reason': 'Overlap is under one second; level balance was skipped.'}
    if any(value is None or not math.isfinite(value) or value < MIN_LUFS for value in (outgoing, incoming)):
        return {**result, 'state': 'uncertain', 'reason': 'One side is too quiet to compare reliably; level balance was skipped.'}
    difference = outgoing - incoming
    if abs(difference) <= DEADBAND_DB:
        return {**result, 'reason': 'Overlap levels are already close; no extra level change.'}
    side = 'outgoing' if difference > 0 else 'incoming'
    amount = min(LIMIT_DB, abs(difference) - DEADBAND_DB)
    return {**result, side + '_db': -amount, 'state': 'balanced',
            'reason': f'{side.capitalize()} song reduced by up to {amount:.1f} dB inside this overlap. '
                      + ('The 3 dB limit leaves some level difference; listen before applying.'
                         if abs(difference) > LIMIT_DB + DEADBAND_DB else 'Smooth ramps keep the handover gradual.')}


def plan(session, positions, prepared, families, overlaps, work, cache, progress):
    curves, reports = {}, []
    entries = {item[0]: item for item in prepared}
    for index, start, end in overlaps:
        progress(f'Checking blend {index} → {index + 1} levels')
        values = []
        for i in (index - 1, index):
            _, left, right, low, high, offset = entries[i]
            if not left <= start < end <= right:
                raise djmix.MixError('The complete overlap is needed to balance its levels.')
            values.append(level(low, high, start - left + offset, end - start, work, cache,
                                families[i], start - positions[i]['start'], progress))
        report = {'index': index, **decision(*values, (end - start) / djmix.SR)}
        reports.append(report)
        from studio_tempo_curve import session_curve
        curve = session_curve(session)
        midpoint = float(curve.to_reference((start + end) / (2 * djmix.SR)))
        # One bar around this overlap's middle, measured on the shared clock.
        half_bar = 120 / session.target_bpm
        bar_frames = (240 / session.target_bpm * djmix.SR if curve.identity else
                      float(curve.to_output(midpoint + half_bar) - curve.to_output(midpoint - half_bar)) * djmix.SR)
        ramp = min((end - start - 1) / 2, bar_frames)
        for i, side in ((index - 1, 'outgoing'), (index, 'incoming')):
            if report[side + '_db']:
                curves.setdefault(i, []).append((start, end, ramp, report[side + '_db']))
    return curves, reports


def envelope(samples, curves):
    gain_db = np.zeros(len(samples))
    for start, end, ramp, amount in curves:
        # Raised cosine ramps inside the blend; no gain changes outside it.
        phase = np.clip(np.minimum(samples - start, end - 1 - samples) / max(1, ramp), 0, 1)
        gain_db += amount * (.5 - .5 * np.cos(np.pi * phase))
    return np.power(10., gain_db / 20)


def recipe(curves, offset, stop):
    return [[start - offset, end - offset, ramp, amount] for start, end, ramp, amount in curves
            if start < stop and end > offset]


def report_text(reports, index, enabled):
    if not enabled:
        return 'Level balance off. Whole-song loudness matching still applies.'
    report = next((item for item in reports if item['index'] == index), None)
    return report['reason'] if report else 'Listen to measure this overlap. Only the louder side may be reduced, by up to 3 dB.'
