"""Transition fade settings and the shared audio/visual volume envelope."""
from __future__ import annotations

import math
import numpy as np

import djmix

FIELDS = ('fades_linked', 'incoming_fade_bars', 'outgoing_fade_bars',
          'incoming_fade_curve', 'outgoing_fade_curve')
CURVES = ('smooth', 'linear', 'fast', 'slow')


def validate(clip):
    if type(clip.fades_linked) is not bool:
        raise djmix.MixError('Link fades must be on or off.')
    for name in ('incoming_fade_bars', 'outgoing_fade_bars'):
        value = getattr(clip, name)
        if value is not None and (type(value) not in (int, float) or
                                  not math.isfinite(value) or not .25 <= value <= 32):
            raise djmix.MixError('Fade lengths must be between one beat and 32 bars.')
    if clip.incoming_fade_curve not in CURVES or clip.outgoing_fade_curve not in CURVES:
        raise djmix.MixError('Choose Smooth, Linear, Fast or Slow for the fade curve.')


def duration(clip, incoming):
    side = 'incoming' if incoming or clip.fades_linked else 'outgoing'
    value = getattr(clip, side + '_fade_bars')
    return float(clip.blend_bars if value is None else min(value, clip.blend_bars))


def curve(clip, incoming):
    side = 'incoming' if incoming or clip.fades_linked else 'outgoing'
    return getattr(clip, side + '_fade_curve')


def gain(progress, incoming, clip):
    """Incoming is anchored at overlap start; outgoing at overlap end."""
    fraction = duration(clip, incoming) / clip.blend_bars
    phase = np.clip((np.asarray(progress) - (0 if incoming else 1 - fraction)) / fraction, 0, 1)
    shape = curve(clip, incoming)
    if shape == 'smooth':
        return np.sin(phase * np.pi / 2) if incoming else np.cos(phase * np.pi / 2)
    if shape == 'linear':
        return phase if incoming else 1 - phase
    if shape == 'fast':
        decay = np.expm1(4 * (1 - phase)) / np.expm1(4.)
        return 1 - decay if incoming else decay
    rise = np.expm1(4 * phase) / np.expm1(4.)
    return rise if incoming else 1 - rise


def token(clip, incoming=None):
    """Only audible differences enter caches; linking alone is bookkeeping."""
    if incoming is not None:
        settings = [duration(clip, incoming) / clip.blend_bars, curve(clip, incoming)]
        return None if settings == [1., 'smooth'] else ['fade', 1, *settings]
    settings = [duration(clip, True) / clip.blend_bars, curve(clip, True),
                duration(clip, False) / clip.blend_bars, curve(clip, False)]
    return None if settings == [1., 'smooth', 1., 'smooth'] else ['fades', 1, *settings]


def panel_values(clip):
    return dict(linked=clip.fades_linked, in_bars=duration(clip, True), out_bars=duration(clip, False),
                in_curve=curve(clip, True), out_curve=curve(clip, False))


def apply_settings(clip, values):
    clip.fades_linked = bool(values['linked'])
    for side, prefix in (('incoming', 'in'), ('outgoing', 'out')):
        bars = float(values[prefix + '_bars'])
        setattr(clip, side + '_fade_bars', None if bars == clip.blend_bars else bars)
        setattr(clip, side + '_fade_curve', values[prefix + '_curve'])
    validate(clip)


def set_duration(clip, incoming, bars):
    values = panel_values(clip)
    values['in_bars' if incoming else 'out_bars'] = float(bars)
    if values['linked']:
        values['in_bars'] = values['out_bars'] = float(bars)
    apply_settings(clip, values)


def reset(clip):
    clip.fades_linked = True
    clip.incoming_fade_bars = clip.outgoing_fade_bars = None
    clip.incoming_fade_curve = clip.outgoing_fade_curve = 'smooth'
