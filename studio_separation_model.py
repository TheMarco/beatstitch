"""Redistributable, pinned Spleeter model for the isolated app worker."""
import hashlib
from pathlib import Path

MODEL_FILE = 'spleeter-4stems.npz'
MODEL_SHA256 = 'a8b3fb520dc13f47c40e5b5d7df88c682a409da56c8bf03b048290e27543617c'
MODEL_BYTES = 157306998
VERSION = 2
RATE = 48000
STEMS = ('drums', 'bass', 'other', 'vocals')
VARIANTS = ('original', 'instrumental', 'drums', 'vocals', 'recombined')


def sha256(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def load_model(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size != MODEL_BYTES or sha256(path) != MODEL_SHA256:
        raise ValueError('The local separation model does not match the verified version.')
    from studio_spleeter_model import load_models
    return load_models(path)
