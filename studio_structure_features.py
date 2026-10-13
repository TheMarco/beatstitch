"""Bounded audio reads for Beat This, preserving its 50 Hz frame centers."""
from contextlib import closing, contextmanager
import math
from pathlib import Path
import tempfile

import numpy as np

import djmix
from studio_process import check_cancelled
from studio_storage import require_space

RATE = 22050
HOP = 441
MEL_BINS = 128
BLOCK_FRAMES = 1500
MAX_SECONDS = 4 * 60 * 60


@contextmanager
def decoded_audio(path, duration, cache_dir, progress):
    """Keep one disposable mono file instead of decoded audio and tensor copies."""
    check_cancelled()
    if not math.isfinite(duration) or duration <= 0 or duration > MAX_SECONDS:
        raise djmix.MixError('Musical analysis supports songs up to four hours. Choose a shorter source or set its beat grid and cues manually.')
    root = Path(cache_dir); root.mkdir(parents=True, exist_ok=True)
    # Decode plus the uncompressed observation cache and small metadata slack.
    frames = math.ceil(duration * RATE / HOP) + 1
    require_space(root, math.ceil(duration * RATE) * 4 + frames * (MEL_BINS * 4 + 64) + 65536,
                  'find musical cues')
    with tempfile.TemporaryDirectory(prefix='.pending-structure-', dir=root) as directory:
        raw_file = Path(directory) / 'audio.raw'
        chunk_samples = RATE * 5
        count, peak, reported = 0, 0., -1
        from studio_analysis_audio import mono_stream
        stream = mono_stream(path, RATE, cache_dir=root, chunk_samples=chunk_samples,
                             progress=progress)
        with raw_file.open('wb') as output, closing(stream):
            for block in stream:
                check_cancelled()
                if len(block) % 4:
                    raise djmix.MixError('Incomplete audio samples during musical analysis.')
                audio = np.frombuffer(block, dtype='<f4')
                if not np.isfinite(audio).all():
                    raise djmix.MixError('Invalid audio samples during musical analysis.')
                count += len(audio)
                if count > MAX_SECONDS * RATE:
                    raise djmix.MixError('Musical analysis supports songs up to four hours. Choose a shorter source or set its beat grid and cues manually.')
                if len(audio): peak = max(peak, float(np.max(abs(audio))))
                if count // (RATE * 30) != reported:
                    reported = count // (RATE * 30)
                    progress(f'Reading musical cues · {min(count / RATE, duration):.0f}s / {duration:.0f}s')
                    require_space(root, len(block), 'read musical cues')
                output.write(block)
        if count < RATE * 8 or peak < 1e-5:
            raise djmix.MixError('Musical analysis needs at least eight seconds of audible audio.')
        check_cancelled()
        yield raw_file, count


def extract(raw_file, count, transform, progress):
    """Call the unchanged model frontend on aligned, overlapping audio blocks.

    Its FFT is centered on each multiple of 441 samples with a 512-sample
    radius. Two context frames on either side keep artificial chunk edges
    outside every retained FFT window. At the true source ends the model's
    original reflection padding is retained, including its last partial frame.
    """
    frames = count // HOP + 1
    logmel = np.empty((frames, MEL_BINS), dtype=np.float32)
    rms = np.empty(frames)
    with Path(raw_file).open('rb') as reader:
        for start in range(0, frames, BLOCK_FRAMES):
            check_cancelled()
            end = min(frames, start + BLOCK_FRAMES)
            left_frame = max(0, start - 2)
            left, right = left_frame * HOP, min(count, (end + 1) * HOP)
            reader.seek(left * 4)
            audio = np.frombuffer(reader.read((right - left) * 4), dtype='<f4')
            if len(audio) != right - left:
                raise djmix.MixError('Incomplete temporary audio during musical analysis.')
            progress(f'Measuring the sound · {min(count, start * HOP) / RATE:.0f}s / {count / RATE:.0f}s')
            local = start - left_frame
            values = transform(audio).cpu().numpy()[local:local + end - start]
            if values.shape != (end - start, MEL_BINS) or not np.isfinite(values).all():
                raise djmix.MixError('Invalid sound measurements during musical analysis.')
            logmel[start:end] = values
            centers = np.arange(start, end) * HOP - left
            a, b = np.clip(centers - 220, 0, len(audio)), np.clip(centers + 221, 0, len(audio))
            cumulative = np.r_[0., np.cumsum(audio.astype(float) ** 2)]
            rms[start:end] = np.sqrt((cumulative[b] - cumulative[a]) / np.maximum(1, b - a))
    check_cancelled()
    return logmel, rms
