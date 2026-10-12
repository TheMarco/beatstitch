"""Reproducible original audio for three short, loudness-matched listening lessons."""
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

import djmix
from build_demo_assets import voice
from studio_engine import _weights

RATE = djmix.SR
SECONDS = 16
COUNT = SECONDS * RATE


def add(target, sound, seconds, gain=1):
    left = round(seconds * RATE); right = min(len(target), left + len(sound))
    if 0 <= left < right: target[left:right] += sound[:right - left] * gain


def drums(times, variation=0):
    rng = np.random.default_rng(920 + variation)
    output = np.zeros(COUNT, np.float32)
    t = np.arange(round(.4 * RATE)) / RATE
    kick = .34 * np.sin(2 * np.pi * (48 * t + 90 * .02 * (1 - np.exp(-t / .02)))) * np.exp(-t / .09)
    kick += .045 * rng.standard_normal(len(t)) * np.exp(-t / .002)
    t = np.arange(round(.12 * RATE)) / RATE
    noise = sosfilt(butter(2, 1800, 'highpass', fs=RATE, output='sos'), rng.standard_normal(len(t)))
    hat = .045 * noise * np.exp(-t / .018)
    clap = .09 * noise * np.exp(-t / .045)
    for i, at in enumerate(times):
        add(output, kick, at)
        add(output, hat, at + .25)
        if i % 2: add(output, clap, at)
    return np.column_stack((output, output))


def music(root=60, bass_only=False, variation=0):
    rng = np.random.default_rng(328 + variation)
    output = np.zeros((COUNT, 2), np.float32)
    notes = (0, 7, 3, 10, 7, 3, 12, 7)
    for step in range(64):
        note = root - 24 + (7 if (step + variation) % 4 >= 2 else 0) if bass_only else root + notes[step % 8]
        sound = voice(note, .32, 'bass' if bass_only else 'pluck', rng)
        at = step * .25 + (.125 * variation if bass_only else 0)
        level = .24 if bass_only else .065
        add(output[:, 0], sound, at, level * (.9 if step % 2 else .7))
        add(output[:, 1], sound, at, level * (.7 if step % 2 else .9))
    return output


def examples():
    times = np.arange(32) * .5
    regular = drums(times)
    # Incoming beat number integrates a tempo ramp from 120 to 124 BPM.
    ramp = (124 - 120) / SECONDS / 60
    drifting_times = (-2 + np.sqrt(4 + 2 * ramp * np.arange(33))) / ramp
    timing = (.62 * regular + .62 * drums(drifting_times, 1),
              .62 * regular + .62 * drums(times, 1))
    rng = np.random.default_rng(44)
    chord = sum(voice(60 + n, SECONDS, 'pad', rng) for n in (0, 3, 7)) * .045
    foundation = .55 * regular + chord[:, None]
    keys = (foundation + music(61), foundation + music(60))
    sources = (.7 * regular + music(60, True), .65 * drums(times, 1) + music(60, True, 1))
    bass = []
    with tempfile.TemporaryDirectory(prefix='dj-lesson-bands-') as temporary:
        root = Path(temporary); bands = []
        for i, source in enumerate(sources):
            raw = root / f'{i}.raw'; source.astype('<f4').tofile(raw)
            low, high = djmix.split_bands(raw, root, str(i))
            bands.append((np.array(low), np.array(high)))
        n = np.arange(COUNT)
        for handoff in (False, True):
            combined = np.zeros_like(sources[0])
            for incoming, (low, high) in enumerate(bands):
                lo, hi = _weights(n, 0, COUNT, bool(incoming), handoff, 120.)
                combined += low * lo[:, None] + high * hi[:, None]
            bass.append(combined)
    return {'timing': timing, 'keys': keys, 'bass': bass}


def build(destination):
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=True)
    for identity, pair in examples().items():
        folder = destination / identity; folder.mkdir(exist_ok=True)
        n = np.arange(COUNT)
        edge = np.minimum(np.clip(n / (.005 * RATE), 0, 1), np.clip((COUNT - 1 - n) / (.005 * RATE), 0, 1))
        measurements = []
        for name, audio in zip(('current', 'draft'), pair):
            sf.write(folder / f'{name}.wav', audio * edge[:, None], RATE, subtype='FLOAT')
            measurements.append(djmix.meter(folder / f'{name}.wav'))
        target = min(-20, *(m['lufs'] - m['true_peak_db'] - 3 for m in measurements))
        report = {'version': 1, 'id': identity, 'seconds': SECONDS, 'bpm': 120,
                  'sha256': {}, 'measured': {}, 'target_lufs': target}
        for name, measurement in zip(('current', 'draft'), measurements):
            path = folder / f'{name}.wav'; audio, _ = sf.read(path, dtype='float32', always_2d=True)
            audio *= 10 ** ((target - measurement['lufs']) / 20)
            sf.write(path, audio, RATE, subtype='PCM_24')
            final = djmix.meter(path)
            if abs(final['lufs'] - target) > .2 or final['true_peak_db'] > -2.8:
                raise RuntimeError(f'{identity}/{name} failed the listening-level check: {final}')
            report['measured'][name] = final
            report['sha256'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (folder / 'lesson.json').write_text(json.dumps(report, indent=2) + '\n')
        print(identity, report['measured'], flush=True)
    (destination / 'ABOUT.txt').write_text(
        'Original synthesized teaching audio created for DJ Studio. No third-party recordings or voices.\n'
        'Each lesson contains two 16-second, 48 kHz stereo versions, matched with constant listening gain.\n'
        'These intentionally simple examples illustrate choices; they do not benchmark automatic analysis\n'
        'or promise that a particular correction will suit every song. Rebuild: build_listening_lessons.py.\n')


if __name__ == '__main__':
    build(Path(__file__).resolve().parent / 'assets/lessons')
