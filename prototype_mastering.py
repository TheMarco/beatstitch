#!/usr/bin/env python3
"""Conservative offline mastering experiment, separate from project settings.

Uses FFmpeg's established filters. No neural model, reference imitation, or
claim that waveform density predicts quality. See ffmpeg-filters.html#alimiter.
"""
from __future__ import annotations

import argparse
import hashlib
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

VERSION = 1
TARGET_LUFS = -14.0
CEILING_DB = -1.2
MAX_GAIN_DB = 6.0
MAX_PEAK_REDUCTION_DB = 2.0


def validate_audio(path, expected_frames=None):
    info = sf.info(path)
    if info.samplerate != djmix.SR or info.channels != 2 or info.frames < djmix.SR:
        raise djmix.MixError('The mastering experiment needs stereo 48 kHz audio of at least one second.')
    if expected_frames is not None and info.frames != expected_frames:
        raise djmix.MixError('Mastering changed the sample count; the A/B comparison would be misaligned.')
    for block in sf.blocks(path, blocksize=65536, dtype='float32', always_2d=True):
        if not np.isfinite(block).all():
            raise djmix.MixError('The mastering audio contains invalid samples.')
    return info


def analyse_dynamics(path):
    """Bounded memory, independent channel power, measured decisions only."""
    window = round(.05 * djmix.SR)
    rms_values, peak_values = [], []
    sub_filter = signal.butter(2, 20, fs=djmix.SR, output='sos')
    zi = np.zeros((len(sub_filter), 2, 2))
    power = sub_power = 0.0
    for block in sf.blocks(path, blocksize=window, dtype='float64', always_2d=True):
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
    return {'rms_p95_db': float(20 * np.log10(np.percentile(rms[active], 95))),
            'crest_median_db': float(np.median(crest)),
            'sub20_power_fraction': float(sub_power / power)}


def core_filters(analysis):
    filters, summary = [], []
    # The low-pass measurement has transition-band leakage; this intentionally
    # conservative threshold avoids automatically thinning normal EDM bass.
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
    """Approach a listening target without demanding large peak reductions."""
    return float(min(MAX_GAIN_DB, TARGET_LUFS - levels['lufs'],
                     CEILING_DB - levels['true_peak_db'] + MAX_PEAK_REDUCTION_DB))


def render_filter(source, destination, filters, frames):
    djmix.ffmpeg('-i', str(source), '-map', '0:a:0', '-af',
                 ','.join([*filters, f'apad=whole_len={frames}', f'atrim=end_sample={frames}']),
                 '-ar', str(djmix.SR), '-ac', '2', '-c:a', 'pcm_s24le', str(destination))
    validate_audio(destination, frames)


def finish_mix(source, folder, *, name='Your current mix', segments=None, progress=print):
    source, folder = Path(source).resolve(), Path(folder).resolve()
    info = validate_audio(source)
    if source.parent == folder and source.name in ('mastered.wav', 'matched.wav'):
        raise djmix.MixError('Choose an input different from the generated mastering files.')
    key = {'version': VERSION, 'source': source_signature(source),
           'ffmpeg': source_signature(djmix.tool_path('ffmpeg')),
           'target': TARGET_LUFS, 'ceiling': CEILING_DB, 'max_gain': MAX_GAIN_DB,
           'max_peak_reduction': MAX_PEAK_REDUCTION_DB}
    manifest = folder / 'report.json'
    try:
        saved = json.loads(manifest.read_text())
        if saved['recipe'] == key:
            for variant in ('current', 'mastered', 'matched'):
                validate_audio(folder / f'{variant}.wav', info.frames)
            progress('Reusing the finished mastering comparison')
            return saved
    except (OSError, ValueError, RuntimeError, KeyError):
        pass
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.mastering-', dir=folder) as temporary:
        stage = Path(temporary)
        progress('Measuring the current mix and its dynamics')
        original_levels = djmix.meter(source)
        analysis = analyse_dynamics(source)
        filters, summary = core_filters(analysis)
        progress('Preparing the gentle finishing pass')
        core = stage / 'core.wav'
        render_filter(source, core, filters, info.frames)
        core_levels = djmix.meter(core)
        gain = limiter_gain(core_levels)
        summary.append(f'{gain:+.1f} dB level adjustment with a conservative peak-reduction allowance')
        summary.append('Lookahead peak limiting at 4× sample rate, checked after returning to 48 kHz')
        mastered = stage / 'mastered.wav'
        peak_limit = 10 ** (CEILING_DB / 20)
        render_filter(core, mastered,
                      ['aresample=192000', f'volume={gain:.9f}dB',
                       f'alimiter=limit={peak_limit:.9f}:attack=5:release=80:level=false:latency=true',
                       'aresample=48000'], info.frames)
        mastered_levels = djmix.meter(mastered)
        safety = min(0.0, -1.1 - mastered_levels['true_peak_db'])
        if safety < 0:
            protected = stage / 'protected.wav'
            render_filter(mastered, protected, [f'volume={safety:.9f}dB'], info.frames)
            os.replace(protected, mastered)
            mastered_levels = djmix.meter(mastered)
        if mastered_levels['true_peak_db'] > -1.0:
            raise djmix.MixError('The mastered audio failed its true-peak headroom check.')
        # Match to whichever version is quieter. Never boost a comparison into
        # clipping merely to force the two loudness readings to agree.
        match_target = min(original_levels['lufs'], mastered_levels['lufs'])
        matched_gain = match_target - mastered_levels['lufs']
        current_gain = match_target - original_levels['lufs']
        progress('Matching A/B listening loudness and checking the rendered audio')
        matched = stage / 'matched.wav'
        render_filter(mastered, matched, [f'volume={matched_gain:.9f}dB'], info.frames)
        current = stage / 'current.wav'
        if current_gain == 0:
            shutil.copyfile(source, current)
        else:
            render_filter(source, current, [f'volume={current_gain:.9f}dB'], info.frames)
        current_levels, matched_levels = djmix.meter(current), djmix.meter(matched)
        if abs(current_levels['lufs'] - matched_levels['lufs']) > .15:
            raise djmix.MixError('The A/B versions did not match listening loudness closely enough.')
        if source_signature(source) != key['source']:
            raise djmix.MixError('The input changed while mastering. Prepare the comparison again.')
        report = {'recipe': key, 'name': name, 'duration': info.duration,
                  'segments': segments or [], 'current': current_levels,
                  'mastered': mastered_levels, 'matched': matched_levels,
                  'original': original_levels, 'matched_gain_db': matched_gain,
                  'current_gain_db': current_gain, 'analysis': analysis,
                  'processing_summary': summary, 'gain_db': gain,
                  'safety_attenuation_db': safety, 'target_lufs': TARGET_LUFS,
                  'target_reached': abs(mastered_levels['lufs'] - TARGET_LUFS) <= .5,
                  'note': 'The loudness target yields to preserving peak dynamics. No automatic broad EQ or stereo widening.'}
        (stage / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        for filename in ('current.wav', 'mastered.wav', 'matched.wav', 'report.json'):
            os.replace(stage / filename, folder / filename)
    progress('Mastering comparison ready')
    return report


def prepare_project(project, folder, data, tool_dir=None, progress=print):
    from studio_engine import Session, layout
    from studio_render_cache import audio_recipe, prepare_listening
    project, folder, data = Path(project), Path(folder), Path(data)
    if tool_dir:
        previous = djmix.tool_path
        tool_dir = Path(tool_dir).resolve()
        def selected_tool(name):
            candidate = tool_dir / (name + ('.exe' if os.name == 'nt' else ''))
            return str(candidate) if candidate.is_file() else previous(name)
        djmix.tool_path = selected_tool
    snapshot = project.read_bytes()
    session = Session.from_dict(json.loads(snapshot))
    session.mastering_enabled = False  # Compare before/after exactly one finishing pass.
    recipe = audio_recipe(session)
    progress('Preparing the saved mix; unchanged song processing is reused')
    # Keep the new assembled mix in the prototype, while reusing the app's
    # per-song cache and its established locking/pinning rules.
    folder.mkdir(parents=True, exist_ok=True)
    prepared = prepare_listening(session, folder / 'render', audio_cache=data / 'audio-cache', progress=progress)
    positions = layout(session)
    from studio_tempo_curve import session_curve
    curve = session_curve(session)
    duration = prepared['duration']
    segments = []
    if len(positions) > 1:
        start = curve.output_frame(positions[1]['start']) / djmix.SR
        end = curve.output_frame(positions[0]['end']) / djmix.SR
        segments.append({'name': 'The blend', 'start': max(0, start - 8), 'end': min(duration, end + 8)})
    for index, pos in enumerate(positions):
        left = curve.output_frame(pos['start'] + pos['incoming']) / djmix.SR
        right = curve.output_frame(positions[index + 1]['start'] if index + 1 < len(positions) else pos['end']) / djmix.SR
        if right - left >= 20:
            center = (left + right) / 2
            track = session.tracks[session.clips[index].track_id]
            segments.append({'name': track.name, 'start': max(left, center - 20), 'end': min(right, center + 20)})
    result = finish_mix(prepared['path'], folder, name='Your current two-song mix' if len(positions) == 2 else session.name,
                        segments=segments, progress=progress)
    result['project_sha256'] = hashlib.sha256(snapshot).hexdigest()
    result['mix_fingerprint'] = recipe['fingerprint']
    result['project_changed_during_preparation'] = project.read_bytes() != snapshot
    result['reused_tracks'] = prepared.get('reused_tracks')
    result['processed_tracks'] = prepared.get('processed_tracks')
    (folder / 'report.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    (folder / 'project-snapshot.djmix').write_bytes(snapshot)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--project', type=Path)
    inputs.add_argument('--audio', type=Path)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--tool-dir', type=Path)
    args = parser.parse_args()
    progress = lambda message: print(message, flush=True)
    if args.project:
        if not args.data_dir:
            parser.error('--project requires --data-dir for the existing song cache')
        result = prepare_project(args.project, args.folder, args.data_dir, args.tool_dir, progress)
    else:
        result = finish_mix(args.audio, args.folder, progress=progress)
    print(json.dumps({k: result[k] for k in ('current', 'mastered', 'matched', 'gain_db', 'analysis', 'processing_summary')}, indent=2))


if __name__ == '__main__':
    main()
