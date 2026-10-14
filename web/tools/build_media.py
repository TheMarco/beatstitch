"""Regenerate the website's audio, waveform data and fonts from the app itself.

Run from the repository root with the app's environment (FFmpeg and Rubber Band on PATH):

    .venv/bin/python web/tools/build_media.py [project.djmix]

The demo mix is rendered by the real engine from web/tools/demo/demo-mix.djmix, an
excerpt of a saved project whose songs live outside the repository, so it only
rebuilds where those files are. Pass another project to use different songs. The
comparison version uses the same songs, overlaps, fades and per-song loudness gains,
but keeps each song at its original tempo and pitch, lines up the first downbeat of
every blend by hand and uses a plain full-band crossfade. Both are level-matched
before encoding.
Screenshots and brand images are separate steps: web/tools/capture_screens.py and
web/tools/render_brand.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
sys.path.insert(0, str(ROOT))

import djmix  # noqa: E402
import studio_engine  # noqa: E402
import studio_theme  # noqa: E402
from studio_finishing import envelope as finishing_envelope  # noqa: E402
from studio_lessons import LESSONS  # noqa: E402
from studio_projects import read_project  # noqa: E402
from studio_widgets import camelot  # noqa: E402

SR = djmix.SR
PROJECT = WEB / 'tools/demo/demo-mix.djmix'
WAVE_RATE = 16  # buckets per second for the arrangement
LESSON_RATE = 60  # buckets per second for the 16-second lessons
DETAIL_RATE = 150  # buckets per second for the zoomed, scrolling deck view


def encode(source: Path, destination: Path, gain_db: float = 0.0):
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
                    '-af', f'volume={gain_db:.3f}dB', '-ar', '44100', '-c:a', 'libmp3lame', '-b:a', '192k',
                    str(destination)], check=True)


def decode(path: Path) -> np.ndarray:
    """A whole song as stereo float at the engine's sample rate, whatever the file's rate."""
    raw = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-i', str(path),
                          '-map', '0:a:0', '-ac', '2', '-ar', str(SR), '-f', 'f32le', '-'],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype='<f4').reshape(-1, 2).astype(np.float64)


def kick_offset(audio: np.ndarray, cue_in: float, bpm: float, spans) -> float:
    """Median seconds from the song's beat grid to its kick drums, over source spans.

    The grid counts beats from the clip's in point. Kicks are the strongest
    low-frequency onset within half a beat of each grid beat.
    """
    hop = SR // 2000
    low = np.abs(sosfilt(butter(4, 100, 'low', fs=SR, output='sos'), audio.mean(axis=1)))
    onset = np.diff(low[:len(low) // hop * hop].reshape(-1, hop).max(axis=1), prepend=0).clip(0)
    beat = 60 / bpm
    offsets = []
    for a, b in spans:
        for k in range(int(np.ceil((a - cue_in) / beat - 1e-6)), int(np.floor((b - cue_in) / beat + 1e-6))):
            centre = cue_in + k * beat
            lo, hi = int((centre - beat / 2) * 2000), int((centre + beat / 2) * 2000)
            if lo >= 0 and hi <= len(onset):
                offsets.append((lo + int(np.argmax(onset[lo:hi]))) / 2000 - centre)
    return float(np.median(offsets)) if offsets else 0.0


def envelope(audio: np.ndarray, buckets: int):
    """Peak and RMS per bucket of a mono-summed absolute signal."""
    mono = np.abs(audio).max(axis=1) if audio.ndim == 2 else np.abs(audio)
    power = (audio ** 2).mean(axis=1) if audio.ndim == 2 else audio ** 2
    edges = np.linspace(0, len(mono), buckets + 1).astype(int)
    peak = np.array([mono[a:b].max() if b > a else 0 for a, b in zip(edges[:-1], edges[1:])])
    rms = np.sqrt(np.array([power[a:b].mean() if b > a else 0 for a, b in zip(edges[:-1], edges[1:])]))
    return peak, rms


def quantize(values: np.ndarray, scale: float):
    return [int(v) for v in np.clip(np.round(values / scale * 100), 0, 100)]


def plain_mix(session, positions, gains):
    """Original tempos, hand-aligned first downbeats, plain equal-power crossfade."""
    total = positions[-1]['end']
    out = np.zeros((total + SR, 2))
    placement = []
    for index, (clip, position) in enumerate(zip(session.clips, positions)):
        track = session.tracks[clip.track_id]
        audio = decode(Path(track.path)) * 10 ** (gains[index] / 20)
        stretch = session.target_bpm / track.bpm  # mix source seconds per timeline second
        # The first song is anchored where it hands over; later songs where they enter.
        anchor = positions[1]['start'] if index == 0 and len(positions) > 1 else position['start']
        anchor_source = clip.cue_in * SR + (anchor - position['start']) * stretch
        offset = int(round(anchor_source - anchor))  # plain source sample = timeline sample + offset
        first = max(position['start'], int(np.ceil(clip.cue_in * SR - offset)))
        last = min(int(np.floor(clip.cue_out * SR - offset)), total)
        if index + 1 < len(positions):
            last = min(last, positions[index + 1]['start'] + positions[index + 1]['incoming'])
        t = np.arange(first, last)
        gain = np.ones(len(t))
        if index:
            window = position['incoming']
            p = np.clip((t - position['start']) / max(1, window - 1), 0, 1)
            gain *= np.sin(p * np.pi / 2)
        if index + 1 < len(positions):
            start, window = positions[index + 1]['start'], positions[index + 1]['incoming']
            p = np.clip((t - start) / max(1, window - 1), 0, 1)
            gain *= np.cos(p * np.pi / 2)
        out[first:last] += audio[t + offset] * gain[:, None]
        # Source times count from the clip's in point, like the BeatStitch version's.
        placement.append({'start': first / SR, 'end': last / SR, 'sourceStart': (first + offset) / SR - clip.cue_in,
                          'sourceEnd': (last + offset) / SR - clip.cue_in})
    out = out[:total]
    # The same opening and ending fades as the engine, plus its 5 ms edges.
    n = np.arange(total)
    edge = np.minimum(np.clip(n / (0.005 * SR), 0, 1), np.clip((total - 1 - n) / (0.005 * SR), 0, 1))
    out *= (edge * finishing_envelope(session, positions, n))[:, None]
    return out, placement


def main():
    media = WEB / 'media'
    data = WEB / 'data'
    shutil.rmtree(media, ignore_errors=True)
    media.mkdir(parents=True)
    data.mkdir(parents=True, exist_ok=True)

    project = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else PROJECT
    session, _ = read_project(project, lambda message: None)
    positions = studio_engine.layout(session)
    with tempfile.TemporaryDirectory(prefix='beatstitch-web-') as directory:
        work = Path(directory)
        report = studio_engine.render_session(session, work / 'beatstitch.wav', progress=print)
        gains = [min(12, -18 - djmix.meter(Path(session.tracks[c.track_id].path))['lufs']) for c in session.clips]
        plain, placement = plain_mix(session, positions, gains)
        peak = np.abs(plain).max()
        if peak > 0.89:  # keep the comparison below about -1 dBFS before level matching
            plain *= 0.89 / peak
        sf.write(work / 'plain.wav', plain, SR, subtype='FLOAT')
        loud = {name: djmix.meter(work / f'{name}.wav') for name in ('beatstitch', 'plain')}
        target = loud['beatstitch']['lufs']
        plain_gain = target - loud['plain']['lufs']
        if loud['plain']['true_peak_db'] + plain_gain > -0.5:
            raise SystemExit('Level matching would clip the comparison version.')
        encode(work / 'beatstitch.wav', media / 'demo-beatstitch.mp3')
        encode(work / 'plain.wav', media / 'demo-without.mp3', plain_gain)

        mix_audio, _ = sf.read(work / 'beatstitch.wav', always_2d=True)
        plain_audio = plain * 10 ** (plain_gain / 20)
        duration = positions[-1]['end'] / SR
        buckets = int(round(duration * WAVE_RATE))
        mix_env = envelope(mix_audio, buckets)
        plain_env = envelope(plain_audio, buckets)
        mix_scale = max(mix_env[0].max(), plain_env[0].max())

        clips = []
        source_envs = []
        detail_envs = []
        kicks = []
        for index, (clip, position) in enumerate(zip(session.clips, positions)):
            track = session.tracks[clip.track_id]
            audio = decode(Path(track.path))
            # Where the kicks really sit in each overlap, so the page's meter shows measured offsets.
            per_sample = session.target_bpm / track.bpm / SR  # source seconds per timeline sample
            spans = []
            if index:
                spans.append((clip.cue_in, clip.cue_in + position['incoming'] * per_sample))
            if index + 1 < len(positions):
                spans.append((clip.cue_out - positions[index + 1]['incoming'] * per_sample, clip.cue_out))
            kicks.append(kick_offset(audio, clip.cue_in, track.bpm, spans))
            audio = audio[int(clip.cue_in * SR):int(np.ceil(clip.cue_out * SR))] * 10 ** (gains[index] / 20)
            source_envs.append(envelope(audio, int(round(len(audio) / SR * WAVE_RATE))))
            detail_envs.append(envelope(audio, int(round(len(audio) / SR * DETAIL_RATE)))[0])
        clip_scale = max(env[0].max() for env in source_envs)
        detail_scale = max(env.max() for env in detail_envs)
        for index, (clip, position) in enumerate(zip(session.clips, positions)):
            track = session.tracks[clip.track_id]
            key = djmix.parse_key(track.key)
            mix_key = djmix.key_name(djmix.transpose(key, clip.shift))
            clips.append({
                'name': track.name, 'bpm': track.bpm, 'key': track.key, 'camelot': camelot(track.key),
                'tonic': key[0], 'mode': key[1], 'shift': clip.shift, 'mixKey': mix_key, 'mixCamelot': camelot(mix_key),
                # The app colors each song from its identity, so the page matches the screenshots.
                'color': studio_theme.clip_color(clip.track_id), 'kick': round(kicks[index], 4),
                'start': position['start'] / SR, 'end': position['end'] / SR,
                'incoming': position['incoming'] / SR, 'blendBars': clip.blend_bars if index else 0,
                'sourceDuration': clip.cue_out - clip.cue_in, 'plain': placement[index],
                'wave': {'peak': quantize(source_envs[index][0], clip_scale),
                         'rms': quantize(source_envs[index][1], clip_scale)},
                'detail': quantize(detail_envs[index], detail_scale),
            })

    lessons = []
    for lesson in LESSONS:
        folder = ROOT / 'assets/lessons' / lesson['id']
        info = json.loads((folder / 'lesson.json').read_text())
        versions = []
        envs = []
        for variant in ('current', 'draft'):
            audio, _ = sf.read(folder / f'{variant}.wav', always_2d=True)
            envs.append(envelope(audio, int(round(len(audio) / SR * LESSON_RATE))))
            encode(folder / f'{variant}.wav', media / f'lesson-{lesson["id"]}-{"a" if variant == "current" else "b"}.mp3')
        scale = max(env[0].max() for env in envs)
        for variant, env in zip('ab', envs):
            versions.append({'id': variant, 'src': f'media/lesson-{lesson["id"]}-{variant}.mp3',
                             'label': lesson[variant], 'wave': quantize(env[0], scale)})
        lessons.append({'id': lesson['id'], 'title': lesson['title'].split(' · ', 1)[1], 'bpm': info['bpm'],
                        'duration': info['seconds'], 'listen': lesson['listen'], 'change': lesson['change'],
                        'versions': versions})

    result = {
        'demo': {
            'name': session.name, 'bpm': session.target_bpm, 'duration': duration,
            'waveRate': WAVE_RATE, 'detailRate': DETAIL_RATE,
            'lufs': report['lufs'], 'truePeak': report['true_peak_db'], 'gains': gains,
            'versions': [{'id': 'beatstitch', 'src': 'media/demo-beatstitch.mp3'},
                         {'id': 'without', 'src': 'media/demo-without.mp3'}],
            'clips': clips,
            'mix': {'beatstitch': {'peak': quantize(mix_env[0], mix_scale), 'rms': quantize(mix_env[1], mix_scale)},
                    'without': {'peak': quantize(plain_env[0], mix_scale), 'rms': quantize(plain_env[1], mix_scale)}},
        },
        'lessons': lessons,
    }
    # A classic script rather than JSON, so waveforms also draw when the page is opened from disk.
    (data / 'media.js').write_text('window.BEATSTITCH_MEDIA = ' + json.dumps(result, separators=(',', ':')) + ';\n')
    print('Loudness', loud, 'comparison gain', round(plain_gain, 2), 'dB')

    for font in ('Barlow-Regular', 'Barlow-Medium', 'Barlow-SemiBold', 'Barlow-Bold'):
        (WEB / 'fonts').mkdir(exist_ok=True)
        shutil.copy2(ROOT / 'assets/fonts' / f'{font}.ttf', WEB / 'fonts' / f'{font}.ttf')
    shutil.copy2(ROOT / 'assets/fonts/OFL.txt', WEB / 'fonts/OFL.txt')


if __name__ == '__main__':
    main()
