"""Reproducible original dance music for the bundled, editable beginner demo."""
from pathlib import Path
import hashlib
import json
import math

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

from studio_engine import Clip, Session, Track

RATE = 48000
BARS = 32
SONGS = (('Night Drive', 124., 57, 'A minor'), ('Signal Bloom', 126., 52, 'E minor'),
         ('First Light', 128., 57, 'A minor'))


def frequency(note):
    return 440 * 2 ** ((note - 69) / 12)


def voice(note, duration, kind, rng):
    t = np.arange(round(duration * RATE), dtype=np.float32) / RATE
    f = frequency(note)
    if kind == 'bass':
        signal = np.sin(2 * np.pi * f * t) + .24 * np.sin(4 * np.pi * f * t)
        shape = (1 - np.exp(-t / .005)) * np.exp(-t / .16)
    elif kind == 'pad':
        signal = sum(np.sin(2 * np.pi * f * ratio * t + phase) * strength
                     for ratio, phase, strength in ((1, 0, 1), (1.003, .4, .4), (2, .6, .24), (3, 1, .1)))
        shape = np.minimum(t / .12, 1) * np.minimum((duration - t) / .35, 1)
    else:
        signal = sum(np.sin(2 * np.pi * f * h * t) / h ** 1.6 for h in range(1, 7))
        shape = (1 - np.exp(-t / .003)) * np.exp(-t / .14)
    return np.asarray(signal * np.clip(shape, 0, 1), dtype=np.float32)


def song(bpm, root, variant):
    beat = 60 / bpm
    count = round(BARS * 4 * beat * RATE)
    drums = np.zeros(count, np.float32)
    music = np.zeros((count, 2), np.float32)
    rng = np.random.default_rng(7821 + variant)

    def add_mono(target, sound, seconds, gain=1):
        start = round(seconds * RATE); stop = min(count, start + len(sound))
        if start < count: target[start:stop] += sound[:stop - start] * gain

    def add_note(note, at, duration, gain, kind='pluck', pan=0):
        sound = voice(note, duration, kind, rng)
        add_mono(music[:, 0], sound, at * beat, gain * math.sqrt((1 - pan) / 2))
        add_mono(music[:, 1], sound, at * beat, gain * math.sqrt((1 + pan) / 2))
        if kind == 'pluck':
            for tap, level in ((.75, .20), (1.5, .08)):
                add_mono(music[:, 0], sound, (at + tap) * beat, gain * level * math.sqrt((1 + pan) / 2))
                add_mono(music[:, 1], sound, (at + tap) * beat, gain * level * math.sqrt((1 - pan) / 2))

    t = np.arange(round(.6 * RATE), dtype=np.float32) / RATE
    phase = 2 * np.pi * (49 * t + 95 * .021 * (1 - np.exp(-t / .021)))
    kick = .48 * np.sin(phase) * np.exp(-t / .095)
    kick += .04 * rng.standard_normal(len(t)).astype(np.float32) * np.exp(-t / .003)
    t = np.arange(round(.2 * RATE), dtype=np.float32) / RATE
    noise = sosfilt(butter(2, 1400, 'highpass', fs=RATE, output='sos'), rng.standard_normal(len(t)))
    hat = np.asarray(.055 * noise * np.exp(-t / .018), np.float32)
    opened = np.asarray(.065 * noise * np.exp(-t / .075), np.float32)
    t = np.arange(round(.26 * RATE), dtype=np.float32) / RATE
    noise = sosfilt(butter(2, (700, 8500), 'bandpass', fs=RATE, output='sos'), rng.standard_normal(len(t)))
    clap_shape = sum(np.where(t >= lag, np.exp(-np.maximum(0, t - lag) / decay), 0)
                     for lag, decay in ((0, .008), (.011, .008), (.023, .052)))
    clap = np.asarray(.13 * noise * clap_shape + .035 * np.sin(2 * np.pi * 180 * t) * np.exp(-t / .03), np.float32)
    notes = ((0, 7, 10, 12, 7, 3, 2, 7), (0, 3, 7, 14, 12, 7, 5, 3), (12, 7, 3, 7, 10, 7, 2, 0))[variant]
    progression = ((0, (0, 3, 7)), (-4, (0, 4, 7)), (3, (0, 4, 7)), (-2, (0, 4, 7)))
    for bar in range(BARS):
        breakdown = 16 <= bar < 20
        for b in range(4):
            at = (bar * 4 + b) * beat
            if not breakdown: add_mono(drums, kick, at)
            if not breakdown and b % 2 and bar >= 2: add_mono(drums, clap, at, .85)
            add_mono(drums, hat, at + beat / 4, .40 if breakdown else .65)
            add_mono(drums, opened, at + beat / 2, .25 if breakdown else .75)
            if variant != 0 and bar % 8 >= 4:
                add_mono(drums, hat, at + 3 * beat / 4, .4)
        if 8 <= bar < 24:
            chord_root, intervals = progression[((bar - 8) // 2) % 4]
            if bar % 2 == 0:
                for interval in intervals:
                    add_note(root + chord_root + interval, bar * 4, 8 * beat, .033,
                             'pad', pan=(interval - 3) / 14)
            if not breakdown:
                for eighth in range(8):
                    at = bar * 4 + eighth / 2 + .5
                    note = root + chord_root - 24 + (12 if eighth in (3, 7) else 0)
                    add_note(note, at, .7 * beat, .20, 'bass')
            for step, interval in enumerate(notes):
                if breakdown and step % 2: continue
                at = bar * 4 + step / 2
                add_note(root + 12 + interval, at, .7 * beat,
                         .045 if breakdown else .065 + .006 * variant, pan=.35 * (-1) ** step)
    t = np.arange(count, dtype=np.float32) / RATE
    # Pump the synthesized musical layer with the kick; the demo renderer itself
    # still uses normal DJ Studio processing, with no compressor.
    pump = .25 + .75 * (1 - np.exp(-(t % beat) / .12))
    music *= pump[:, None]
    # A full bar of release ends before the authored drums-only outro begins.
    tonal_end = np.clip((24 * 4 * beat - t) / (4 * beat), 0, 1)
    music *= tonal_end[:, None]
    audio = music + drums[:, None]
    fade = np.minimum(np.clip(t / .005, 0, 1), np.clip((count / RATE - t) / .35, 0, 1))
    audio *= fade[:, None]
    audio *= .78 / max(float(np.max(np.abs(audio))), .001)
    return audio


def build(destination):
    destination = Path(destination)
    media = destination / 'media'; media.mkdir(parents=True, exist_ok=True)
    session = Session(name='First mix · Demo', target_bpm=126.)
    for i, (name, bpm, root, key) in enumerate(SONGS):
        print(f'Synthesizing {name} ({bpm:g} BPM, {key})', flush=True)
        audio = song(bpm, root, i)
        relative = f'media/{i + 1:02d}-{name.lower().replace(" ", "-")}.flac'
        path = destination / relative
        sf.write(path, audio, RATE, subtype='PCM_24')
        signature = [relative, path.stat().st_size, path.stat().st_mtime_ns]
        size = math.ceil(len(audio) / 1400)
        mono = np.max(np.abs(audio), axis=1)
        peaks = np.pad(mono, (0, (-len(mono)) % size)).reshape(-1, size).max(axis=1)
        waveform = np.clip(peaks / max(float(np.percentile(peaks, 98)), .001), 0, 1).round(4).tolist()
        duration = len(audio) / RATE
        track = Track(f'demo-{i + 1}', relative, name, duration, bpm, key, cue_out=duration,
                      estimated=False, waveform=waveform, status='Demo · authored BPM, key and regular beat grid',
                      timing={'version': 1, 'state': 'authored', 'source': signature, 'bpm': bpm},
                      beat_grid={'version': 1, 'source': signature, 'bpm': bpm, 'anchor': 0.},
                      fingerprint={'source': signature, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        session.tracks[track.id] = track
        session.clips.append(Clip(track.id, 0., duration, id=f'demo-clip-{i + 1}', blend_bars=8,
                                  intro_percussive=True, outro_percussive=True, energy=3 + (i > 0)))
    value = {**session.to_dict(), '_portable_paths': 1, '_demo_version': 1}
    (destination / 'Mix.djmix').write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    (destination / 'ABOUT.txt').write_text(
        'DJ Studio — First mix demo\n\n'
        'Three original, procedurally synthesized dance recordings made for this project.\n'
        'No external recordings, samples or loops are used. You may use this audio in demo\n'
        'mixes, exports and shared test projects. The generator is build_demo_assets.py.\n\n'
        'Each song is 32 bars of 4/4: 8 bars of drums, 8 bars with bass/chords/lead,\n'
        '4 bars of breakdown, 4 bars returning to the groove, and 8 bars of drums.\n'
        'The musical layer releases before the drums-only ending. BPM, key and beat grids\n'
        'come from the composition; this project does not measure analyzer accuracy.\n'
        'The three-track mix plays at 126 BPM with eight-bar bass-handoff transitions.\n'
        'Pitch and optional blend-level balancing start off. All choices remain editable.\n')
    return destination / 'Mix.djmix'


if __name__ == '__main__':
    build(Path(__file__).resolve().parent / 'assets' / 'demo')
