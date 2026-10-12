"""One source time warp combining drift correction and a shared set tempo."""
from pathlib import Path

import numpy as np
import soundfile as sf

import djmix
from studio_cache import source_signature
from studio_process import check_cancelled
from studio_storage import require_space
from studio_tempo_curve import keyframes
from studio_timing import corrected_time, mapping

VERSION = 1
CONTEXT_SECONDS = 2


def reference_times(track, clip, curve, reference_start, source_seconds):
    points = mapping(track, clip)
    corrected = np.asarray(source_seconds) if points is None else np.interp(source_seconds, points[:, 0], points[:, 1])
    return reference_start + (corrected - corrected_time(track, clip, clip.cue_in)) * track.bpm / curve.reference_bpm


def cache_token(track, clip, curve, reference_start):
    """Scope source reuse to the complete DSP input, including cue context."""
    start, end = max(0., clip.cue_in - CONTEXT_SECONDS), min(track.duration, clip.cue_out + CONTEXT_SECONDS)
    a, b = reference_times(track, clip, curve, reference_start, np.array([start, end]))
    # Include one sample beyond either source boundary for decoder rounding.
    margin = track.bpm / curve.reference_bpm / djmix.SR
    return ['set-tempo-source', VERSION, reference_start, clip.cue_out,
            curve.segment_token(a - margin, b + margin)]


def prepare(track, clip, curve, reference_start, work, label, gain):
    """Render a complete clip at its exact place on the shared tempo curve.

    Pitch is handled by the established independent R3 pass. The R2 time map
    then composes source drift and set tempo into a single timing operation.
    Returned audio is stereo float32 raw at 48 kHz, aligned to output frames.
    """
    check_cancelled()
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    before = source_signature(track.path)
    points = mapping(track, clip)
    if clip.stabilize and track.timing.get('source') != before:
        raise djmix.MixError('The accepted timing correction belongs to an older recording. Analyze timing again.')
    start, end = max(0., clip.cue_in - CONTEXT_SECONDS), min(track.duration, clip.cue_out + CONTEXT_SECONDS)
    reference_end = reference_start + round((corrected_time(track, clip, clip.cue_out) - corrected_time(track, clip, clip.cue_in))
                                          * track.bpm / curve.reference_bpm * djmix.SR) / djmix.SR
    first_out, last_out = curve.output_span(round(reference_start * djmix.SR), round(reference_end * djmix.SR))
    frames = last_out - first_out
    if frames <= 0: raise djmix.MixError('The tempo curve leaves no audible source frames.')
    require_space(work, round(80 * max(frames + 8 * djmix.SR, (end - start) * djmix.SR)), 'apply the set tempo curve')
    decoded, stretched = work / f'{label}-decode.wav', work / f'{label}-stretch.wav'
    djmix.ffmpeg('-i', track.path, '-ss', f'{start:.12f}', '-t', f'{end - start:.12f}', '-map', '0:a:0',
                 '-ar', str(djmix.SR), '-ac', '2', '-af', 'volume=-12dB', '-c:a', 'pcm_f32le', str(decoded))
    if clip.shift:
        pitched = work / f'{label}-pitch.wav'
        djmix.transform(decoded, pitched, track.bpm, track.bpm, clip.shift, True)
        decoded = pitched
    n = sf.info(decoded).frames
    if n < 2: raise djmix.MixError('The decoded song is too short to stretch.')
    actual_end = start + n / djmix.SR
    def to_absolute(source):
        return curve.to_output(reference_times(track, clip, curve, reference_start, source)) * djmix.SR
    origin = float(to_absolute(np.array([start]))[0])
    def transform(source_frames): return to_absolute(start + source_frames / djmix.SR) - origin
    # Include every source correction corner and every tempo anchor, so the
    # adaptive approximation cannot smooth across an intended curve boundary.
    corners = [start, actual_end, clip.cue_in, clip.cue_out]
    if points is not None: corners.extend(points[:, 0])
    for beat in curve.beats:
        corrected = (beat * 60 / curve.reference_bpm - reference_start) * curve.reference_bpm / track.bpm + corrected_time(track, clip, clip.cue_in)
        corners.append(corrected if points is None else float(np.interp(corrected, points[:, 1], points[:, 0])))
    knots = sorted({0, n - 1, *(round((t - start) * djmix.SR) for t in corners if start < t < actual_end)})
    knots = [k for k in knots if 0 <= k < n]
    pairs = keyframes(transform, knots)
    # Rubber Band's endpoint key is the last frame, not the one-past boundary.
    output_frames = round(float(transform(np.array([n]))[0]))
    pairs[-1, 1] = output_frames - 1
    if np.any(np.diff(pairs, axis=0) <= 0): raise djmix.MixError('The requested tempo map is not strictly increasing.')
    time_map = work / f'{label}-time-map.txt'
    time_map.write_text(''.join(f'{a} {b}\n' for a, b in pairs[1:]), encoding='utf-8')
    djmix.run(['rubberband', '--fast', '--crisp', '4', '--centre-focus', '--quiet',
               '--time', str(output_frames / n), '--timemap', str(time_map), str(decoded), str(stretched)])
    first = round(first_out - origin)
    raw = work / f'{label}.raw'
    djmix.ffmpeg('-i', str(stretched), '-af', f'atrim=start_sample={first}:end_sample={first + frames},volume={gain + 12:.12f}dB',
                 '-f', 'f32le', '-ar', str(djmix.SR), '-ac', '2', str(raw))
    found = raw.stat().st_size // 8
    if abs(found - frames) > 2:
        raise djmix.MixError(f'Tempo-curve render length mismatch: expected {frames}, got {found}.')
    with raw.open('ab' if found < frames else 'r+b') as handle:
        if found < frames: handle.write(b'\0' * (8 * (frames - found)))
        else: handle.truncate(frames * 8)
    check_cancelled()
    if source_signature(track.path) != before: raise djmix.MixError('The source changed while applying the tempo curve.')
    return raw
