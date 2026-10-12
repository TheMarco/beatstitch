"""Run the verified official key CNN without importing it into the Qt process."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
              'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_name] = '1'


def infer(source):
    import numpy as np
    import djmix
    from studio_key_model import verify_model
    model_path = verify_model()
    duration = min(60., djmix.probe(source)['duration'])
    decoded = djmix.ffmpeg('-i', str(source), '-t', str(duration), '-map', '0:a:0',
                           '-ar', '44100', '-ac', '1', '-f', 'f32le', '-').stdout
    audio = np.frombuffer(decoded, dtype='<f4').copy()
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError('Decoded audio is empty or non-finite.')
    details = {'excerpt_seconds': len(audio) / 44100, 'frames': len(audio)}
    if len(audio) < 2 * 44100 or np.max(np.abs(audio)) < 1e-5:
        return {**details, 'state': 'uncertain', 'candidates': [],
                'reason': 'Too little audible music to estimate a key. Choose a key yourself, or leave it Unknown.'}
    import librosa
    import torch
    from vendor.openkeyscan.model import KeyNet
    torch.set_num_threads(1)
    # This worker runs one inference per process, keeping Torch away from Qt.
    torch.set_num_interop_threads(1)
    model = KeyNet(num_classes=24, in_channels=1, Nf=64, p=0.3)
    model.load_state_dict(torch.load(model_path, map_location='cpu', weights_only=True))
    model.eval()
    cqt = librosa.cqt(audio, sr=44100, hop_length=8820,
                      n_bins=105, bins_per_octave=24, fmin=65)
    # Match the pinned official server: it discards the final two time frames.
    spec = np.log1p(np.abs(cqt))[:, 0:-2]
    with torch.inference_mode():
        prediction = torch.softmax(model(torch.tensor(spec, dtype=torch.float32)[None, None]), dim=1).numpy()
    if (prediction.shape != (1, 24) or not np.isfinite(prediction).all()
            or np.min(prediction) < 0 or not np.isclose(prediction.sum(), 1., atol=1e-5)):
        raise ValueError('Invalid key model output.')
    # Official Camelot class order: 1A..12A followed by 1B..12B.
    labels = [djmix.key_name(djmix.parse_key(label)) for label in (
        'G# minor', 'D# minor', 'A# minor', 'F minor', 'C minor', 'G minor',
        'D minor', 'A minor', 'E minor', 'B minor', 'F# minor', 'C# minor',
        'B major', 'F# major', 'C# major', 'G# major', 'D# major', 'A# major',
        'F major', 'C major', 'G major', 'D major', 'A major', 'E major')]
    scores = prediction[0]
    candidates = [{'key': labels[i], 'score': float(scores[i])} for i in np.argsort(-scores)]
    return {**details, 'state': 'estimated', 'candidates': candidates,
            'reason': 'Estimated from the first minute (or the entire shorter song). Listen to confirm; the key can change later.'}


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 2:
        return 2
    source, output = map(Path, arguments)
    code = 0
    try:
        result = infer(source)
    except Exception as error:
        result = {'error': f'{type(error).__name__}: {error}'[:1200]}
        code = 1
    pending = output.with_suffix('.pending')
    pending.write_text(json.dumps(result, allow_nan=False), encoding='utf-8')
    pending.replace(output)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
