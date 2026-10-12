"""Audio-content recipes and persistent prepared mixes, independent of UI revisions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import numpy as np
import soundfile as sf

import djmix
from studio_cache import digest, engine_signature, source_signature
from studio_engine import layout, preview_range
from studio_timing import mapping, RENDER_VERSION
from studio_storage import managed_cache

RENDER_CACHE_VERSION = 1


def audio_recipe(session, region=None):
    positions = layout(session)
    if not positions:
        raise djmix.MixError('Add songs before preparing audio.')
    start, end = region or (0, positions[-1]['end'])
    if not 0 <= start < end <= positions[-1]['end']:
        raise djmix.MixError('Invalid preview range.')
    from studio_tempo_curve import session_curve
    from studio_tempo_processing import cache_token as source_tempo_token
    curve = session_curve(session)
    ingredients, tempo_sources = [], []
    for index, (clip, pos) in enumerate(zip(session.clips, positions)):
        left, right = max(start, pos['start']), min(end, pos['end'])
        if left >= right:
            continue
        track = session.tracks[clip.track_id]
        if not curve.identity:
            tempo_sources.append(source_tempo_token(track, clip, curve, pos['start'] / djmix.SR))
        points = mapping(track, clip)
        identity = source_signature(track.path)
        if clip.stabilize and track.timing.get('source') != identity:
            raise djmix.MixError(f'{track.name} changed. Analyze its timing again.')
        incoming = ([pos['start'] - start, pos['incoming'], clip.bass_swap]
                    if index and left < pos['start'] + pos['incoming'] else None)
        if incoming is not None and clip.balance_levels:
            from studio_level_balance import VERSION
            incoming.append(['level-balance', VERSION])
        outgoing = None
        if index + 1 < len(positions):
            nxt = positions[index + 1]
            if right > nxt['start']:
                outgoing = [nxt['start'] - start, nxt['incoming'], session.clips[index + 1].bass_swap]
                from studio_transition_effects import token as effect_token
                effect = effect_token(session.clips[index + 1])
                if effect: outgoing.append(effect)
                if session.clips[index + 1].balance_levels:
                    from studio_level_balance import VERSION
                    outgoing.append(['level-balance', VERSION])
        ingredients.append([identity, track.duration, track.bpm, clip.cue_in, clip.shift,
                            None if points is None else [points.tolist(), clip.cue_out],
                            pos['start'] - start, left - start, right - start, incoming, outgoing])
    parts = [RENDER_CACHE_VERSION, RENDER_VERSION, engine_signature(), session.target_bpm, end - start, ingredients]
    from studio_finishing import recipe as finishing_recipe
    finishing = finishing_recipe(session, positions, start, end)
    if finishing: parts.append(finishing)
    if tempo_sources: parts.append(['set-tempo-sources', tempo_sources, curve.segment_token(start / djmix.SR, end / djmix.SR)])
    output_start, output_end = curve.output_span(start, end)
    key = digest(parts)
    return {'fingerprint': key, 'start': start / djmix.SR, 'reference_end': end / djmix.SR,
            'duration': (output_end - output_start) / djmix.SR}


def listening_recipe(session, kind, transition=None):
    region = preview_range(session, transition) if kind == 'transition' else None
    return audio_recipe(session, region)


class RenderCache:
    def __init__(self, root):
        self.root = Path(root)

    def destination(self, fingerprint):
        return self.root / fingerprint / 'audio.wav'

    def get(self, recipe):
        folder = self.root / recipe['fingerprint']
        try:
            result = json.loads((folder / 'report.json').read_text())
            path = folder / 'audio.wav'
            if result['fingerprint'] != recipe['fingerprint'] or result['bytes'] != path.stat().st_size:
                return None
            info = sf.info(path)
            if info.samplerate != djmix.SR or info.channels != 2 or abs(info.frames - recipe['duration'] * djmix.SR) > 1:
                return None
            try:
                levels = np.load(folder / 'levels.npy', allow_pickle=False)
                if levels.ndim != 2 or levels.shape[1] != 2 or not np.isfinite(levels).all():
                    levels = None
            except (OSError, ValueError):
                levels = None
            os.utime(folder / 'report.json', None)
            return {**result, **recipe, 'path': str(path), 'levels': levels, 'cached_render': True}
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            return None

    def put(self, recipe, result):
        folder = self.root / recipe['fingerprint']
        path = folder / 'audio.wav'
        if Path(result['path']).resolve() != path.resolve():
            raise ValueError('Prepared audio must be rendered to its cache destination.')
        saved = {k: v for k, v in result.items() if k != 'levels'}
        saved.update(recipe, bytes=path.stat().st_size)
        levels = result.get('levels')
        if levels is not None:
            temporary = folder / (uuid.uuid4().hex + '.npy')
            try:
                np.save(temporary, levels, allow_pickle=False)
                os.replace(temporary, folder / 'levels.npy')
            finally:
                temporary.unlink(missing_ok=True)
        temporary = folder / (uuid.uuid4().hex + '.json')
        try:
            temporary.write_text(json.dumps(saved, allow_nan=False))
            os.replace(temporary, folder / 'report.json')
        finally:
            temporary.unlink(missing_ok=True)


@managed_cache
def prepare_listening(session, root, region=None, progress=lambda message: None, audio_cache=None):
    from studio_engine import render_session
    from studio_waveform import level_envelope
    recipe = audio_recipe(session, region)
    cache = RenderCache(root)
    cached = cache.get(recipe)
    if cached:
        progress('Reusing the prepared audio')
        from studio_tempo_curve import session_curve
        curve = session_curve(session)
        origin = int(curve.output_frame(region[0] if region else 0))
        indices = {int(curve.output_frame(p['start'])) - origin: i for i, p in enumerate(layout(session))}
        cached['level_balance'] = [{**item, 'index': indices[item['relative_start']]}
                                   for item in cached.get('level_balance', []) if item.get('relative_start') in indices]
        return cached
    destination = cache.destination(recipe['fingerprint'])
    result = render_session(session, destination, region=region, progress=progress, cache_dir=audio_cache)
    if audio_recipe(session, region)['fingerprint'] != recipe['fingerprint']:
        destination.unlink(missing_ok=True)
        raise djmix.MixError('A source changed while preparing audio. Please try again.')
    progress('Preparing playback levels')
    result['levels'] = level_envelope(destination)
    result.update(recipe)
    cache.put(recipe, result)
    return result
