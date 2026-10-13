"""Bounded tempo-stabilization (drift) diagnostics; research only.

Two strictly separate evaluations share one frozen plan:

* Detector support: production ``studio_timing.analyze`` suggestions on
  constructed ``synthetic_timing`` fixtures and on the 25 ``real`` baselines.
  Constructed anchors are scored one-to-one against the frozen construction
  beats; real rows are descriptive only (no ground-truth timing exists).
* Oracle DSP: ``studio_timing.prepare`` exercised with a supplied oracle map
  built from the known nonuniform beats (no detector), at pitch 0 and +2,
  target BPM 128. This verifies the warp/render path, not detector accuracy.

Abstentions (``state != 'drift'`` or no map) are preserved, never converted
into a correction success, and no calibrated confidence is promoted from
synthetic or unverified real audio. Only short listening excerpts are kept;
decoded/render temporaries live in bounded temporary directories.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import tempfile
import time

import numpy as np
import soundfile as sf

import studio_timing
from detector_experiment_common import RATE, event_score, load_plan, verify_record
from detector_pilot import atomic_json, file_hash

FAMILY = 'drift'
REPORT_NAME = 'drift-report.json'
VERSION = 1
TARGET_BPM = 128.0
ORACLE_PITCHES = (0, 2)
TOLERANCES = (0.03, 0.07)
SOURCE_FILES = ('studio_timing.py', 'studio_timing_features.py', 'djmix.py',
                'studio_analysis_audio.py', 'detector_experiment_common.py',
                'detector_experiment_drift.py', 'detector_experiment_transient.py')
LIMITATIONS = [
    'Development evidence only; real baselines have no verified beat labels, so real rows are descriptive.',
    'Detector abstention/unsupported maps are preserved and never counted as correction success.',
    'Oracle DSP rows use a supplied known-beat map and verify rendering only, not detector accuracy.',
    'No calibrated confidence is promoted from synthetic or unverified real audio.',
]


def source_identities():
    root = Path(__file__).resolve().parent
    result = {}
    for name in SOURCE_FILES:
        path = root / name
        result[name] = file_hash(path) if path.is_file() else None
    return result


def _valid_events(values, duration):
    if values is None:
        return None
    array = np.asarray(values, dtype=float)
    if (array.ndim != 1 or not np.isfinite(array).all() or np.any(array < 0)
            or np.any(array >= duration) or (len(array) > 1 and np.any(np.diff(array) <= 0))):
        raise ValueError('Events must be finite, ordered, and inside the audio.')
    return [float(v) for v in array]


def _uniform_grid(beats):
    """Chord grid through the first/last known beats (the event clock)."""
    beats = np.asarray(beats, dtype=float)
    numbers = np.arange(len(beats))
    period = float((beats[-1] - beats[0]) / (len(beats) - 1)) if len(beats) > 1 else 60.0 / TARGET_BPM
    return beats[0] + numbers * period, 60.0 / period


def build_oracle_map(beats, duration):
    """Map known beats onto their chord-uniform grid; endpoints stay fixed.

    Returns ``(points, native_bpm, desired)`` where ``points`` is a valid
    ``studio_timing`` map ``[[source, corrected], ...]`` including
    ``[0, 0]`` and ``[duration, duration]``. Raises ``ValueError`` when the
    supplied beats cannot form a production-valid map, so no success is
    invented for an invalid oracle.
    """
    beats = _valid_events(beats, duration)
    if beats is None or len(beats) < 4 or not math.isfinite(duration) or duration <= 0:
        raise ValueError('Oracle map needs at least four known beats and a positive duration.')
    desired, native_bpm = _uniform_grid(beats)
    points = [[0.0, 0.0]] + [[float(s), float(d)] for s, d in zip(beats, desired)] + [[float(duration), float(duration)]]
    array = np.asarray(points, dtype=float)
    rates = np.diff(array[:, 1]) / np.diff(array[:, 0])
    if (np.any(np.diff(array, axis=0) <= 0) or np.max(np.abs(array[:, 0] - array[:, 1])) > 0.5
            or np.any((rates < 0.88) | (rates > 1.12))):
        raise ValueError('Known beats do not form a valid oracle timing map.')
    return points, float(native_bpm), [float(v) for v in desired]


def timing_jitter(beats, map_points):
    """Source vs corrected jitter (ms) against the known chord-uniform clock."""
    beats = np.asarray(beats, dtype=float)
    desired, native_bpm = _uniform_grid(beats)
    source_ms = (beats - desired) * 1000.0

    def stats(values):
        return {'median_ms': float(np.median(values)), 'p95_abs_ms': float(np.percentile(np.abs(values), 95)),
                'max_abs_ms': float(np.max(np.abs(values))), 'rms_ms': float(np.sqrt(np.mean(values ** 2)))}

    corrected = None
    if map_points:
        try:
            points = np.asarray(map_points, dtype=float)
            if (points.ndim == 2 and points.shape[1] == 2 and len(points) >= 2
                    and np.isfinite(points).all() and np.all(np.diff(points, axis=0) > 0)):
                corrected = stats((np.interp(beats, points[:, 0], points[:, 1]) - desired) * 1000.0)
        except (ValueError, TypeError):
            corrected = None
    return {'reference_bpm': float(native_bpm), 'source': stats(source_ms), 'corrected': corrected}


def _anchors(timing):
    beats = []
    for region in timing.get('regions') or []:
        beats.extend(region.get('beats') or [])
    return sorted(set(float(b) for b in beats))


def _scores(reference, predicted):
    if reference is None:
        return None
    return {f'{int(tol * 1000)}ms': event_score(reference, predicted, tol) for tol in TOLERANCES}


def _header(plan, identities):
    return {'version': VERSION, 'family': FAMILY, 'plan_sha256': plan['plan_sha256'],
            'runner_sha256': file_hash(Path(__file__)), 'source_sha256': identities,
            'target_bpm': TARGET_BPM, 'status': 'running', 'rows': [],
            'ui_promotion_allowed': False, 'calibrated_confidence_promoted': False,
            'limitations': LIMITATIONS}


def _resume(path, plan, identities):
    path = Path(path)
    if not path.exists():
        return _header(plan, identities)
    saved = __import__('json').loads(path.read_text())
    if (saved.get('plan_sha256') != plan['plan_sha256'] or saved.get('family') != FAMILY
            or saved.get('runner_sha256') != file_hash(Path(__file__))
            or saved.get('source_sha256') != identities
            or saved.get('target_bpm') != TARGET_BPM):
        raise ValueError('The experiment changed. Preserve this result and use a new output directory.')
    return saved


def _excerpt_original(record, cue_in, cue_out, destination):
    frames = int(round((cue_out - cue_in) * RATE))
    with sf.SoundFile(record['audio']) as stream:
        stream.seek(int(round(cue_in * RATE)))
        audio = stream.read(frames, dtype='float32', always_2d=True)
    if len(audio) != frames or not np.isfinite(audio).all():
        raise ValueError('Original excerpt is incomplete or non-finite.')
    sf.write(destination, audio, RATE, subtype='FLOAT')
    return frames


def measure_rendered_attacks(path, expected):
    """Measure rendered kick attacks near known events, without a beat tracker.

    This uses the existing zero-phase kick envelope, not its grid fitter or
    detector. The known source-clock offsets are reported separately so an
    envelope's acoustic attack bias cannot masquerade as a warp error.
    """
    from studio_timing_features import extract
    envelope = extract(Path(path))
    observed = []
    for event in expected:
        left = max(0, round((event - .08) * 1000))
        right = min(len(envelope), round((event + .08) * 1000) + 1)
        if right <= left: raise ValueError('Known attack is outside the rendered excerpt.')
        if float(np.max(envelope[left:right])) <= 1e-7:
            raise ValueError('No audible kick attack was measurable near the known event.')
        observed.append((left + int(np.argmax(envelope[left:right]))) / 1000)
    offsets = np.asarray(observed) - np.asarray(expected)
    return {'expected_seconds': list(map(float, expected)), 'observed_seconds': observed,
            'offset_median_ms': float(np.median(offsets) * 1000),
            'offset_p95_abs_ms': float(np.percentile(abs(offsets), 95) * 1000),
            'method': 'strongest kick-envelope attack within +/-80ms of a known event; no beat-grid detector'}


def _run_oracle(record, pitch, output_dir, progress):
    """One oracle DSP row; detector code is never consulted here."""
    import studio_timing as timing_module
    from studio_engine import Clip, Track

    expected = record.get('expected') or {}
    beats, duration = expected.get('beats'), record['frames'] / RATE
    points, native_bpm, _ = build_oracle_map(beats, duration)
    # Short excerpt: eight beat intervals starting at the fifth known beat.
    cue_in, cue_out = float(beats[4]), float(beats[12])
    label = f"oracle-{record['id']}-pitch{pitch:+d}"
    row = {'record_id': record['id'], 'record_name': record.get('name'),
           'evaluation': 'oracle_dsp', 'detector_involved': False, 'pitch_semitones': pitch,
           'target_bpm': TARGET_BPM, 'native_bpm': native_bpm, 'status': 'failed',
           'cue_seconds': [cue_in, cue_out], 'excerpts': {}}
    track = Track(id='oracle-' + record['id'], path=str(Path(record['audio']).resolve()),
                  name=record.get('name') or record['id'], duration=float(duration), bpm=native_bpm,
                  timing={'version': timing_module.VERSION, 'source': timing_module.signature(record['audio']),
                          'state': 'drift', 'bpm': native_bpm, 'map': points, 'regions': [], 'coverage': 0.0})
    clip = Clip(track_id=track.id, cue_in=cue_in, cue_out=cue_out, stabilize=True,
                transpose=bool(pitch), semitones=pitch)
    excerpt_dir = Path(output_dir) / 'excerpts'; excerpt_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='drift-oracle-', dir=Path(output_dir) / 'scratch') as folder:
        work = Path(folder)
        raw = timing_module.prepare(track, clip, TARGET_BPM, work, label, 0.0)
        raw = Path(raw)
        data = np.fromfile(raw, dtype='<f4')
        if data.size % 2 or not np.isfinite(data).all():
            raise ValueError('Corrected excerpt is incomplete or non-finite.')
        frames = int(data.size // 2)
        ratio = native_bpm / TARGET_BPM
        expected_frames = int(round((timing_module.corrected_time(track, clip, cue_out)
                                     - timing_module.corrected_time(track, clip, cue_in)) * ratio * RATE))
        if frames != expected_frames:
            raise ValueError(f'Corrected excerpt frame count mismatch: expected {expected_frames}, got {frames}.')
        corrected_path = excerpt_dir / f'{label}-corrected.wav'
        sf.write(corrected_path, data.reshape(-1, 2), RATE, subtype='FLOAT')
    original_path = excerpt_dir / f"oracle-{record['id']}-original.wav"
    if pitch == ORACLE_PITCHES[0] or not original_path.exists():
        original_frames = _excerpt_original(record, cue_in, cue_out, original_path)
    else:
        original_frames = int(round((cue_out - cue_in) * RATE))
    row['excerpts'] = {
        'original': {'path': str(original_path.relative_to(Path(output_dir))), 'frames': original_frames,
                     'sha256': file_hash(original_path), 'sample_rate': RATE},
        'corrected': {'path': str(corrected_path.relative_to(Path(output_dir))), 'frames': frames,
                      'sha256': file_hash(corrected_path), 'sample_rate': RATE}}
    row['source_identity'] = timing_module.signature(record['audio'])
    inside = np.asarray(beats[5:12], dtype=float)
    original_expected = inside - cue_in
    corrected_expected = (np.interp(inside, np.asarray(points)[:, 0], np.asarray(points)[:, 1])
                          - timing_module.corrected_time(track, clip, cue_in)) * ratio
    row['original_attacks'] = measure_rendered_attacks(original_path, original_expected)
    row['corrected_attacks'] = measure_rendered_attacks(corrected_path, corrected_expected)
    source_offset = np.asarray(row['original_attacks']['observed_seconds']) - original_expected
    output_offset = np.asarray(row['corrected_attacks']['observed_seconds']) - corrected_expected
    errors = (output_offset - source_offset * ratio) * 1000
    row['audio_timing_error_after_source_attack_bias_ms'] = errors.tolist()
    row['audio_timing_p95_abs_ms'] = float(np.percentile(abs(errors), 95))
    row['loudest_kick_peak_within_30ms'] = bool(np.max(abs(errors)) <= 30)
    # A transposed ringing kick can have a different loudest oscillation. Use
    # the deliberately constructed broadband attacks to verify its clock;
    # retain the low-band peak movement as a separate shape diagnostic.
    from detector_experiment_transient import markers
    before = markers(original_path, original_expected)
    after = markers(corrected_path, corrected_expected)
    source_attacks = np.asarray(before['times']) + cue_in
    mapped_attacks = (np.interp(source_attacks, np.asarray(points)[:, 0], np.asarray(points)[:, 1])
                      - timing_module.corrected_time(track, clip, cue_in)) * ratio
    clock_errors = (np.asarray(after['times']) - mapped_attacks) * 1000
    row['constructed_transient_clock'] = {'errors_ms':clock_errors.tolist(),
        'p95_abs_ms':float(np.percentile(abs(clock_errors),95)),
        'max_abs_ms':float(np.max(abs(clock_errors))),
        'source_markers':before, 'corrected_markers':after}
    row['audio_timing_within_30ms'] = bool(np.max(abs(clock_errors)) <= 30)
    row['verification_scope'] = ('Constructed broadband beat-marker clock and sample counts. '
                                 'Loudest kick-oscillation movement is retained separately; perceived sound quality remains unverified.')
    row['status'] = 'completed' if row['audio_timing_within_30ms'] else 'failed'
    if row['status'] == 'failed':
        row['error'] = 'Rendered known kick attacks exceed the predeclared 30ms timing bound.'
    return row


def run(plan_path, output_dir, limit=None, nominal_bpm=TARGET_BPM, progress=print):
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError('limit must be a positive integer.')
    if not math.isfinite(nominal_bpm) or not 20 <= nominal_bpm <= 300:
        raise ValueError('nominal_bpm must be within 20..300.')
    plan = load_plan(plan_path)  # frozen-plan hash first
    identities = source_identities()
    records = [r for r in plan['records'] if r.get('kind') in ('synthetic_timing', 'real')]
    if limit is not None:
        records = records[:limit]
    for record in records:  # baseline hashes before any inference/rendering
        verify_record(record)
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'scratch').mkdir(parents=True, exist_ok=True)
    report_path = output_dir / REPORT_NAME
    report = _resume(report_path, plan, identities)
    report['status'] = 'running'
    cache_dir = output_dir / 'cache' / 'timing'; cache_dir.mkdir(parents=True, exist_ok=True)
    existing = {}
    for old in report['rows']:
        pitch = old.get('pitch_semitones') if old.get('evaluation') == 'oracle_dsp' else None
        existing[(old['record_id'], old.get('evaluation'), pitch)] = old

    def save(status='running'):
        report['status'] = status
        counts = {s: sum(1 for r in report['rows'] if r['status'] == s)
                  for s in ('completed', 'uncertain', 'failed')}
        nonuniform = [r for r in records if (r.get('expected') or {}).get('beats')
                      and (r.get('expected') or {}).get('bpm') is None]
        report['summary'] = {**counts, 'rows': len(report['rows']),
                             'denominator_detector_rows': len(records),
                             'denominator_oracle_rows': len(nonuniform) * len(ORACLE_PITCHES),
                             'attempted_rows': len(records) + len(nonuniform) * len(ORACLE_PITCHES)}
        atomic_json(report_path, report)

    def store(row):
        pitch = row.get('pitch_semitones') if row.get('evaluation') == 'oracle_dsp' else None
        key = (row['record_id'], row.get('evaluation'), pitch)
        if key in existing:
            report['rows'][report['rows'].index(existing[key])] = row
        else:
            report['rows'].append(row)
        existing[key] = row
        save()

    for record in records:
        key = (record['id'], 'detector', None)
        if key not in existing or existing[key]['status'] not in ('completed', 'uncertain'):
            started = time.monotonic()
            duration = record['frames'] / RATE
            expected = record.get('expected') or {}
            known = _valid_events(expected.get('beats'), duration) if expected.get('beats') else None
            if expected.get('bpm'):
                bpm = float(expected['bpm'])
            elif known and len(known) > 1:
                bpm = float(_uniform_grid(known)[1])
            else:
                bpm = float(plan.get('bpm_hints', {}).get(record['id'], nominal_bpm))
            row = {'record_id': record['id'], 'record_name': record.get('name'),
                   'record_kind': record.get('kind'), 'evaluation': 'detector',
                   'detector_involved': True, 'status': 'failed', 'nominal_bpm': bpm,
                   'reference_type': 'constructed' if known else 'real_descriptive_no_ground_truth',
                   'correction_supported': False, 'timing': None, 'beat_scores': None,
                   'jitter': None, 'audio_sha256': record.get('audio_sha256')}
            try:
                timing = studio_timing.analyze(Path(record['audio']), bpm, float(duration),
                                               cache_dir, progress)
                row['timing'] = timing
                anchors = _anchors(timing)
                row['anchors_seconds'] = anchors
                if known:
                    row['beat_scores'] = _scores(known, anchors)
                    row['jitter'] = timing_jitter(known, timing.get('map'))
                supported = timing.get('state') == 'drift' and bool(timing.get('map')) and bool(anchors)
                row['correction_supported'] = bool(supported)
                row['detector_state'] = timing.get('state')
                row['analysis_supported'] = timing.get('state') in ('steady', 'drift')
                row['status'] = 'completed' if row['analysis_supported'] else 'uncertain'
                if not row['analysis_supported']:
                    row['uncertainty'] = 'Production analysis abstained or offered no valid correction map; preserved as unsupported.'
                elif timing.get('state') == 'steady':
                    row['interpretation'] = 'No supported drift correction was offered; this does not verify every beat is steady.'
            except Exception as error:
                if str(error) == 'Operation cancelled.':
                    raise
                row['status'] = 'failed'; row['error'] = str(error)[:1800]
            row['wall_seconds'] = float(time.monotonic() - started)
            store(row)
            progress(f"{record['id']}/detector: {row['status']}")
        # Oracle DSP is independent of the detector outcome above.
        expected = record.get('expected') or {}
        if expected.get('beats') and expected.get('bpm') is None:
            for pitch in ORACLE_PITCHES:
                key = (record['id'], 'oracle_dsp', pitch)
                if key in existing and existing[key]['status'] in ('completed', 'uncertain'):
                    continue
                started = time.monotonic()
                try:
                    row = _run_oracle(record, pitch, output_dir, progress)
                except Exception as error:
                    if str(error) == 'Operation cancelled.':
                        raise
                    row = {'record_id': record['id'], 'record_name': record.get('name'),
                           'evaluation': 'oracle_dsp', 'detector_involved': False,
                           'pitch_semitones': pitch, 'target_bpm': TARGET_BPM,
                           'status': 'failed', 'error': str(error)[:1800]}
                row['wall_seconds'] = float(time.monotonic() - started)
                store(row)
                progress(f"{record['id']}/oracle pitch{pitch:+d}: {row['status']}")
    save('completed' if all(r['status'] != 'failed' for r in report['rows']) else 'partial')
    return report


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--nominal-bpm', type=float, default=TARGET_BPM,
                        help='Assumed BPM for real baselines only; not a ground-truth label.')
    args = parser.parse_args(arguments)
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    try:
        report = run(args.plan, args.out, args.limit, args.nominal_bpm,
                     lambda message: print(message, flush=True))
    except Exception as error:
        print(str(error), flush=True)
        return 1
    print(f"Saved {args.out / REPORT_NAME} rows={report['summary']['rows']} failed={report['summary']['failed']}")
    return 1 if report['summary']['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
