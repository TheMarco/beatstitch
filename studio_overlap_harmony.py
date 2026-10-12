"""Local tonal evidence from the two actually rendered overlap stems.

Pitch-profile correlations are descriptive similarities, not confidence
probabilities. Weak, short, changing or ambiguous passages remain uncertain.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile

import librosa
import numpy as np
import soundfile as sf

import djmix
from analyze_audio import key_candidates
from studio_cache import digest, source_signature
from studio_engine import layout
from studio_storage import managed_cache

VERSION = 1
RATE, HOP = 22050, 512
WINDOW = 8


def tonal_evidence(audio, rate=RATE):
    """Analyze at most eight seconds; stereo is combined after pitch extraction."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1: audio = audio[:, None]
    if (audio.ndim != 2 or audio.shape[1] not in (1, 2) or not len(audio)
            or not np.isfinite(audio).all() or rate != RATE or len(audio) > (WINDOW + .1) * RATE):
        raise ValueError('Local harmony needs finite, bounded mono or stereo audio at 22050 Hz.')
    duration = len(audio) / rate
    empty = {'duration': duration, 'state': 'uncertain', 'keys': [], 'candidates': [], 'tonal': False}
    peak = float(np.max(np.abs(audio)))
    if peak < 1e-5:
        return {**empty, 'state': 'silent', 'reason': 'Little audible material in this part of the overlap.'}
    if duration < 1:
        return {**empty, 'reason': 'Too little overlap audio to estimate a key.'}
    y = (audio / peak).T
    spectrum = librosa.stft(y, n_fft=2048, hop_length=HOP)
    harmonic, _ = librosa.decompose.hpss(spectrum, margin=(3, 3))
    fraction = float(np.sum(np.abs(harmonic) ** 2) / max(float(np.sum(np.abs(spectrum) ** 2)), 1e-12))
    if fraction < .025:
        return {**empty, 'harmonic_fraction': fraction,
                'reason': 'Little sustained tonal evidence. This does not establish a drums-only passage.'}
    harmonic_y = librosa.istft(harmonic, hop_length=HOP, length=len(audio))
    chroma = librosa.feature.chroma_cqt(y=harmonic_y, sr=RATE, hop_length=HOP, n_octaves=7,
                                       bins_per_octave=12, norm=None, tuning=0.)
    chroma = np.mean(chroma, axis=0)
    vector = np.mean(chroma, axis=1)
    concentration = float(np.std(vector) / max(float(np.mean(vector)), 1e-12))
    tonal = fraction >= .06 and concentration >= .35
    candidates = key_candidates(chroma)
    supported = [c['key'] for c in candidates if c['correlation'] >= .6
                 and c['correlation'] >= candidates[0]['correlation'] - .12][:2] if candidates else []
    pitches = int(np.count_nonzero(vector >= .2 * float(vector.max())))
    if not tonal:
        reason = 'No clear tonal pattern. Do not assume this is drums only; listen for bass, voices and tails.'
    elif pitches < 3:
        reason = 'Pitched sound is present, but too few distinct notes establish a key.'
    elif duration < 4:
        reason = 'Pitched sound is present, but this short overlap leaves the key uncertain.'
    elif not supported:
        reason = 'Pitched sound is present, but it does not support a clear major/minor key.'
    else:
        return {**empty, 'state': 'estimated', 'keys': supported, 'candidates': candidates,
                'tonal': True, 'harmonic_fraction': fraction,
                'reason': 'Local pitch-profile estimates; correlations are not confidence probabilities.'}
    return {**empty, 'tonal': tonal, 'candidates': candidates, 'harmonic_fraction': fraction, 'reason': reason}


def summarize_side(parts):
    keys = list(dict.fromkeys(key for part in parts for key in part['keys']))
    audible = [part for part in parts if part['state'] != 'silent']
    known = [part for part in audible if part['state'] == 'estimated']
    # A shared candidate throughout measured windows supports one local key.
    shared = set(known[0]['keys']) if known else set()
    for part in known[1:]: shared.intersection_update(part['keys'])
    stable = bool(known) and len(known) == len(audible) and bool(shared)
    return {'state': 'estimated' if stable else 'uncertain',
            'keys': [key for key in keys if key in shared] if stable else keys,
            'tonal': any(part['tonal'] for part in parts), 'parts': parts,
            'reason': ('Estimates are consistent across the audible overlap.' if stable else
                       'Key evidence varies or is incomplete across the overlap.' if known else
                       next((part['reason'] for part in audible), 'Little audible material in the overlap.'))}


def compare_sides(outgoing, incoming):
    result = {'state': 'uncertain', 'reason': 'Overlap harmony is uncertain. Solo each side and listen for competing notes.'}
    if any(side['state'] != 'estimated' for side in (outgoing, incoming)):
        return result
    relations = [djmix.relationship(djmix.parse_key(a), djmix.parse_key(b))
                 for a in outgoing['keys'] for b in incoming['keys']]
    if relations and all(relations):
        return {'state': 'related', 'reason': 'The local key estimates are related. This does not rule out competing melodies or vocals.'}
    if relations and not any(relations):
        return {'state': 'possible_clash', 'reason': 'The local key estimates are unrelated. Listen for clashing notes; try different cues, a shorter overlap, or a pitch alternative.'}
    return result


def _valid_result(value):
    try:
        if (value['version'] != VERSION or value['state'] not in ('uncertain', 'related', 'possible_clash')
                or not math.isfinite(value['seconds']) or value['seconds'] <= 0
                or not isinstance(value['reason'], str)):
            return False
        for name in ('outgoing', 'incoming'):
            side = value[name]
            if side['state'] not in ('estimated', 'uncertain') or type(side['tonal']) is not bool:
                return False
            if not isinstance(side['keys'], list) or not isinstance(side['parts'], list) or not side['parts']:
                return False
            if side['state'] == 'estimated' and not side['keys']:
                return False
            for key in side['keys']: djmix.parse_key(key)
            if not isinstance(side['reason'], str): return False
        return True
    except (KeyError, TypeError, ValueError, djmix.MixError):
        return False


def _analyze_stem(path, start, end, progress):
    parts = []
    with sf.SoundFile(path) as stream:
        if stream.samplerate != djmix.SR or stream.channels != 2:
            raise djmix.MixError('Prepare this blend again before checking its harmony.')
        length = end - start
        if start < 0 or end > stream.frames or length <= 0:
            raise djmix.MixError('The prepared audio does not cover this overlap.')
        size = round(WINDOW * stream.samplerate)
        # Equal-length windows avoid an uninformative tiny final fragment.
        count = max(1, math.ceil(length / size))
        boundaries = np.rint(np.linspace(start, end, count + 1)).astype(int)
        for i, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
            progress(f'Checking overlap notes · part {i + 1} of {count}')
            stream.seek(int(left)); audio = stream.read(int(right - left), dtype='float32', always_2d=True)
            audio = librosa.resample(audio.T, orig_sr=stream.samplerate, target_sr=RATE).T
            part = tonal_evidence(audio)
            parts.append({**part, 'start': (left - start) / stream.samplerate, 'end': (right - start) / stream.samplerate})
    return summarize_side(parts)


@managed_cache
def analyze_overlap(session, index, report, cache_dir, progress=lambda _: None):
    """No extra rendering: inspect the exact overlap inside a prepared pair preview."""
    positions = layout(session)
    incoming, outgoing = positions[index], positions[index - 1]
    from studio_tempo_curve import preview_overlap
    start, end = preview_overlap(session, index, report['start'])
    folder = Path(report['folder'])
    paths = [folder / f'{kind}.wav' for kind in ('outgoing', 'incoming')]
    key = [VERSION, start, end, *[source_signature(p) for p in paths]]
    cached_file = folder / ('harmony-' + digest(key) + '.json')
    try:
        cached = json.loads(cached_file.read_text())
        if cached['key'] == key and _valid_result(cached['analysis']):
            progress('Reusing this overlap’s harmony check')
            return cached['analysis']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    sides = []
    for name, path in zip(('Outgoing', 'Incoming'), paths):
        sides.append(_analyze_stem(path, start, end, lambda message: progress(f'{name}: {message}')))
    result = {'version': VERSION, 'seconds': (end - start) / djmix.SR,
              'outgoing': sides[0], 'incoming': sides[1], **compare_sides(*sides)}
    progress('Overlap harmony ready')
    if key != [VERSION, start, end, *[source_signature(p) for p in paths]]:
        raise djmix.MixError('Prepared audio changed during the harmony check. Listen again.')
    with tempfile.NamedTemporaryFile(mode='w', prefix='harmony-', suffix='.tmp', dir=folder, delete=False) as stream:
        stage = Path(stream.name)
        try:
            json.dump({'key': key, 'analysis': result}, stream, allow_nan=False)
            stream.close(); os.replace(stage, cached_file)
        finally:
            stage.unlink(missing_ok=True)
    return result


def advice_text(result, session, index):
    labels = {'related': 'Related local key estimates', 'possible_clash': 'Possible clash in this overlap',
              'uncertain': 'Overlap key uncertain'}
    describe = lambda side: ' / '.join(side['keys']) if side['state'] == 'estimated' else 'Uncertain'
    if result['state'] == 'uncertain':
        def brief(side):
            if side['state'] == 'estimated': return 'estimated ' + ' / '.join(side['keys'])
            if side['keys']: return 'key evidence varies'
            return 'pitched sound, key unclear' if side['tonal'] else 'no clear tonal pattern'
        text = (f"Overlap key uncertain. Out: {brief(result['outgoing'])}. In: {brief(result['incoming'])}. "
                'Solo each side to check notes and tails.')
    else:
        text = f"{labels[result['state']]} · {describe(result['outgoing'])} → {describe(result['incoming'])}. {result['reason']}"
    a, b = session.clips[index - 1:index + 1]
    for marked, side, name in ((a.outro_percussive, result['outgoing'], 'outro'),
                                (b.intro_percussive, result['incoming'], 'intro')):
        if marked and side['tonal']:
            text += f' Pitched material was detected in the marked drum {name}; check bass, voices and tonal tails.'
    return text
