"""Two-song auditions: production envelopes, shared gain, one playback clock."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf

import djmix
from studio_ab_audio import ABMixer, ABTransport, RATE
from studio_cache import AudioCache, digest, source_signature
from studio_engine import layout, render_session
from studio_timing import mapping, RENDER_VERSION
from studio_storage import managed_cache


def pair_range(session, index):
    """Four bars of context where possible, excluding other transitions."""
    positions = layout(session)
    if not 0 < index < len(positions):
        raise djmix.MixError("Choose a transition between two songs.")
    outgoing, incoming = positions[index - 1:index + 1]
    context = round(4 * 240 / session.target_bpm * djmix.SR)
    left = outgoing['start'] + outgoing['incoming']
    right = positions[index + 1]['start'] if index + 1 < len(positions) else incoming['end']
    return (max(left, incoming['start'] - context),
            min(right, outgoing['end'] + context))


@managed_cache
def prepare_blend(session, index, cache_dir, progress=lambda message: None):
    positions = layout(session)
    start, end = pair_range(session, index)
    cache = AudioCache(cache_dir)
    ingredients = []
    for clip in session.clips[index - 1:index + 1]:
        track = session.tracks[clip.track_id]
        points = mapping(track, clip)
        signature = source_signature(track.path)
        if clip.stabilize and track.timing.get('source') != signature:
            raise djmix.MixError(f"{track.name} changed on disk. Analyze its timing again.")
        ingredients.append([signature, track.bpm, clip.cue_in, clip.cue_out, clip.shift,
                            points.tolist() if points is not None else None])
    # Absolute arrangement position, names, key labels and other songs do not
    # change this pair's audio. Context bounds do, when a neighbor is very close.
    incoming = positions[index]
    key = [1, RENDER_VERSION, cache.engine, session.target_bpm, ingredients,
           incoming['incoming'], session.clips[index].bass_swap,
           start - incoming['start'], end - incoming['start']]
    from studio_fades import token as fade_token
    if fade_token(session.clips[index]): key.append(fade_token(session.clips[index]))
    if session.clips[index].balance_levels:
        from studio_level_balance import VERSION
        key.append(['level-balance', VERSION])
    from studio_transition_effects import token as effect_token
    effect = effect_token(session.clips[index])
    if effect: key.append(effect)
    from studio_finishing import recipe as finishing_recipe
    finishing = finishing_recipe(session, positions, start, end)
    if finishing: key.append(finishing)
    from studio_tempo_curve import render_span, tempo_token
    tempo = tempo_token(session)
    if tempo:
        from studio_render_cache import audio_recipe
        key.append(['set-tempo-pair', audio_recipe(session, (start, end))['fingerprint']])
    out_start, out_end = render_span(session, (start, end))
    frames = out_end - out_start
    folder = cache.root / 'transitions' / digest(key)
    report = {'folder': str(folder), 'start': start / RATE, 'end': end / RATE,
              'duration': frames / RATE, 'cached': False}
    manifest = folder / 'report.json'
    try:
        saved = json.loads(manifest.read_text())
        infos = [sf.info(folder / f'{name}.wav') for name in ('outgoing', 'incoming', 'blend')]
        if saved['key'] == key and all(i.frames == frames and i.samplerate == RATE and i.channels == 2 for i in infos):
            os.utime(manifest, None)
            progress('Reusing this prepared blend')
            # Pair caches survive moving the pair to a different arrangement index.
            balance = [{**item, 'index': index} for item in saved.get('level_balance', [])]
            return {**report, 'cached': True, 'level_balance': balance}
    except (OSError, ValueError, KeyError, RuntimeError):
        pass
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.blend-', dir=folder.parent) as temporary:
        stage = Path(temporary)
        rendered = render_session(session, stage / 'blend.wav', region=(start, end), cache_dir=cache_dir, progress=progress,
                                  stems={index - 1: stage / 'outgoing.wav', index: stage / 'incoming.wav'})
        report['level_balance'] = rendered['level_balance']
        (stage / 'report.json').write_text(json.dumps({'key': key, 'level_balance': report['level_balance']}, allow_nan=False))
        progress('Blend ready')
        folder.mkdir(exist_ok=True)
        for path in sorted(stage.iterdir(), key=lambda p: p.name == 'report.json'):
            os.replace(path, folder / path.name)
    cache.prune()
    return report


class BlendMixer:
    """Absolute sample cursor permits loops without restarting the audio sink."""
    WEIGHTS = {'outgoing': (1., 0.), 'blend': (1., 1.), 'incoming': (0., 1.)}

    def __init__(self, outgoing, incoming):
        if (outgoing.shape != incoming.shape or outgoing.ndim != 2 or outgoing.shape[1] != 2
                or not len(outgoing) or not np.isfinite(outgoing).all() or not np.isfinite(incoming).all()):
            raise ValueError('Both songs need matching, finite stereo audio.')
        self.outgoing, self.incoming = outgoing, incoming
        self.length = len(outgoing)
        self.frame = 0
        self.gains = self.target = (1., 1.)
        self.loop = False
        self.stop_at = self.length

    @property
    def frames(self):
        return math.inf if self.loop else self.stop_at

    def select(self, variant):
        if variant not in self.WEIGHTS:
            raise ValueError('Choose Outgoing, Blend or Incoming.')
        self.target = self.WEIGHTS[variant]

    def set_loop(self, enabled):
        self.loop = bool(enabled)
        if not self.loop:
            # Already queued frames must play. Finish at their next boundary.
            self.stop_at = max(1, math.ceil(self.frame / self.length)) * self.length

    def seek(self, frame):
        self.frame = max(0, int(frame))
        if self.frame == 0:
            self.stop_at = self.length
        if not self.loop:
            self.frame = min(self.frame, self.stop_at)
        self.gains = self.target

    def read(self, frames):
        count = int(min(frames, self.frames - self.frame))
        if count <= 0:
            return b''
        indices = np.arange(self.frame, self.frame + count) % self.length
        out = ABMixer.ramp(self.gains[0], self.target[0], count)
        inc = ABMixer.ramp(self.gains[1], self.target[1], count)
        audio = self.outgoing[indices] * out[:, None] + self.incoming[indices] * inc[:, None]
        self.gains = float(out[-1]), float(inc[-1])
        self.frame += count
        return np.asarray(audio, dtype='<f4').tobytes()


class BlendTransport(ABTransport):
    FILES = ('outgoing', 'incoming')
    MIXER = BlendMixer
    DEFAULT = 'blend'

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.variant, self.loop = self.DEFAULT, False

    def load(self, folder):
        self._set_playing(False)
        self._reset()
        self.mixer = None
        decoded = []
        for name in self.FILES:
            audio, rate = sf.read(Path(folder) / f'{name}.wav', dtype='float32', always_2d=True)
            if rate != RATE:
                raise ValueError('The blend needs 48 kHz audio.')
            decoded.append(audio)
        self.mixer = self.MIXER(*decoded)
        self.mixer.select(self.variant)
        self.mixer.set_loop(self.loop)
        self.mixer.seek(0)
        self.origin, self.last_reported = 0, -100
        self.positionChanged.emit(0)

    def position(self):
        if self.mixer is None:
            return 0
        frame = self.position_frame()
        local = (self.mixer.length if not self.mixer.loop and frame >= self.mixer.stop_at
                 else frame % self.mixer.length)
        return round(local * 1000 / RATE)

    def seek(self, milliseconds):
        if self.mixer is not None:
            # The exact loop boundary is also the next loop's zero. An explicit
            # End gesture should land on the last sample, not jump to the start.
            last_frame = self.mixer.length - (1 if self.mixer.loop else 0)
            milliseconds = max(0, min(milliseconds, last_frame * 1000 / RATE))
            self.mixer.stop_at = self.mixer.length
        super().seek(milliseconds)

    def set_loop(self, enabled):
        self.loop = bool(enabled)
        if self.mixer is not None:
            self.mixer.set_loop(enabled)
