"""Restrained outgoing effects, confined to the overlap and cached after stretching."""
import os
import uuid

import numpy as np
from scipy.signal import butter, sosfilt

import djmix
from studio_cache import digest
from studio_storage import require_space

VERSION = 1
EFFECTS = {
    'none': ('No extra effect', 'Use the normal crossfade and your bass-handling choice.'),
    'filter': ('Filter out', 'Gradually thin the outgoing song with a 600 Hz high-pass filter. '
               'Can make room for the incoming song; may sound too thin. Only affects this overlap.'),
    'echo': ('Echo out', 'Fade the outgoing song early and repeat a short filtered ending four times, '
             'each quieter. Repeats stay inside this overlap. Listen for distracting repeated notes or words.'),
}


def token(clip):
    return ['outgoing-effect', VERSION, clip.transition_effect] if clip.transition_effect != 'none' else None


def active_overlaps(session, positions, start, end):
    return [(i, pos['start'], positions[i - 1]['end']) for i, pos in enumerate(positions)
            if i and session.clips[i].transition_effect != 'none'
            and pos['start'] < end and positions[i - 1]['end'] > start]


def echo_timing(frames, tempo):
    """Exact dry cutoff and repeated source window, in overlap-relative frames."""
    tail = min(round(240 / tempo * djmix.SR), frames // 2)
    delay = max(1, tail // 4); cut = frames - 4 * delay
    return delay, cut, cut - delay


def write_effect(path, low, high, offset, frames, kind, bass_swap, tempo, progress,
                 *, curve=None, reference_start=0, reference_frames=None, clip=None):
    """Start each filter at the same overlap boundary, including for cropped previews.

    Echo keeps only one beat (half a beat for a one-bar overlap) in memory.
    The remaining work is streamed; tails never extend the protected overlap.
    """
    from studio_engine import _weights
    sos = butter(2, 600, 'highpass', fs=djmix.SR, output='sos')
    state = np.zeros((len(sos), 2, 2))
    varying = curve is not None and not curve.identity
    reference_frames = reference_frames if varying else frames
    origin = int(curve.output_frame(reference_start)) if varying else 0
    def musical(n):
        return curve.to_reference((origin + n) / djmix.SR) * djmix.SR - reference_start if varying else n
    def elapsed(n):
        return curve.output_frame(reference_start + np.asarray(n)) - origin if varying else n
    delay, cut, send_start = echo_timing(reference_frames, tempo)
    send_a, send_b = int(elapsed(send_start)), int(elapsed(cut))
    send = np.zeros((send_b - send_a, 2), dtype=np.float32) if kind == 'echo' else None
    with path.open('wb') as output:
        for left in range(0, frames, djmix.BLOCK):
            if left % (16 * djmix.BLOCK) == 0: progress('Preparing ' + EFFECTS[kind][0].lower())
            right = min(frames, left + djmix.BLOCK); n = np.arange(left, right)
            beats = musical(n)
            lo, hi = _weights(beats, 0, reference_frames, False, bass_swap, tempo, clip)
            selection = slice(offset + left, offset + right)
            dry = low[selection] * lo[:, None] + high[selection] * hi[:, None]
            filtered, state = sosfilt(sos, dry, axis=0, zi=state)
            if kind == 'filter':
                wet = np.sin(np.clip(beats / max(1, reference_frames - 1), 0, 1) * np.pi / 2) ** 2
                result = dry * (1 - wet[:, None]) + filtered * wet[:, None]
                # The causal filter has a short residual; bring it to zero within the blend.
                result *= np.clip((frames - 1 - n) / (.005 * djmix.SR), 0, 1)[:, None]
            else:
                gain = np.cos(np.clip((beats - send_start) / max(1, delay - 1), 0, 1) * np.pi / 2) ** 2
                result = dry * gain[:, None]
                a, b = max(left, send_a), min(right, send_b)
                if a < b: send[a - send_a:b - send_a] = filtered[a - left:b - left]
            if not np.isfinite(result).all(): raise djmix.MixError('Invalid audio while preparing the transition effect.')
            output.write(np.asarray(result, dtype='<f4').tobytes())
    if kind == 'echo':
        n = np.arange(len(send))
        taper = np.minimum(np.clip(n / (.005 * djmix.SR), 0, 1),
                           np.clip((len(send) - 1 - n) / (.005 * djmix.SR), 0, 1))
        send *= taper[:, None]
        mapped = np.memmap(path, dtype='<f4', mode='r+', shape=(frames, 2))
        try:
            for tap, gain in enumerate((.32, .16, .08, .04)):
                progress('Adding quiet echo repeats')
                a, b = int(elapsed(cut + tap * delay)), min(frames, int(elapsed(cut + (tap + 1) * delay)))
                if varying:
                    for at in range(a, b, djmix.BLOCK):
                        stop = min(b, at + djmix.BLOCK)
                        source = musical(np.arange(at, stop)) - cut - tap * delay + send_start
                        coordinates = curve.to_output((reference_start + source) / djmix.SR) * djmix.SR - origin - send_a
                        repeat = np.column_stack([np.interp(coordinates, n, send[:, ch]) for ch in (0, 1)])
                        mapped[at:stop] += repeat * gain
                else:
                    mapped[a:b] += send[:b - a] * gain
            mapped.flush()
        finally: mapped._mmap.close()


def plan(session, positions, prepared, families, overlaps, work, cache, progress):
    from studio_tempo_curve import session_curve
    from studio_engine import layout
    curve = session_curve(session)
    references = layout(session) if not curve.identity else positions
    entries = {item[0]: item for item in prepared}
    effects = {}; reused = processed = 0
    try:
        for index, start, end in overlaps:
            outgoing = index - 1; clip = session.clips[index]
            _, left, right, low, high, offset = entries[outgoing]
            if not left <= start < end <= right:
                raise djmix.MixError('The complete overlap is needed to prepare its outgoing effect.')
            frames = end - start
            key = [VERSION, families[outgoing], start - positions[outgoing]['start'], frames,
                   clip.transition_effect, clip.bass_swap, session.target_bpm]
            from studio_fades import token as fade_token
            if fade_token(clip, False): key.append(fade_token(clip, False))
            if not curve.identity:
                a = references[index]['start'] / djmix.SR
                b = (references[index]['start'] + references[index]['incoming']) / djmix.SR
                key.append(curve.segment_token(a, b))
            path = (cache.root / 'effects' / (digest(key) + '.raw') if cache else
                    work / f'effect-{index}.raw')
            if cache and path.is_file() and path.stat().st_size == frames * 8:
                progress('Reusing ' + EFFECTS[clip.transition_effect][0].lower())
                os.utime(path, None); reused += 1
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                require_space(path.parent, frames * 8 + 4096, 'prepare this transition effect')
                pending = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
                try:
                    write_effect(pending, low, high, start - left + offset, frames,
                                 clip.transition_effect, clip.bass_swap, session.target_bpm, progress,
                                 curve=curve, reference_start=references[index]['start'],
                                 reference_frames=references[index]['incoming'], clip=clip)
                    os.replace(pending, path)
                finally: pending.unlink(missing_ok=True)
                processed += 1
            effects[outgoing] = {'start': start, 'end': end, 'audio': djmix.raw_audio(path)}
        return effects, {'effects_reused': reused, 'effects_processed': processed}
    except BaseException:
        for effect in effects.values(): effect['audio']._mmap.close()
        raise
