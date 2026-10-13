#!/usr/bin/env python3
"""Development-only content experiment runner (family ``content``).

Runs the unchanged production ``analyze_content`` on controlled plan audio
through an isolated cache, records raw window scores (never probabilities),
compares the current .08 voice prompt against the former .2 prompt, and runs
unchanged ``tonal_evidence`` on short passages. Transformation rows measure
consistency against the preserved baseline audio, never truth. Known voice
labels are scored per window only when the label scope is identical
(``entire_window_any_singing`` over the whole ten-second window); unknown or
gap labels stay excluded and are never treated as negatives. An ambiguous
local key never proves a drums-only passage, and no fitted map is installed.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import soundfile as sf

from detector_experiment_common import (RATE, applicable_cases, case_audio,
                                        load_plan, map_events, resume_report)
from detector_pilot import atomic_json, file_hash
from studio_content import analyze_content, state, voice_regions
from studio_overlap_harmony import summarize_side, tonal_evidence

FAMILY = 'content'
REPORT_NAME = 'content-report.json'
PROMPT_NEW, PROMPT_OLD = .08, .2
# Transforms that keep the exact same ten-second window and its label scope.
SCOPE_PRESERVING = {'baseline', 'gain', 'swap', 'polarity'}


def case_id(case):
    return case.get('id') or case['type']


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def _window_rows(analysis):
    rows = []
    for window in analysis.get('windows', []):
        scores = window['scores']
        rows.append({'start': window['start'], 'end': window['end'],
                     'scores': dict(scores), 'rms_db': window['rms_db'],
                     'state': window['state'],
                     'prompt_08': bool(window['rms_db'] >= -65 and scores['voice'] >= PROMPT_NEW),
                     'prompt_20': bool(window['rms_db'] >= -65 and scores['voice'] >= PROMPT_OLD),
                     'nonfinite_scores': not all(_finite(v) for v in scores.values())})
    return rows


def _voice_evaluation(record, case, windows):
    """Score only identical-scope, fully labeled ten-second windows."""
    expected = record.get('expected') or {}
    if not isinstance(expected.get('voice'), bool):
        return {'status': 'excluded', 'reason': 'Voice label unknown; never treated as negative.'}
    constructed_negative = expected['voice'] is False and expected.get('voice_scope') == 'construction_has_no_voice_samples'
    if expected.get('voice_scope') != 'entire_window_any_singing' and not constructed_negative:
        return {'status': 'excluded', 'reason': 'Voice label scope is not entire_window_any_singing.'}
    if case['type'] not in SCOPE_PRESERVING and not constructed_negative:
        return {'status': 'consistency_only',
                'reason': 'Transformed timing needs exact label remapping; consistency only.'}
    label = expected['voice']
    cells = []
    for window in windows:
        if not constructed_negative and (abs((window['end'] - window['start']) - 10.) > .01 or abs(window['start']) > .001 or abs(window['end']-10.) > .01):
            cells.append({'window': [window['start'], window['end']], 'status': 'excluded',
                          'reason': 'Window is not the labeled ten-second window.'})
            continue
        cells.append({'window': [window['start'], window['end']], 'status': 'scored',
                      'label_voice_any_singing': label,
                      'prompt_08': window['prompt_08'], 'prompt_20': window['prompt_20'],
                      'prompt_08_correct': window['prompt_08'] == label,
                      'prompt_20_correct': window['prompt_20'] == label})
    return {'status': 'scored' if any(c['status'] == 'scored' for c in cells) else 'excluded',
            'label_voice_any_singing': label, 'windows': cells,
            'note': 'Raw strengths are not probabilities; no vocal-boundary or vocal-clash accuracy is claimed.'}


def _tonal(record, audio_path):
    """Unchanged tonal_evidence on equal parts of at most eight seconds."""
    info = sf.info(audio_path)
    duration = info.frames / info.samplerate
    # Fixed first ten seconds for long examples; the selection is not based on
    # any model score. This includes the constructed pitched examples.
    with sf.SoundFile(audio_path) as stream:
        rate = stream.samplerate
        audio = stream.read(min(stream.frames,10*rate),dtype='float32',always_2d=True)
    duration = len(audio)/rate
    if not np.isfinite(audio).all():
        return {'status': 'failed', 'reason': 'Nonfinite audio; tonal evidence not run.'}
    import librosa
    mono_stereo = librosa.resample(audio.T,orig_sr=rate,target_sr=22050).T if rate != 22050 else audio
    count = max(1, math.ceil(duration / 8.))
    edges = np.rint(np.linspace(0, len(mono_stereo), count + 1)).astype(int)
    parts = []
    for left, right in zip(edges, edges[1:]):
        part = tonal_evidence(mono_stereo[left:right])
        parts.append({**part, 'start': left / 22050, 'end': right / 22050,
                      'concentration': 'unavailable (production tonal_evidence does not return it)'})
    expected = record.get('expected') or {}
    pitched = expected.get('pitched') if isinstance(expected.get('pitched'), bool) else None
    key_label = expected.get('key')
    constructed = record['kind'] in ('synthetic_tonal', 'synthetic_timing')
    summary = summarize_side(parts)
    return {'status': 'measured', 'summary': summary, 'parts': parts,
            'concentration': 'unavailable (production tonal_evidence does not return it)',
            'harmonic_fraction': [p.get('harmonic_fraction', 'unavailable') for p in parts],
            'candidates': [p.get('candidates', []) for p in parts],
            'missed_constructed_tonal_evidence': bool(pitched and not summary['tonal']),
            'drums_only_proven': False,
            'note': 'Ambiguous or absent local key never proves drums-only; no production threshold changed.',
            'key_correctness': ({'label': key_label, 'estimated': summary['keys'],
                                 'offered_estimate': summary['state']=='estimated',
                                 'correct': summary['state']=='estimated' and bool(set(summary['keys']) & {key_label})}
                                if constructed and isinstance(key_label, str) else
                                'not_applicable (key correctness only for constructed composition labels)')}


def _row(plan, record, case, output_dir, scratch):
    row = {'record': record['id'], 'record_kind': record['kind'], 'case': case_id(case),
           'case_type': case['type'],
           'provenance': {'audio_sha256': record['audio_sha256'], 'frames': record['frames'],
                          'duration': record['frames'] / RATE, 'provenance': record.get('provenance')}}
    try:
        with case_audio(record, case, scratch) as audio_path:
            info = sf.info(audio_path)
            duration = info.frames / info.samplerate
            analysis = analyze_content(audio_path, duration, Path(output_dir) / 'cache' / 'content',
                                       progress=lambda _: None)
            windows = _window_rows(analysis)
            row.update({'status': 'measured', 'duration': duration,
                        'case_audio_sha256': file_hash(audio_path),
                        'windows': windows,
                        'states': sorted({w['state'] for w in windows}),
                        'silent_windows': sum(1 for w in windows if w['state'] == 'quiet'),
                        'nonfinite_windows': sum(1 for w in windows if w['nonfinite_scores']),
                        'regions_08': voice_regions(analysis, threshold=PROMPT_NEW),
                        'regions_20': voice_regions(analysis, threshold=PROMPT_OLD),
                        'voice_evaluation': _voice_evaluation(record, case, windows),
                        'tonal': _tonal(record, audio_path)})
    except Exception as error:  # every failure stays a counted row
        row.update({'status': 'failed', 'error': f'{type(error).__name__}: {error}'})
    return row


def _summarize(report, plan):
    rows = report['rows']
    measured = [r for r in rows if r['status'] == 'measured']
    baselines = {(r['record'], r['case_type']): r for r in measured}
    crossings = 0
    for row in measured:
        base = next((r for r in measured if r['record'] == row['record'] and r['case_type'] == 'baseline'), None)
        if base is None or row is base:
            continue
        same_window_scope = row['case_type'] in ('gain','swap','polarity','pitch')
        for name in ('prompt_08', 'prompt_20'):
            if same_window_scope:
                crossings += sum(1 for a, b in zip(row['windows'], base['windows']) if a[name] != b[name])
        row['baseline_comparison'] = {
            'status': 'consistency_only', 'identical_window_scope': same_window_scope,
            'max_abs_score_delta': max((max(abs(a['scores'][k] - b['scores'][k]) for k in a['scores'])
                                        for a, b in zip(row['windows'], base['windows'])), default=None),
            'mapped_baseline_regions_08': [map_events([r['start'], r['end']], {'type': row['case_type'],
                                           **next(c for c in plan['cases'] if case_id(c) == row['case'])}, row['duration'])
                                           for r in base['regions_08']],
            'note': 'Disagreement with preserved audio content; not truth.'}
    report['summary'] = {
        'rows': len(rows), 'measured': len(measured), 'failed': len(rows) - len(measured),
        'abstentions_excluded': sum(1 for r in measured if r['voice_evaluation']['status'] != 'scored'),
        'threshold_crossings_vs_baseline': crossings,
        'missed_constructed_tonal_evidence': sum(1 for r in measured
                                             if isinstance(r.get('tonal'), dict)
                                             and r['tonal'].get('missed_constructed_tonal_evidence')),
        'note': 'Raw scores are not probabilities. Constructed instrumental references cannot prove real-music accuracy.'}
    report['status'] = 'complete' if len(rows) >= sum(
        len(applicable_cases(plan, r, FAMILY)) for r in plan['records']) else 'partial'


def run(plan_path, output_dir, limit=None, progress=print):
    if limit is not None and limit < 1:
        raise ValueError('limit must be positive')
    plan = load_plan(plan_path)
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / REPORT_NAME
    report = resume_report(report_path, plan, FAMILY, __file__)
    allowed={(r['id'],case_id(c)) for r in plan['records'] for c in applicable_cases(plan,r,FAMILY)}
    done = {(r['record'], r['case']) for r in report['rows']}
    if len(done)!=len(report['rows']) or not done.issubset(allowed):
        raise ValueError('Saved content rows do not match the frozen plan.')
    scratch = output_dir / 'scratch'
    new = 0
    for record in plan['records']:
        for case in applicable_cases(plan, record, FAMILY):
            if (record['id'], case_id(case)) in done or (limit is not None and new >= limit):
                continue
            row = _row(plan, record, case, output_dir, scratch)
            report['rows'].append(row); new += 1
            _summarize(report, plan)
            atomic_json(report_path, report)
            progress(f"content {row['record']}/{row['case']}: {row['status']}")
    _summarize(report, plan)
    atomic_json(report_path, report)
    return report


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args(arguments)
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    report = run(args.plan, args.out, args.limit, progress=lambda _: None)
    summary = report['summary']
    print(f"content-report: {summary['measured']} measured, {summary['failed']} failed, "
          f"{summary['abstentions_excluded']} excluded/abstained -> {args.out / REPORT_NAME}")
    return 1 if summary['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
