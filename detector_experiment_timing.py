"""Timing experiment: Beat This (raw + coherent grid) vs librosa, same audio.

Research-only. Constructed records are scored against their frozen
construction reference; real records are scored only for transformation
consistency against their own baseline (never as ground truth). Raw peak
strengths and coherent-grid support/coverage are not accuracy or confidence.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import time

import numpy as np
import soundfile as sf

from detector_experiment_common import (
    RATE, applicable_cases, case_audio, event_score, load_plan, map_events,
    resume_report)
from detector_pilot import atomic_json, digest, file_hash

REPORT_NAME = 'timing-report.json'
BACKENDS = ('beat_this_raw', 'beat_this_grid', 'librosa')
FRAME_QUANTIZATION = {'beat_this_raw': 1 / 50, 'beat_this_grid': 1 / 50,
                      'librosa': 128 / 22050}
MAX_EVENTS = 20000
DRIFT_CORRECTION_API = (
    'studio_timing.analyze(path, bpm, duration, cache, progress), '
    'studio_timing.summarize(windows, duration) and studio_timing.fit_grid(onset, nominal_bpm) '
    'can be scored independently against constructed nonuniform beats by comparing '
    'summarize() region beats/grid to the frozen construction reference with '
    'detector_experiment_common.event_score; not applied here, so reference '
    'definitions are unchanged. Primary review should confirm this API surface.')


def _validate_events(values, duration, label):
    if values is None:
        return None
    array = np.asarray(values, dtype=float)
    if (array.ndim != 1 or len(array) > MAX_EVENTS or not np.isfinite(array).all()
            or np.any(array < 0) or np.any(array >= duration)
            or (len(array) > 1 and np.any(np.diff(array) <= 0))):
        raise ValueError(f'{label} must be finite, ordered events inside the audio.')
    return [float(v) for v in array]


def _validate_bpm(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) and 20 <= value <= 300 else None


def tempo_metrics(expected, observed):
    """Strict within-2% check; half/double ambiguity flagged separately."""
    expected, observed = _validate_bpm(expected), _validate_bpm(observed)
    if expected is None or observed is None:
        return {'expected_bpm': expected, 'observed_bpm': observed,
                'error_percent': None, 'within_2_percent': None,
                'tempo_relation': 'unknown', 'half_double_ambiguity': False,
                'status': 'uncertain'}
    ratio = observed / expected
    within = abs(ratio - 1) <= 0.02
    relation = ('consistent' if within else 'half' if 0.48 <= ratio <= 0.52
                else 'double' if 1.90 <= ratio <= 2.10 else 'other')
    return {'expected_bpm': expected, 'observed_bpm': observed,
            'error_percent': float((ratio - 1) * 100), 'within_2_percent': bool(within),
            'tempo_relation': relation, 'half_double_ambiguity': relation in ('half', 'double'),
            'status': 'completed'}


def _scores(reference, predicted):
    if reference is None or predicted is None:
        return None
    return {f'{int(tol * 1000)}ms': event_score(reference, predicted, tol)
            for tol in (0.03, 0.07)}


def _source_gaps(beats):
    if not beats or len(beats) < 4:
        return []
    intervals = np.diff(beats)
    median = float(np.median(intervals))
    if not math.isfinite(median) or median <= 0:
        return []
    return [{'start': float(beats[i]), 'end': float(beats[i + 1])}
            for i, gap in enumerate(intervals) if gap > 1.5 * median][:20]


def _weaknesses(record, case, backend, kind, times, detail):
    return {'record_id': record['id'], 'record_name': record.get('name'),
            'case_id': case['id'], 'backend': backend, 'kind': kind,
            'times_seconds': [float(t) for t in times][:10], 'detail': detail}


def _run_beat_this(path, duration, cache_dir, progress):
    """Reuse production analyze_tempo/analyze_song and the exact saved NPZ."""
    from studio_cache import source_signature
    import studio_structure
    from studio_structure import MODEL_SHA256, analyze_song, analyze_tempo
    tempo = analyze_tempo(Path(path), float(duration), Path(cache_dir), progress)
    observation_version = getattr(studio_structure, 'OBSERVATION_VERSION', 1)
    raw_path = Path(cache_dir) / (digest([observation_version, MODEL_SHA256, source_signature(path)]) + '.npz')
    with np.load(raw_path, allow_pickle=False) as saved:
        raw = {name: saved[name].tolist()
               for name in ('beats', 'downbeats', 'beat_strength', 'downbeat_strength')}
    bpm = _validate_bpm(tempo.get('bpm'))
    grid = {}
    if bpm is not None:
        grid = analyze_song(Path(path), bpm, float(duration), Path(cache_dir), progress)
    return {'tempo': tempo, 'raw': raw, 'grid': grid}


def _run_librosa(path, duration):
    """Production librosa path; it has no downbeat observations."""
    from analyze_audio import analyze
    result = analyze(Path(path), 0.0, float(duration))
    return {'bpm': result.get('tempo_tracker_estimate'),
            'beats': result.get('beat_times', []), 'downbeats': None,
            'limitations': result.get('limitations', [])}


def _backend_events(backend, beat_this, librosa, duration):
    """Return (beats, downbeats|None, bpm, grid_info) with validation."""
    if backend == 'librosa':
        return (_validate_events(librosa['beats'], duration, 'librosa beats'), None,
                _validate_bpm(librosa['bpm']), {})
    raw, grid = beat_this['raw'], beat_this['grid']
    if backend == 'beat_this_raw':
        return (_validate_events(raw['beats'], duration, 'raw beats'),
                _validate_events(raw['downbeats'], duration, 'raw downbeats'),
                _validate_bpm(beat_this['tempo'].get('bpm')),
                {'beat_strength_mean': float(np.mean(raw['beat_strength'])) if raw.get('beat_strength') else None,
                 'downbeat_strength_mean': float(np.mean(raw['downbeat_strength'])) if raw.get('downbeat_strength') else None,
                 'strength_meaning': 'Raw peak strengths are not correctness probabilities.'})
    return (_validate_events(grid.get('beats', []), duration, 'grid beats'),
            _validate_events(grid.get('downbeats', []), duration, 'grid downbeats'),
            _validate_bpm(grid.get('bpm')),
            {'grid_coverage': grid.get('coverage'), 'grid_state': grid.get('state'),
             'grid_bars': len(grid.get('bars', [])), 'grid_support_is_accuracy': False,
             'support_meaning': 'Coherent-grid coverage/support is not reference precision or accuracy.'})


def _score_row(row, record, case, backend, beats, downbeats, bpm, duration, baseline):
    expected = record.get('expected') or {}
    weaknesses = []
    constructed = expected.get('beats') is not None or expected.get('bpm') is not None
    row['reference_type'] = 'constructed' if constructed else 'transformation_consistency'
    if constructed:
        speed = case.get('speed', 1.) if case.get('type') in ('tempo', 'pitch') else 1.
        ref_beats = (_validate_events(map_events(expected['beats'], case, duration), duration, 'reference beats')
                     if expected.get('beats') is not None else None)
        ref_down = (_validate_events(map_events(expected['downbeats'], case, duration), duration, 'reference downbeats')
                    if expected.get('downbeats') is not None else None)
        exp_bpm = expected.get('bpm') * speed if expected.get('bpm') is not None else None
        row['tempo'] = tempo_metrics(exp_bpm, bpm)
        row['beat_scores'] = _scores(ref_beats, beats)
        row['downbeat_scores'] = (_scores(ref_down, downbeats) if downbeats is not None and ref_down is not None else None)
        for label, scores, ref, pred in (('beats', row['beat_scores'], ref_beats, beats),
                                         ('downbeats', row['downbeat_scores'], ref_down, downbeats)):
            if not scores:
                continue
            score = scores['30ms']
            if score['missed']:
                missed = [t for t in ref if all(abs(t - p) > 0.03 for p in (pred or []))]
                weaknesses.append(_weaknesses(record, case, backend, f'missed_{label}', missed,
                                              f"{score['missed']} reference {label} unmatched at 30 ms."))
            if score['extra']:
                extra = [t for t in (pred or []) if all(abs(t - r) > 0.03 for r in ref)]
                weaknesses.append(_weaknesses(record, case, backend, f'extra_{label}', extra,
                                              f"{score['extra']} predicted {label} unmatched at 30 ms."))
        if row['tempo']['status'] == 'completed' and not row['tempo']['within_2_percent']:
            weaknesses.append(_weaknesses(record, case, backend, 'tempo_error', [],
                                          f"Tempo relation {row['tempo']['tempo_relation']}, error {row['tempo']['error_percent']:.2f}%."))
    elif case['type'] == 'baseline':
        row['tempo'] = {'status': 'baseline', 'observed_bpm': bpm}
        row['beat_scores'] = row['downbeat_scores'] = None
        row['consistency_note'] = 'Baseline events are stored for transformation comparison; they are not ground truth.'
    elif baseline is None or baseline.get(backend) is None:
        row['tempo'] = {'status': 'uncertain', 'observed_bpm': bpm}
        row['beat_scores'] = row['downbeat_scores'] = None
        row['status'] = 'uncertain'
        row['uncertainty'] = 'Baseline backend events unavailable; consistency not scored.'
    else:
        base_beats, base_down = baseline[backend][:2]
        base_bpm = baseline[backend][2] if len(baseline[backend]) > 2 else None
        mapped = _validate_events(map_events(base_beats, case, duration), duration, 'mapped baseline beats')
        row['tempo'] = tempo_metrics(base_bpm * case.get('speed', 1.) if base_bpm is not None else None, bpm)
        row['tempo']['reference_type'] = 'mapped_baseline_estimate_consistency_only'
        row['beat_scores'] = _scores(mapped, beats)
        row['downbeat_scores'] = (_scores(_validate_events(map_events(base_down, case, duration), duration, 'mapped baseline downbeats'), downbeats)
                                  if downbeats is not None and base_down is not None else None)
        score = row['beat_scores']['30ms'] if row['beat_scores'] else None
        if score and (score['missed'] or score['extra']):
            weaknesses.append(_weaknesses(record, case, backend, 'transformation_inconsistency', beats or [],
                                          f"Versus mapped baseline at 30 ms: missed={score['missed']}, extra={score['extra']} (extra detections inside transformed audio are counted)."))
    if backend == 'beat_this_grid' and row.get('grid', {}).get('grid_coverage') is not None and row['grid']['grid_coverage'] < 0.5:
        weaknesses.append(_weaknesses(record, case, backend, 'low_grid_coverage', [],
                                      f"Grid coverage {row['grid']['grid_coverage']:.3f}; support is not accuracy."))
    row['weaknesses'] = weaknesses
    return weaknesses


def run(plan_path, output_dir, limit=None, progress=print):
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError('limit must be a positive integer.')
    plan = load_plan(plan_path)
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / REPORT_NAME
    report = resume_report(report_path, plan, 'timing', __file__)
    report['drift_correction_api'] = DRIFT_CORRECTION_API
    report['status'] = 'running'
    cache_dir = output_dir / 'cache' / 'beats'; scratch = output_dir / 'scratch'
    cache_dir.mkdir(parents=True, exist_ok=True)
    existing = {(r['record_id'], r['case_id'], r['backend']): r for r in report['rows']}
    records = plan['records'][:limit] if limit is not None else plan['records']

    def save(status='running'):
        report['status'] = status
        report['weaknesses'] = [w for r in report['rows'] for w in r.get('weaknesses', [])]
        counts = {s: sum(1 for r in report['rows'] if r['status'] == s)
                  for s in ('completed', 'uncertain', 'failed')}
        report['summary'] = {**counts, 'rows': len(report['rows']),
                             'denominator_rows': sum(len(applicable_cases(plan, r, 'timing'))
                                                      for r in records) * len(BACKENDS)}
        atomic_json(report_path, report)

    for record in records:
        cases = sorted(applicable_cases(plan, record, 'timing'),
                       key=lambda c: 0 if c['type'] == 'baseline' else 1)
        baseline = {}
        for old in report['rows']:
            if old['record_id'] == record['id'] and old.get('case_type') == 'baseline' and old['status'] in ('completed', 'uncertain'):
                baseline[old['backend']] = (old['events']['beats'], old['events']['downbeats'], old.get('observed_bpm'))
        for case in cases:
            if all((record['id'], case['id'], backend) in existing and existing[(record['id'], case['id'], backend)]['status'] in ('completed','uncertain') for backend in BACKENDS):
                progress(f'Reusing complete timing case {record["id"]}/{case["id"]}; no transform or inference')
                continue
            started_case = time.monotonic()
            beat_this = librosa = None; beat_this_error = librosa_error = None
            try:
                with case_audio(record, case, scratch) as audio_path:
                    info = sf.info(audio_path)
                    duration = float(info.duration)
                    if not math.isfinite(duration) or duration <= 0 or duration > 4 * 3600:
                        raise ValueError('Case audio duration out of bounds.')
                    audio_sha = file_hash(audio_path)
                    for backend in BACKENDS:
                        key = (record['id'], case['id'], backend)
                        if key in existing and existing[key]['status'] in ('completed', 'uncertain'):
                            progress(f"Reusing {record['id']}/{case['id']}/{backend}")
                            if case['type'] == 'baseline':
                                baseline[backend] = (existing[key]['events']['beats'], existing[key]['events']['downbeats'], existing[key].get('observed_bpm'))
                            continue
                        started = time.monotonic()
                        row = {'record_id': record['id'], 'record_name': record.get('name'),
                               'record_kind': record.get('kind'), 'case_id': case['id'],
                               'case_type': case['type'], 'backend': backend, 'status': 'failed',
                               'audio_sha256': audio_sha, 'audio_duration_seconds': duration,
                               'source_audio': record['audio'],
                               'frame_quantization_seconds': FRAME_QUANTIZATION[backend],
                               'events': {'beats': None, 'downbeats': None},
                               'downbeats_status': 'unavailable' if backend == 'librosa' else 'available',
                               'limitations': ['Librosa has no downbeat observations; no bar count is inferred from its first beat.'] if backend == 'librosa' else []}
                        try:
                            if backend == 'librosa':
                                if librosa is None and librosa_error is None:
                                    try: librosa = _run_librosa(audio_path, duration)
                                    except Exception as error: librosa_error = error
                                if librosa_error is not None: raise librosa_error
                            else:
                                if beat_this is None and beat_this_error is None:
                                    try: beat_this = _run_beat_this(audio_path, duration, cache_dir, progress)
                                    except Exception as error: beat_this_error = error
                                if beat_this_error is not None: raise beat_this_error
                            beats, downbeats, bpm, grid_info = _backend_events(backend, beat_this, librosa, duration)
                            row['events'] = {'beats': beats, 'downbeats': downbeats}
                            row['observed_bpm'] = bpm
                            row['grid'] = grid_info
                            if backend == 'librosa':
                                row['downbeats_status'] = 'unavailable'
                            row['source_gaps'] = _source_gaps(beats or [])
                            _score_row(row, record, case, backend, beats, downbeats, bpm, duration, baseline)
                            row['status'] = ('uncertain' if bpm is None or not beats or row.get('status') == 'uncertain'
                                              or (backend == 'beat_this_grid' and grid_info.get('grid_state') == 'uncertain')
                                              else 'completed')
                            if row['status'] == 'uncertain' and 'uncertainty' not in row:
                                row['uncertainty'] = 'Backend abstained or returned no usable beats/BPM.'
                            if case['type'] == 'baseline' and beats is not None:
                                baseline[backend] = (beats, downbeats, bpm)
                        except Exception as error:
                            if str(error) == 'Operation cancelled.': raise
                            row['status'] = 'failed'; row['error'] = str(error)[:1800]
                        row['wall_seconds'] = float(time.monotonic() - started)
                        if key in existing:
                            report['rows'][report['rows'].index(existing[key])] = row
                        else:
                            report['rows'].append(row)
                        existing[key] = row; save()
                        progress(f"{record['id']}/{case['id']}/{backend}: {row['status']}")
            except Exception as error:
                if str(error) == 'Operation cancelled.': raise
                for backend in BACKENDS:
                    key = (record['id'], case['id'], backend)
                    if key in existing and existing[key]['status'] in ('completed', 'uncertain'):
                        continue
                    row = {'record_id': record['id'], 'record_name': record.get('name'),
                           'record_kind': record.get('kind'), 'case_id': case['id'],
                           'case_type': case['type'], 'backend': backend, 'status': 'failed',
                           'error': str(error)[:1800], 'wall_seconds': float(time.monotonic() - started_case),
                           'audio_sha256': record.get('audio_sha256'), 'audio_duration_seconds': None,
                           'source_audio': record['audio'], 'frame_quantization_seconds': FRAME_QUANTIZATION[backend],
                           'events': {'beats': None, 'downbeats': None},
                           'downbeats_status': 'unavailable' if backend == 'librosa' else 'available',
                           'beat_scores': None, 'downbeat_scores': None, 'weaknesses': []}
                    if key in existing: report['rows'][report['rows'].index(existing[key])] = row
                    else: report['rows'].append(row)
                    existing[key] = row
                save()
    save('completed' if all(r['status'] != 'failed' for r in report['rows']) else 'partial')
    return report


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args(arguments)
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    try:
        report = run(args.plan, args.out, args.limit, lambda m: print(m, flush=True))
    except Exception as error:
        print(str(error), flush=True); return 1
    print(f"Saved {args.out / REPORT_NAME} rows={report['summary']['rows']} failed={report['summary']['failed']}")
    return 1 if report['summary']['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
