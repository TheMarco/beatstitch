"""Bounded mono analysis reads that preserve audible, cancelling stereo.

Playback and exported sound are never modified. Ordinary sources retain the
existing FFmpeg mono conversion exactly. When the stereo average loses most
of a channel's energy, analysis uses one globally selected channel instead.
The choice is fixed for the passage, avoiding artificial switching transients.
"""
from contextlib import closing
import math
from pathlib import Path
import tempfile

import numpy as np

import djmix
from studio_process import check_cancelled
from studio_storage import require_space

VERSION = 1
MAX_SECONDS = 4 * 3600


def mono_stream(path, rate, *, cache_dir=None, chunk_samples=None,
                start=None, duration=None, progress=lambda _: None, details=None):
    """Yield complete little-endian float32 samples with bounded buffers."""
    path = Path(path)
    if not isinstance(rate, int) or rate <= 0:
        raise djmix.MixError('Invalid analysis sample rate.')
    info = djmix.probe(path)
    length = info['duration'] if duration is None else duration
    if (not math.isfinite(length) or not 0 < length <= MAX_SECONDS
            or (start is not None and (not math.isfinite(start) or start < 0))):
        raise djmix.MixError('Audio analysis supports finite passages up to four hours.')
    chunk_samples = rate * 5 if chunk_samples is None else chunk_samples
    if not isinstance(chunk_samples, int) or chunk_samples <= 0:
        raise djmix.MixError('Invalid analysis buffer size.')
    arguments = ['-i', str(path)]
    if start is not None: arguments += ['-ss', str(start)]
    if duration is not None: arguments += ['-t', str(duration)]
    arguments += ['-map', '0:a:0', '-ar', str(rate)]
    check_cancelled()
    fallback = False
    if details is not None: details['analysis_channel'] = 'mono_mix'
    # Mono sources cannot cancel against a second channel.
    if info['channels'] != 1:
        root = Path(cache_dir) if cache_dir is not None else Path(tempfile.gettempdir())
        root.mkdir(parents=True, exist_ok=True)
        require_space(root, math.ceil(length * rate) * 8 + chunk_samples * 32 + 65536,
                      'check stereo audio for analysis')
        with tempfile.TemporaryDirectory(prefix='.pending-analysis-mono-', dir=root) as folder:
            stereo_path = Path(folder) / 'stereo.raw'
            energy = np.zeros(2, dtype='float64'); mixed_energy = 0.; count = 0
            stream = djmix.stream_ffmpeg(*arguments, '-ac', '2', '-f', 'f32le', '-',
                                        chunk_bytes=chunk_samples * 8)
            with stereo_path.open('wb') as output, closing(stream):
                for raw in stream:
                    check_cancelled()
                    if len(raw) % 8:
                        raise djmix.MixError('Incomplete stereo samples during audio analysis.')
                    audio = np.frombuffer(raw, dtype='<f4').reshape(-1, 2)
                    if not np.isfinite(audio).all():
                        raise djmix.MixError('Invalid stereo samples during audio analysis.')
                    count += len(audio)
                    if count > MAX_SECONDS * rate:
                        raise djmix.MixError('Audio analysis supports passages up to four hours.')
                    values = audio.astype('float64')
                    energy += np.sum(values * values, axis=0)
                    mixed = values.mean(axis=1)
                    mixed_energy += float(np.sum(mixed * mixed))
                    output.write(raw)
                    progress(f'Checking stereo channels · {min(count / rate, length):.0f}s / {length:.0f}s')
            channel = int(np.argmax(energy))
            fallback = mixed_energy < .25 * float(energy[channel])
            if fallback:
                if details is not None:
                    details['analysis_channel'] = 'stronger_left' if channel == 0 else 'stronger_right'
                    details['stereo_cancellation_ratio'] = mixed_energy / float(energy[channel])
                progress('Stereo channels cancel when combined · analyzing the stronger channel')
                with stereo_path.open('rb') as reader:
                    while raw := reader.read(chunk_samples * 8):
                        check_cancelled()
                        audio = np.frombuffer(raw, dtype='<f4').reshape(-1, 2)
                        yield audio[:, channel].astype('<f4', copy=True).tobytes()
    if not fallback:
        stream = djmix.stream_ffmpeg(*arguments, '-ac', '1', '-f', 'f32le', '-',
                                    chunk_bytes=chunk_samples * 4)
        with closing(stream):
            for raw in stream:
                check_cancelled()
                if len(raw) % 4 or not np.isfinite(np.frombuffer(raw, dtype='<f4')).all():
                    raise djmix.MixError('Invalid or incomplete mono samples during audio analysis.')
                yield raw


def mono_samples(path, rate, *, start=None, duration=None, details=None):
    """Read bounded model excerpts; long analyses must use mono_stream."""
    if duration is None or not math.isfinite(duration) or not 0 < duration <= 60:
        raise djmix.MixError('In-memory analysis passages must be at most sixty seconds.')
    raw = b''.join(mono_stream(path, rate, start=start, duration=duration, details=details))
    return np.frombuffer(raw, dtype='<f4').copy()
