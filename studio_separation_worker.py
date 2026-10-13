"""Isolated offline separation of a precisely positioned source passage."""
import json
import os
from pathlib import Path
import sys


def publish(path, value):
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')
    pending.replace(path)


def infer(work):
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import resample_poly
    from studio_spleeter_model import separate_waveform
    from studio_separation_model import load_model, MODEL_FILE, STEMS, RATE
    request = json.loads((work / 'request.json').read_text())
    start, end = request['start'], request['end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end or end - start > 240 * RATE:
        raise ValueError('Separation supports blend passages up to four minutes.')
    with sf.SoundFile(work / 'decoded.wav') as file:
        if file.samplerate != RATE or file.channels != 2 or end > file.frames:
            raise ValueError('The source does not cover this blend.')
        left, right = max(0, start - 8 * RATE), min(file.frames, end + 8 * RATE)
        file.seek(left); original = file.read(right - left, dtype='float32', always_2d=True)
    if not np.isfinite(original).all(): raise ValueError('The passage contains invalid audio.')
    audio = resample_poly(original, 147, 160, axis=0)
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    publish(work / 'progress.json', 'Loading the local vocal/drum separator')
    model = load_model(Path(request['models']) / MODEL_FILE)
    estimates = separate_waveform(audio, model, lambda value: publish(work / 'progress.json', value))
    if set(estimates) != set(STEMS) or any(a.shape != (len(audio), 2) or not np.isfinite(a).all()
                                         for a in estimates.values()):
        raise ValueError('The separator returned invalid or misaligned audio.')
    for name in STEMS:
        converted = resample_poly(estimates[name], 160, 147, axis=0)
        excerpt = converted[start - left:end - left]
        if excerpt.shape != (end - start, 2): raise ValueError('The separated passage is incomplete.')
        sf.write(work / (name + '.wav'), excerpt, RATE, subtype='FLOAT')
    return {'frames': end - start, 'rate': RATE, 'channels': 2}


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 1: return 2
    work = Path(arguments[0])
    try: result = infer(work)
    except Exception as error:
        publish(work / 'result.json', {'error': f'{type(error).__name__}: {error}'[:1200]}); return 1
    publish(work / 'result.json', result); return 0


if __name__ == '__main__': raise SystemExit(main())
