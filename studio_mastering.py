"""Production mastering helpers ported from the accepted prototype.

Conservative offline finishing only: measured 20 Hz cleanup, gentle
1.2:1 compression when crest is high, capped gain, and an oversampled
compensated limiter with final true-peak validation. No broad EQ,
stereo widening, or AI processing.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np
from scipy import signal
import soundfile as sf

import djmix
from studio_cache import source_signature
import studio_process

VERSION = 1
TARGET_LUFS = -14.0
CEILING_DB = -1.2
MAX_GAIN_DB = 6.0
MAX_PEAK_REDUCTION_DB = 2.0

_GENERATED = ("current.wav", "mastered.wav", "matched.wav", "report.json")


def _check_cancelled():
    studio_process.check_cancelled()


def _finite(value, what="value"):
    out = float(value)
    if not math.isfinite(out):
        raise djmix.MixError(f"{what} is not finite.")
    return out


def validate_audio(path, expected_frames=None):
    info = sf.info(path)
    if info.samplerate != djmix.SR or info.channels != 2 or info.frames < djmix.SR:
        raise djmix.MixError('Mastering needs stereo 48 kHz audio of at least one second.')
    if expected_frames is not None and info.frames != expected_frames:
        raise djmix.MixError('Mastering changed the sample count; the A/B comparison would be misaligned.')
    for block in sf.blocks(path, blocksize=65536, dtype='float32', always_2d=True):
        _check_cancelled()
        if not np.isfinite(block).all():
            raise djmix.MixError('The mastering audio contains invalid samples.')
    return info


def analyse_dynamics(path):
    window = round(.05 * djmix.SR)
    rms_values, peak_values = [], []
    sub_filter = signal.butter(2, 20, fs=djmix.SR, output='sos')
    zi = np.zeros((len(sub_filter), 2, 2))
    power = sub_power = 0.0
    for block in sf.blocks(path, blocksize=window, dtype='float64', always_2d=True):
        _check_cancelled()
        rms_values.append(math.sqrt(float(np.mean(block ** 2))))
        peak_values.append(float(np.max(np.abs(block))))
        low, zi = signal.sosfilt(sub_filter, block, axis=0, zi=zi)
        power += float(np.sum(block ** 2))
        sub_power += float(np.sum(low ** 2))
    rms = np.asarray(rms_values)
    peak = np.asarray(peak_values)
    active = rms > max(1e-5, float(rms.max()) / 100)
    if not active.any() or power <= 1e-8:
        raise djmix.MixError('This mix is too quiet to evaluate reliably.')
    crest = 20 * np.log10(np.maximum(peak[active], 1e-12) / rms[active])
    return {'rms_p95_db': _finite(20 * np.log10(np.percentile(rms[active], 95)), 'rms_p95_db'),
            'crest_median_db': _finite(np.median(crest), 'crest_median_db'),
            'sub20_power_fraction': _finite(sub_power / power, 'sub20_power_fraction')}


def core_filters(analysis):
    filters, summary = [], []
    if analysis['sub20_power_fraction'] >= .01:
        filters.append('highpass=f=20:p=2')
        summary.append('20 Hz cleanup of measured very-low-frequency energy')
    if analysis['crest_median_db'] >= 9:
        threshold = np.clip(10 ** ((analysis['rms_p95_db'] - 2) / 20), .01, .5)
        filters.append(f'acompressor=threshold={threshold:.9f}:ratio=1.2:attack=30:'
                       'release=180:knee=2:makeup=1:link=maximum:detection=rms')
        summary.append('Gentle 1.2:1 compression, with a slow attack to preserve drum transients')
    else:
        summary.append('Extra compression skipped: the measured passages already have modest crest')
    return filters or ['anull'], summary


def limiter_gain(levels):
    return float(min(MAX_GAIN_DB, TARGET_LUFS - levels['lufs'],
                     CEILING_DB - levels['true_peak_db'] + MAX_PEAK_REDUCTION_DB))


def render_filter(source, destination, filters, frames):
    _check_cancelled()
    djmix.ffmpeg('-i', str(source), '-map', '0:a:0', '-af',
                 ','.join([*filters, f'apad=whole_len={frames}', f'atrim=end_sample={frames}']),
                 '-ar', str(djmix.SR), '-ac', '2', '-c:a', 'pcm_s24le', str(destination))
    _check_cancelled()
    validate_audio(destination, frames)


def _same_file(a: Path, b: Path) -> bool:
    try:
        if a.resolve() == b.resolve():
            return True
    except OSError:
        pass
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def _validate_segments(segments, duration):
    if segments is None:
        return []
    if not isinstance(segments, (list, tuple)):
        raise djmix.MixError('Segments must be a list.')
    out = []
    for item in segments:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'].strip():
            raise djmix.MixError('Each segment needs a name.')
        try:
            start, end = float(item['start']), float(item['end'])
        except (KeyError, TypeError, ValueError) as exc:
            raise djmix.MixError('Each segment needs finite start and end times.') from exc
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration + 1e-6):
            raise djmix.MixError('Each segment must satisfy 0 <= start < end <= duration.')
        out.append({'name': item['name'], 'start': start, 'end': end})
    return out


def master_file(source: Path, destination: Path, progress=lambda msg: None) -> dict:
    source, destination = Path(source), Path(destination)
    if _same_file(source, destination):
        raise djmix.MixError('Choose a mastering destination different from the source.')
    info = validate_audio(source)
    signature = source_signature(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _same_file(source, destination):
        raise djmix.MixError('Choose a mastering destination different from the source.')
    with tempfile.TemporaryDirectory(prefix='.mastering-', dir=destination.parent) as temporary:
        stage = Path(temporary)
        _check_cancelled(); progress('Measuring the current mix and its dynamics')
        input_levels = djmix.meter(source)
        _finite(input_levels['lufs'], 'input_lufs'); _finite(input_levels['true_peak_db'], 'input_true_peak_db')
        analysis = analyse_dynamics(source)
        filters, summary = core_filters(analysis)
        _check_cancelled(); progress('Preparing the gentle finishing pass')
        core = stage / 'core.wav'
        render_filter(source, core, filters, info.frames)
        core_levels = djmix.meter(core)
        gain = _finite(limiter_gain(core_levels), 'gain_db')
        summary.append(f'{gain:+.1f} dB level adjustment with a conservative peak-reduction allowance')
        summary.append('Lookahead peak limiting at 4× sample rate, checked after returning to 48 kHz')
        mastered = stage / 'mastered.wav'
        peak_limit = 10 ** (CEILING_DB / 20)
        _check_cancelled(); progress('Limiting peaks and checking the mastered audio')
        render_filter(core, mastered,
                      ['aresample=192000', f'volume={gain:.9f}dB',
                       f'alimiter=limit={peak_limit:.9f}:attack=5:release=80:level=false:latency=true',
                       'aresample=48000'], info.frames)
        mastered_levels = djmix.meter(mastered)
        safety = _finite(min(0.0, -1.1 - mastered_levels['true_peak_db']), 'safety_attenuation_db')
        if safety < 0:
            protected = stage / 'protected.wav'
            render_filter(mastered, protected, [f'volume={safety:.9f}dB'], info.frames)
            os.replace(protected, mastered)
            mastered_levels = djmix.meter(mastered)
        _finite(mastered_levels['lufs'], 'lufs'); _finite(mastered_levels['true_peak_db'], 'true_peak_db')
        if mastered_levels['true_peak_db'] > -1.0:
            raise djmix.MixError('The mastered audio failed its true-peak headroom check.')
        validate_audio(mastered, info.frames)
        _check_cancelled()
        if source_signature(source) != signature:
            raise djmix.MixError('The input changed while mastering. Run mastering again.')
        # Publish only after every check passes; a failure above leaves any
        # existing destination untouched.
        os.replace(mastered, destination)
    result_mastering = {
        'input_lufs': float(input_levels['lufs']),
        'input_true_peak_db': float(input_levels['true_peak_db']),
        'gain_db': float(gain),
        'safety_attenuation_db': float(safety),
        'analysis': {k: float(v) for k, v in analysis.items()},
        'processing_summary': list(summary),
        'target_lufs': float(TARGET_LUFS),
        'target_reached': bool(abs(mastered_levels['lufs'] - TARGET_LUFS) <= .5),
        'ceiling_db': float(CEILING_DB),
        'max_gain_db': float(MAX_GAIN_DB),
        'max_peak_reduction_db': float(MAX_PEAK_REDUCTION_DB),
    }
    result = {'path': str(destination.resolve()), 'duration': float(info.duration),
              'frames': int(info.frames), 'lufs': float(mastered_levels['lufs']),
              'true_peak_db': float(mastered_levels['true_peak_db']),
              'mastering': result_mastering}
    json.dumps(result, allow_nan=False)
    progress('Mastering ready')
    return result


def prepare_comparison(current: Path, mastered: Path, folder: Path, *, name='Your mix',
                       segments=None, processing_summary=None, progress=lambda msg: None) -> dict:
    current, mastered, folder = Path(current), Path(mastered), Path(folder)
    folder_resolved = folder.resolve()
    for input_path in (current, mastered):
        resolved = input_path.resolve()
        if resolved.parent == folder_resolved and resolved.name in _GENERATED:
            raise djmix.MixError('Choose inputs different from the generated comparison files.')
    if _same_file(current, mastered):
        raise djmix.MixError('Choose different current and mastered inputs.')
    current_info = validate_audio(current)
    mastered_info = validate_audio(mastered)
    if current_info.frames != mastered_info.frames:
        raise djmix.MixError('Current and mastered audio must have identical frame counts.')
    frames, duration = int(current_info.frames), float(current_info.duration)
    clean_segments = _validate_segments(segments, duration)
    if processing_summary is not None and (not isinstance(processing_summary, list) or
            not all(isinstance(item, str) for item in processing_summary)):
        raise djmix.MixError('Processing summary must be a list of explanations.')
    recipe = {'version': VERSION,
              'current': source_signature(current),
              'mastered': source_signature(mastered),
              'ffmpeg': source_signature(djmix.tool_path('ffmpeg')) if djmix.tool_path('ffmpeg') else None}
    manifest = folder / 'report.json'
    try:
        saved = json.loads(manifest.read_text())
        if saved.get('recipe') == recipe:
            for variant in ('current', 'mastered', 'matched'):
                validate_audio(folder / f'{variant}.wav', frames)
            # Names and navigation can change without changing audio.
            saved.update(name=name, segments=clean_segments)
            if processing_summary is not None:
                saved['processing_summary'] = processing_summary
            metadata = folder / '.report-updated.json'
            try:
                metadata.write_text(json.dumps(saved, indent=2, allow_nan=False) + '\n')
                os.replace(metadata, manifest)
            finally:
                metadata.unlink(missing_ok=True)
            progress('Reusing the finished mastering comparison')
            return saved
    except (OSError, ValueError, RuntimeError, KeyError):
        pass
    folder.mkdir(parents=True, exist_ok=True)
    # Re-check after mkdir in case inputs are inside a newly created folder.
    for input_path in (current, mastered):
        resolved = input_path.resolve()
        if resolved.parent == folder_resolved and resolved.name in _GENERATED:
            raise djmix.MixError('Choose inputs different from the generated comparison files.')
    current_signature, mastered_signature = recipe['current'], recipe['mastered']
    with tempfile.TemporaryDirectory(prefix='.comparison-', dir=folder.parent) as temporary:
        stage = Path(temporary)
        _check_cancelled(); progress('Measuring the current mix and the master')
        current_input_levels = djmix.meter(current)
        mastered_levels = djmix.meter(mastered)
        for levels in (current_input_levels, mastered_levels):
            _finite(levels['lufs'], 'lufs'); _finite(levels['true_peak_db'], 'true_peak_db')
        match_target = min(current_input_levels['lufs'], mastered_levels['lufs'])
        matched_gain = _finite(match_target - mastered_levels['lufs'], 'matched_gain_db')
        current_gain = _finite(match_target - current_input_levels['lufs'], 'current_gain_db')
        # By construction both gains are <= 0: attenuate only, never boost.
        assert matched_gain <= 1e-12 and current_gain <= 1e-12
        _check_cancelled(); progress('Matching A/B listening loudness')
        # Mastered is copied verbatim: never process finished audio again.
        shutil.copyfile(mastered, stage / 'mastered.wav')
        validate_audio(stage / 'mastered.wav', frames)
        if matched_gain == 0:
            shutil.copyfile(mastered, stage / 'matched.wav')
        else:
            render_filter(mastered, stage / 'matched.wav', [f'volume={matched_gain:.9f}dB'], frames)
        if current_gain == 0:
            shutil.copyfile(current, stage / 'current.wav')
        else:
            render_filter(current, stage / 'current.wav', [f'volume={current_gain:.9f}dB'], frames)
        current_levels, matched_levels = djmix.meter(stage / 'current.wav'), djmix.meter(stage / 'matched.wav')
        _finite(current_levels['lufs'], 'current lufs'); _finite(matched_levels['lufs'], 'matched lufs')
        if abs(current_levels['lufs'] - matched_levels['lufs']) > .15:
            raise djmix.MixError('The A/B versions did not match listening loudness closely enough.')
        if source_signature(current) != current_signature or source_signature(mastered) != mastered_signature:
            raise djmix.MixError('An input changed while preparing the comparison. Prepare it again.')
        summary = processing_summary if processing_summary is not None else ['Mastered audio copied without further processing',
                   f'A/B comparison matched to {match_target:.1f} LUFS by attenuation only']
        report = {'recipe': recipe, 'folder': str(folder_resolved), 'name': name,
                  'duration': duration, 'frames': frames, 'segments': clean_segments,
                  'current': {'lufs': float(current_levels['lufs']), 'true_peak_db': float(current_levels['true_peak_db'])},
                  'mastered': {'lufs': float(mastered_levels['lufs']), 'true_peak_db': float(mastered_levels['true_peak_db'])},
                  'matched': {'lufs': float(matched_levels['lufs']), 'true_peak_db': float(matched_levels['true_peak_db'])},
                  'matched_gain_db': float(matched_gain), 'current_gain_db': float(current_gain),
                  'processing_summary': summary}
        json.dumps(report, allow_nan=False)
        (stage / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        _check_cancelled(); progress('Publishing the mastering comparison')
        # The app's cache work lock protects publication. Retain the previous
        # owned files for rollback if a filesystem replacement fails midway.
        backup = stage / 'previous'; backup.mkdir()
        for filename in _GENERATED:
            previous = folder / filename
            if previous.exists():
                try:
                    os.link(previous, backup / filename)
                except OSError:
                    shutil.copyfile(previous, backup / filename)
        published = []
        try:
            for filename in _GENERATED:  # Manifest is last.
                os.replace(stage / filename, folder / filename)
                published.append(filename)
        except OSError:
            for filename in published:
                previous = backup / filename
                if previous.exists(): os.replace(previous, folder / filename)
                else: (folder / filename).unlink(missing_ok=True)
            raise
    progress('Mastering comparison ready')
    return report
