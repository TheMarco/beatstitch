"""Private Demucs research comparison; not imported or bundled by the app."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import numpy as np

MODEL_REPOSITORY = 'adefossez/HTDemucs'
MODEL_REVISION = 'cbc8a9b1a87023b7fd74e7b3412e6321c0eab003'
MODEL_FILE = '955717e8.safetensors'
MODEL_SHA256 = 'd9fa14133cfcc034a6758923bb3a8ca9f8dfd0b582134643bbf83f72c17576dd'
MODEL_BYTES = 84025440
VERSION = 1
RATE = 48000
STEMS = ('drums', 'bass', 'other', 'vocals')
VARIANTS = ('original', 'instrumental', 'drums', 'vocals', 'recombined')


def sha256(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def normalization(audio):
    """Demucs normalization, bounded for silence and phase-opposed stereo."""
    ref = audio.mean(axis=1)
    mean = float(ref.mean())
    scale = max(float(ref.std()), float(audio.std()) * .1, 1e-8)
    return mean, scale

def load_model(path):
    if path.stat().st_size != MODEL_BYTES or sha256(path) != MODEL_SHA256:
        raise ValueError('The local separation model does not match the verified version.')
    from demucs.htdemucs import HTDemucs
    from safetensors import safe_open

    def fraction(value):
        if value.get('_type') == 'fraction':
            return Fraction(value['numerator'], value['denominator'])
        return value

    with safe_open(path, framework='pt', device='cpu') as handle:
        metadata = handle.metadata()
        if metadata.get('klass') != 'demucs.htdemucs.HTDemucs' or 'structure' in metadata:
            raise ValueError('Unsupported separation checkpoint format.')
        # Explicit known class; no imports or pickled objects from metadata.
        model = HTDemucs(*json.loads(metadata['args']),
                         **json.loads(metadata['kwargs'], object_hook=fraction))
        model.load_state_dict({key: handle.get_tensor(key) for key in handle.keys()}, strict=True)
    if tuple(model.sources) != STEMS or model.samplerate != 44100 or model.audio_channels != 2:
        raise ValueError('The model does not produce the expected stereo sources.')
    return model.eval()
