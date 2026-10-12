"""Disk-backed zero-phase kick filtering with bounded decoded audio buffers.

The forward and backward IIR state and odd endpoint padding match sosfiltfilt.
Only the compact 1 kHz rhythm envelope is retained in memory for grid fitting.
No filtered sound is exported or used as playback audio.
"""
from contextlib import closing
import math
from pathlib import Path
import tempfile

import numpy as np
from scipy.ndimage import gaussian_filter1d, uniform_filter1d
from scipy.signal import butter, sosfilt, sosfilt_zi

import djmix
from studio_process import check_cancelled
from studio_storage import managed_cache, require_space

RATE = 12000
HOP = 12
BLOCK = RATE * 5
MAX_SECONDS = 4 * 60 * 60


def _forward(path, output, sos, edge, progress, duration):
    """Append the odd-padded forward pass, retaining state at every boundary."""
    state = None
    head = np.empty(0, dtype='<f4')
    tail = head
    count, reported = 0, -1
    stream = djmix.stream_ffmpeg('-i', str(path), '-map', '0:a:0', '-ar', str(RATE),
                                '-ac', '1', '-f', 'f32le', '-', chunk_bytes=BLOCK * 4)
    with output.open('wb') as handle, closing(stream):
        for raw in stream:
            check_cancelled()
            if len(raw) % 4:
                raise djmix.MixError('Incomplete samples while reading song timing.')
            audio = np.frombuffer(raw, dtype='<f4')
            if not np.isfinite(audio).all():
                raise djmix.MixError('Invalid samples while reading song timing.')
            count += len(audio)
            if count > MAX_SECONDS * RATE:
                raise djmix.MixError('Timing analysis supports songs up to four hours. Use a shorter source or keep fixed-tempo processing.')
            if count // (RATE * 30) != reported:
                reported = count // (RATE * 30)
                progress(f'Reading song timing · {min(count / RATE, duration):.0f}s / {duration:.0f}s')
                require_space(output.parent, BLOCK * 16, 'analyze song timing')
            if state is None:
                head = np.concatenate((head, audio))
                if len(head) <= edge:
                    continue
                padding = 2 * head[0] - head[1:edge + 1][::-1]
                filtered, state = sosfilt(sos, padding, zi=sosfilt_zi(sos) * padding[0])
                handle.write(filtered.astype('<f8', copy=False).tobytes())
                audio = head
            filtered, state = sosfilt(sos, audio, zi=state)
            handle.write(filtered.astype('<f8', copy=False).tobytes())
            tail = np.concatenate((tail, audio))[-edge - 1:].copy()
        if state is not None:
            padding = 2 * tail[-1] - tail[-edge - 2:-1][::-1]
            filtered, state = sosfilt(sos, padding, zi=state)
            handle.write(filtered.astype('<f8', copy=False).tobytes())
    return count


def _backward(source, output, sos, edge, count, progress):
    """Read forward results in reverse; write unpadded samples at their offsets."""
    end = count + 2 * edge
    with source.open('rb') as reader, output.open('w+b') as writer:
        reader.seek((end - 1) * 8)
        state = sosfilt_zi(sos) * np.frombuffer(reader.read(8), dtype='<f8')[0]
        reported = -1
        while end:
            check_cancelled()
            start = max(0, end - BLOCK)
            reader.seek(start * 8)
            values = np.frombuffer(reader.read((end - start) * 8), dtype='<f8')
            if len(values) != end - start:
                raise djmix.MixError('Incomplete temporary audio during timing analysis.')
            filtered, state = sosfilt(sos, values[::-1], zi=state)
            left, right = max(start, edge), min(end, count + edge)
            if right > left:
                writer.seek((left - edge) * 8)
                writer.write(filtered[::-1][left - start:right - start].astype('<f8', copy=False).tobytes())
            elapsed = (count + 2 * edge - start) // (RATE * 30)
            if elapsed != reported:
                reported = elapsed
                progress(f'Measuring rhythm · {min(count, count + 2 * edge - start) / RATE:.0f}s / {count / RATE:.0f}s')
                require_space(output.parent, BLOCK * 8, 'analyze song timing')
            end = start


def _envelope(path, count, progress):
    # Thirty neighboring samples preserve the centered 60-sample RMS window,
    # including SciPy's reflection at the actual start and end of the source.
    energy = np.empty((count + HOP - 1) // HOP)
    block = max(HOP, BLOCK // HOP * HOP)
    with path.open('rb') as reader:
        for start in range(0, count, block):
            check_cancelled()
            end = min(count, start + block)
            left, right = max(0, start - 30), min(count, end + 30)
            reader.seek(left * 8)
            audio = np.frombuffer(reader.read((right - left) * 8), dtype='<f8')
            if len(audio) != right - left:
                raise djmix.MixError('Incomplete rhythm filter data.')
            rms = np.sqrt(np.maximum(0, uniform_filter1d(audio * audio, 60)))
            selected = rms[start - left:end - left:HOP]
            energy[start // HOP:start // HOP + len(selected)] = selected
            if start % (RATE * 30) == 0:
                progress(f'Finding beat attacks · {start / RATE:.0f}s / {count / RATE:.0f}s')
    check_cancelled()
    rise = np.maximum(0, energy - np.r_[np.zeros(10), energy[:-10]])
    return gaussian_filter1d(rise, 2)


@managed_cache
def extract(path, cache_dir=None, progress=lambda message: None):
    check_cancelled()
    duration = djmix.probe(Path(path))['duration']
    if duration > MAX_SECONDS:
        raise djmix.MixError('Timing analysis supports songs up to four hours. Use a shorter source or keep fixed-tempo processing.')
    root = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir())
    root.mkdir(parents=True, exist_ok=True)
    sos = butter(3, [35, 180], btype='bandpass', fs=RATE, output='sos')
    edge = 3 * (2 * len(sos) + 1 - min((sos[:, 2] == 0).sum(), (sos[:, 5] == 0).sum()))
    # Two double-precision passes plus endpoint padding and metadata slack.
    require_space(root, math.ceil(duration * RATE) * 16 + 2 * edge * 8 + 65536, 'analyze song timing')
    with tempfile.TemporaryDirectory(prefix='.pending-timing-', dir=root) as directory:
        forward, filtered = Path(directory) / 'forward.raw', Path(directory) / 'filtered.raw'
        count = _forward(path, forward, sos, edge, progress, duration)
        if count < RATE:
            return np.zeros(0)
        _backward(forward, filtered, sos, edge, count, progress)
        forward.unlink()
        return _envelope(filtered, count, progress)
