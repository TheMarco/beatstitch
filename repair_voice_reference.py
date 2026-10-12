#!/usr/bin/env python3
"""Document and remeasure a missing non-vocal annotation tail; never tune a model.

Preserves the original failed test and all successful rows. The only accepted
correction ends one final no-voice interval at the verified audio duration.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from benchmark_content import ARCHIVE_BYTES, ARCHIVE_MD5, detector_identity, file_hash, parse_labels, save_report
from validate_voice_threshold import (BASELINE, CANDIDATE, CRITERIA, approve_test, load_report,
                                       measure, reserved_catalog, summarize, validate_rows)


def corrected_tail(text, duration):
    labels = parse_labels(text)
    start, end, voiced = labels[-1]
    if voiced or not start < duration < end or end - duration <= .25:
        raise ValueError('Only a final no-voice interval extending beyond existing audio can be corrected.')
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    fields = lines[-1].split()
    lines[-1] = f'{fields[0]} {duration:.6f} nosing'
    result = '\n'.join(lines) + '\n'
    if parse_labels(result)[:-1] != labels[:-1]:
        raise ValueError('A correction must preserve every earlier annotation.')
    return result, {'kind': 'end_missing_nonvoice_tail_at_audio_end',
                    'original_end': end, 'corrected_end': round(duration, 6),
                    'unavailable_nonvoice_seconds': end - duration,
                    'not_changed': 'Earlier boundaries, voice labels, audio, detector and acceptance criteria.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--test', type=Path, required=True)
    parser.add_argument('--validation', type=Path, required=True)
    parser.add_argument('--development', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    plan_path = args.out.with_suffix('.plan.json')
    if args.out.exists() or plan_path.exists():
        parser.error('Correction requires new output paths; originals are preserved.')
    if args.archive.stat().st_size != ARCHIVE_BYTES or file_hash(args.archive, 'md5') != ARCHIVE_MD5:
        parser.error('Archive differs from the published release.')
    original, plan = load_report(args.test)
    validation, validation_plan = load_report(args.validation)
    detector = detector_identity()
    approve_test(validation, validation_plan, file_hash(args.development), detector)
    if (original['status'] != 'finished_with_failures' or plan['phase'] != 'test'
            or plan['detector'] != detector or plan['criteria'] != CRITERIA
            or plan['thresholds'] != {'baseline': BASELINE, 'candidate': CANDIDATE}
            or plan['validation_sha256'] != file_hash(args.validation)
            or plan['development_sha256'] != file_hash(args.development)):
        parser.error('Require the unchanged reserved experiment and its passed validation.')
    validate_rows(original, plan)
    failed = [i for i, r in enumerate(original['results']) if r['status'] == 'failed']
    if (len(original['results']) != 16 or len(failed) != 1
            or original['results'][failed[0]]['error'] != 'ValueError: Annotations extend beyond the recording.'):
        parser.error('This amendment handles exactly one annotation-end failure.')
    index = failed[0]; record = plan['records'][index]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.archive) as archive, tempfile.TemporaryDirectory(
            prefix='.voice-reference-', dir=args.out.parent) as temporary:
        if reserved_catalog(archive, 'test') != plan['records']:
            parser.error('Official source records differ from the original test.')
        work = Path(temporary); audio = work / record['name']
        with archive.open(record['audio']) as source, audio.open('wb') as target:
            shutil.copyfileobj(source, target)
        import djmix
        import soundfile as sf
        from studio_content import analyze_content
        duration = djmix.probe(audio)['duration']
        decoded = work / 'duration.wav'
        djmix.ffmpeg('-i', str(audio), '-ar', '32000', '-ac', '1', '-c:a', 'pcm_f32le', str(decoded))
        info = sf.info(decoded)
        decoded_duration = info.frames / info.samplerate
        if abs(decoded_duration - duration) > .1:
            parser.error('Decoded and probed durations disagree; do not amend the reference.')
        raw = archive.read(record['labels']).decode()
        revised, correction = corrected_tail(raw, duration)
        correction.update(source=record['name'], audio_sha256=file_hash(audio),
                          probed_duration=duration, decoded_duration=decoded_duration,
                          original_labels_sha256=record['labels_sha256'],
                          revised_labels_sha256=hashlib.sha256(revised.encode()).hexdigest())
        revised_plan = copy.deepcopy(plan)
        revised_plan['records'][index]['labels_sha256'] = correction['revised_labels_sha256']
        revised_plan.update(original_test_sha256=file_hash(args.test),
                            original_test_plan_sha256=original['plan_sha256'], reference_correction=correction,
                            amendment_runner_sha256=file_hash(__file__),
                            amendment='Data-quality correction after final-test inspection; no model or threshold selection.')
        # Freeze the amendment before the missing recording is inferred again.
        with plan_path.open('x') as handle:
            json.dump(revised_plan, handle, indent=2, allow_nan=False)
        report = copy.deepcopy(original)
        report.update(status='running', plan_sha256=file_hash(plan_path), reference_correction=correction)
        report['limitations'] = [*report['limitations'],
            'One final no-voice annotation tail was corrected after test inspection; the original failure is preserved.']
        save_report(args.out, report)
        analysis = analyze_content(audio, duration, work / 'cache')
        analysis.pop('source', None)
        labels = parse_labels(revised)
        report['results'][index] = {'record': revised_plan['records'][index], 'status': 'measured',
                                   'analysis': analysis, 'audio_sha256': correction['audio_sha256'],
                                   'annotation_text': revised, 'original_annotation_text': raw,
                                   'baseline': measure(analysis, labels, BASELINE),
                                   'candidate': measure(analysis, labels, CANDIDATE)}
        validate_rows(report, revised_plan)
        report.update(status='finished', summary=summarize(report['results'], 16))
        save_report(args.out, report)
        print(json.dumps({'status': report['status'], 'correction': correction, **report['summary']}))


if __name__ == '__main__':
    main()
