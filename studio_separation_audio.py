"""Synchronized monitoring of original audio and estimated musical parts."""
from __future__ import annotations

import numpy as np

from studio_ab_audio import FADE_FRAMES
from studio_transition_audio import BlendMixer, BlendTransport

VARIANTS = ('original', 'instrumental', 'drums', 'vocals', 'recombined')


class SeparationMixer(BlendMixer):
    WEIGHTS = {name: tuple(float(i == j) for i in range(5)) for j, name in enumerate(VARIANTS)}

    def __init__(self, *versions):
        if len(versions) != len(VARIANTS):
            raise ValueError('All five listening versions are required.')
        super().__init__(versions[0], versions[1])
        if any(a.shape != versions[0].shape or not np.isfinite(a).all() for a in versions[2:]):
            raise ValueError('Every version must contain matching, finite stereo frames.')
        self.versions = versions
        self.gains = self.target = self.WEIGHTS['original']
        self.remaining = 0

    def select(self, variant):
        if variant not in self.WEIGHTS:
            raise ValueError('Unknown separation listening version.')
        target = self.WEIGHTS[variant]
        if target != self.target:
            self.target, self.remaining = target, FADE_FRAMES

    def seek(self, frame):
        super().seek(frame)
        self.remaining = 0

    def read(self, frames):
        count = int(min(frames, self.frames - self.frame))
        if count <= 0: return b''
        indices = np.arange(self.frame, self.frame + count) % self.length
        amount = np.minimum(np.arange(1, count + 1, dtype=np.float32) / max(1, self.remaining), 1)
        current, target = np.asarray(self.gains), np.asarray(self.target)
        # A common ramp preserves unity gain even when switching again mid-fade.
        weights = current[:, None] + (target - current)[:, None] * amount
        audio = sum(samples[indices] * weight[:, None] for samples, weight in zip(self.versions, weights))
        self.gains = tuple(weights[:, -1])
        self.remaining = max(0, self.remaining - count)
        self.frame += count
        return np.asarray(audio, dtype='<f4').tobytes()


class SeparationTransport(BlendTransport):
    FILES = VARIANTS
    MIXER = SeparationMixer
    DEFAULT = 'original'
