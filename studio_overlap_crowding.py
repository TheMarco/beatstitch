"""Experimental evidence of simultaneous changing pitched patterns.

This is not melody transcription or a musical-quality classifier. It describes
the rendered audio, including fades/effects; compatible keys are not a veto.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
import librosa
import soundfile as sf
from scipy.ndimage import median_filter

import djmix
from studio_cache import digest, source_signature
from studio_engine import layout
from studio_storage import managed_cache

VERSION = 1
RATE, HOP, FFT = 22050, 256, 4096
STEP = .1


def pitched_pattern(audio):
    """Bounded stereo-power analysis; never cancel opposite-polarity channels."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        audio = audio[:, None]
    if (audio.ndim != 2 or audio.shape[1] not in (1, 2) or not len(audio)
            or len(audio) > 10 * RATE or not np.isfinite(audio).all()):
        raise ValueError('Pattern analysis needs finite mono/stereo audio, up to ten seconds at 22050 Hz.')
    spectrum = librosa.stft(audio.T, n_fft=FFT, hop_length=HOP)
    power = np.mean(abs(spectrum) ** 2, axis=0)
    magnitude = np.sqrt(power)
    harmonic, _ = librosa.decompose.hpss(magnitude, kernel_size=31, margin=3.)
    frequencies = librosa.fft_frequencies(sr=RATE, n_fft=FFT)
    band = (frequencies >= 220) & (frequencies <= 3520)
    total = np.sum(power[band], axis=0)
    harmonic_power = harmonic ** 2
    # A bassline's upper harmonics can look like a second lead. Remove bins
    # explained by a strong low fundamental before measuring upper pitch motion.
    # This is deliberately conservative when bass and a lead share harmonics.
    low_pitch, low_magnitude = librosa.piptrack(S=harmonic, sr=RATE, n_fft=FFT,
                                              fmin=45, fmax=220, threshold=.2)
    low_index = np.argmax(low_magnitude, axis=0)
    bass = low_pitch[low_index, np.arange(harmonic.shape[1])]
    multiples = frequencies[:, None] / np.maximum(bass[None, :], 1e-10)
    nearest = np.rint(multiples)
    explained = ((bass[None, :] > 0) & (nearest >= 2)
                 & (np.abs(frequencies[:, None] - nearest * bass[None, :])
                    < np.maximum(2 * RATE / FFT, frequencies[:, None] * .015)))
    harmonic_power[explained] = 0
    harmonic_power[~band] = 0
    energy = np.sum(harmonic_power, axis=0)
    chroma = librosa.feature.chroma_stft(S=harmonic_power, sr=RATE, n_fft=FFT,
                                        hop_length=HOP, tuning=0, norm=None)
    # Remove sub-frame pitch jumps; stable 100 ms observations avoid treating
    # every vibrato crossing or drum transient as a new musical event.
    chroma = median_filter(chroma, size=(1, 9))
    count = max(1, int(np.ceil(len(audio) / RATE / STEP)))
    bounds = np.minimum(np.rint(np.arange(count + 1) * STEP * RATE / HOP).astype(int), chroma.shape[1])
    profiles, levels, fractions = [], [], []
    for left, right in zip(bounds, bounds[1:]):
        right = max(left + 1, right)
        profiles.append(np.mean(chroma[:, left:right], axis=1))
        levels.append(float(np.mean(energy[left:right])) / (FFT ** 2 * .1875))
        fractions.append(float(np.sum(energy[left:right]) / max(np.sum(total[left:right]), 1e-20)))
    profiles = np.asarray(profiles)
    profiles /= np.maximum(np.sum(profiles, axis=1, keepdims=True), 1e-20)
    return {'profiles': profiles, 'energy': np.asarray(levels), 'fraction': np.asarray(fractions)}


def pattern_motion(side):
    profiles = side['profiles']
    strong = (np.max(profiles, axis=1) >= .2) & (side['fraction'] >= .12)
    notes = np.argmax(profiles, axis=1)
    runs = []
    for index, note in enumerate(notes):
        if not strong[index]:
            note = -1
        if runs and runs[-1][0] == note:
            runs[-1][1] += 1
        else:
            runs.append([int(note), 1])
    stable = [note for note, frames in runs if note >= 0 and frames >= 2]
    return {'active': bool(np.mean(strong) >= .45 and len(stable) >= max(4, math.ceil(len(profiles) * STEP))
                           and len(set(stable)) >= 3),
            'events': len(stable), 'notes': len(set(stable)), 'coverage': float(np.mean(strong))}


def compare_patterns(outgoing, incoming):
    """One four-second observation; thresholds are heuristics, not confidence."""
    a, b = outgoing, incoming
    if len(a['profiles']) != len(b['profiles']):
        raise ValueError('Both sides must cover the same interval.')
    motions = [pattern_motion(side) for side in (a, b)]
    power = np.maximum(a['energy'], b['energy'])
    # The relative threshold respects the actual mix envelopes. An almost-muted
    # lead must not become as important as the audible lead after normalization.
    audible = (np.minimum(a['energy'], b['energy']) >= .032 * power) & (power > 1e-6)
    similarity = np.sum(a['profiles'] * b['profiles'], axis=1) / np.maximum(
        np.linalg.norm(a['profiles'], axis=1) * np.linalg.norm(b['profiles'], axis=1), 1e-20)
    different = float(np.mean(audible & (similarity < .82)))
    return {'flagged': all(m['active'] for m in motions) and different >= .4,
            'different_fraction': different, 'audible_fraction': float(np.mean(audible)),
            'outgoing': motions[0], 'incoming': motions[1]}


def _valid_result(result, seconds):
    try:
        if (result['version'] != VERSION or result['state'] not in ('possible_crowding', 'not_flagged', 'uncertain')
                or result['overlap_seconds'] != seconds or not isinstance(result['parts'], list)):
            return False
        cursor, flagged = 0., 0.
        for part in result['parts']:
            if (type(part['flagged']) is not bool or part['start'] != cursor
                    or not cursor < part['end'] <= seconds):
                return False
            cursor = part['end']
            for name in ('different_fraction', 'audible_fraction'):
                if not 0 <= part[name] <= 1: return False
            for name in ('outgoing', 'incoming'):
                side = part[name]
                if (type(side['active']) is not bool or not 0 <= side['coverage'] <= 1
                        or type(side['events']) is not int or not 0 <= side['events'] <= 100
                        or type(side['notes']) is not int or not 0 <= side['notes'] <= 12):
                    return False
            if part['flagged']: flagged += part['end'] - part['start']
        return (math.isfinite(seconds) and seconds > 0 and result['seconds'] == flagged
                and ((seconds < 4 and not result['parts'] and result['state'] == 'uncertain')
                     or (seconds >= 4 and cursor == seconds
                         and result['state'] == ('possible_crowding' if flagged else 'not_flagged'))))
    except (KeyError, TypeError, ValueError):
        return False


@managed_cache
def analyze_crowding(session, index, report, cache_dir, progress=lambda _: None):
    """Inspect only the overlap in rendered stems; completed results are reusable."""
    positions = layout(session)
    if not 0 < index < len(positions):
        raise djmix.MixError('Select a transition between two songs.')
    from studio_tempo_curve import preview_overlap
    start, end = preview_overlap(session, index, report['start'])
    seconds = (end - start) / djmix.SR
    if not 0 < seconds <= 600:
        raise djmix.MixError('This experimental check supports overlaps up to ten minutes.')
    folder = Path(report['folder'])
    paths = [folder / f'{side}.wav' for side in ('outgoing', 'incoming')]
    def identity():
        return [VERSION, start, end, *[source_signature(path) for path in paths]]
    key = identity()
    cached_file = folder / ('crowding-' + digest(key) + '.json')
    progress('Checking for a saved musical crowding result')
    try:
        saved = json.loads(cached_file.read_text())
        if saved['key'] == key and _valid_result(saved['analysis'], seconds):
            progress('Reusing this blend’s musical crowding check')
            return saved['analysis']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    parts = []
    with sf.SoundFile(paths[0]) as a, sf.SoundFile(paths[1]) as b:
        if any(s.samplerate != djmix.SR or s.channels != 2 or start < 0 or end > s.frames for s in (a, b)):
            raise djmix.MixError('Prepare this blend again before checking musical crowding.')
        if seconds >= 4:
            # No tiny remainder; each observation covers four to eight seconds.
            count = max(1, int(seconds // 4))
            bounds = np.rint(np.linspace(start, end, count + 1)).astype(int)
            for i, (left, right) in enumerate(zip(bounds, bounds[1:])):
                features = []
                for name, stream in (('Outgoing', a), ('Incoming', b)):
                    progress(f'Checking musical patterns · {name.lower()} · part {i + 1} of {count}')
                    stream.seek(int(left))
                    audio = stream.read(int(right - left), dtype='float32', always_2d=True)
                    audio = librosa.resample(audio.T, orig_sr=djmix.SR, target_sr=RATE).T
                    features.append(pitched_pattern(audio))
                parts.append({'start': (left - start) / djmix.SR, 'end': (right - start) / djmix.SR,
                              **compare_patterns(*features)})
    flagged = sum(p['end'] - p['start'] for p in parts if p['flagged'])
    result = {'version': VERSION, 'state': 'possible_crowding' if flagged else 'not_flagged' if parts else 'uncertain',
              'seconds': flagged, 'overlap_seconds': seconds, 'parts': parts}
    progress('Musical crowding check ready')  # Cancellation must precede publication.
    if key != identity():
        raise djmix.MixError('Prepared audio changed during this check. Check the blend again.')
    with tempfile.NamedTemporaryFile(mode='w', prefix='crowding-', suffix='.tmp', dir=folder, delete=False) as stream:
        stage = Path(stream.name)
        try:
            json.dump({'key': key, 'analysis': result}, stream, allow_nan=False)
            stream.close(); os.replace(stage, cached_file)
        finally:
            stage.unlink(missing_ok=True)
    return result


def crowding_advice(result):
    if result['state'] == 'uncertain':
        return 'Too little overlap for this musical crowding check (at least four seconds needed). Listen to judge.'
    if result['state'] == 'possible_crowding':
        return (f'Possible musical crowding in roughly {result["seconds"]:.1f} seconds of this overlap. '
                'Both sides have different moving pitched patterns. They may work together; listen before changing anything.')
    return ('No musical crowding flagged by this experimental check. '
            'It can miss competing parts; this is not a rating of how well the blend works.')


def shorter_choice(session, index):
    """An explicitly unscored draft, keeping cues/pitch and checking protection."""
    if not 0 < index < len(session.clips):
        return None
    current = session.clips[index].blend_bars
    shorter = [n for n in (1, 2, 4, 8, 16) if n < current]
    if not shorter:
        return None
    candidate = copy.deepcopy(session)
    candidate.clips[index].blend_bars = max(shorter)
    layout(candidate)  # Both neighboring blends and protected passages must fit.
    return max(shorter)
