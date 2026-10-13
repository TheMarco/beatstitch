"""Audio-content recipes and persistent prepared mixes, independent of UI revisions."""
from __future__ import annotations

import json
import copy
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


def audio_recipe(session, region=None, *, include_mastering=True):
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
    # Project loading accepts integer JSON tempos as floats. That spelling must
    # not invalidate identical prepared audio after saving and reopening.
    parts = [RENDER_CACHE_VERSION, RENDER_VERSION, engine_signature(), float(session.target_bpm), end - start, ingredients]
    from studio_finishing import recipe as finishing_recipe
    finishing = finishing_recipe(session, positions, start, end)
    if finishing: parts.append(finishing)
    if tempo_sources: parts.append(['set-tempo-sources', tempo_sources, curve.segment_token(start / djmix.SR, end / djmix.SR)])
    if include_mastering and session.mastering_enabled:
        from studio_mastering import VERSION
        # Global loudness and compressor history come from the whole mix. An
        # edited later song can therefore change the finishing of an earlier
        # excerpt, while its transformed source cache stays valid.
        full = audio_recipe(session, include_mastering=False)
        parts.append(['automatic-mastering', VERSION, full['fingerprint']])
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
            # A read-only cache can still supply valid prepared audio. Failure
            # to update its access time must not force needless processing.
            try: os.utime(folder / 'report.json', None)
            except OSError: pass
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
    if session.mastering_enabled:
        result = prepare_finished(session, root, destination, region, progress, audio_cache)
    else:
        result = render_session(session, destination, region=region, progress=progress, cache_dir=audio_cache)
    if audio_recipe(session, region)['fingerprint'] != recipe['fingerprint']:
        destination.unlink(missing_ok=True)
        raise djmix.MixError('A source changed while preparing audio. Please try again.')
    progress('Preparing playback levels')
    result['levels'] = level_envelope(destination)
    result.update(recipe)
    cache.put(recipe, result)
    return result


def prepare_finished(session, root, destination, region, progress, audio_cache):
    """Finish once; regional auditions are excerpts of that exact finished mix."""
    from studio_mastering import master_file
    if region is None:
        dry = copy.deepcopy(session)
        dry.mastering_enabled = False
        base = prepare_listening(dry, root, progress=progress, audio_cache=audio_cache)
        progress('Automatic mastering: finishing the prepared mix; song processing is unchanged')
        finished = master_file(Path(base['path']), destination, progress=progress)
        return {**base, **finished, 'cached_render': False, 'mastering_applied': True,
                'processed_tracks': 0 if base.get('cached_render') else base['processed_tracks'],
                'reused_tracks': len(session.clips) if base.get('cached_render') else base['reused_tracks']}
    full = prepare_listening(session, root, progress=progress, audio_cache=audio_cache)
    from studio_tempo_curve import session_curve
    start, end = session_curve(session).output_span(*region)
    progress('Preparing an excerpt of the finished mix; no new mastering pass')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(uuid.uuid4().hex + '.wav')
    try:
        with sf.SoundFile(full['path']) as source, sf.SoundFile(temporary, mode='w',
                samplerate=djmix.SR, channels=2, subtype='PCM_24') as target:
            source.seek(start)
            offset = 0
            while offset < end - start:
                progress('Copying the prepared mastered excerpt')
                chunk = source.read(min(65536, end - start - offset), dtype='float64', always_2d=True)
                if not len(chunk):
                    raise djmix.MixError('The prepared finished mix ended unexpectedly.')
                n = np.arange(offset, offset + len(chunk))
                edge = np.minimum(np.clip(n / (.005 * djmix.SR), 0, 1),
                                  np.clip((end - start - 1 - n) / (.005 * djmix.SR), 0, 1))
                target.write(chunk * edge[:, None])
                offset += len(chunk)
        check = sf.info(temporary)
        if check.frames != end - start:
            raise djmix.MixError('The finished excerpt has the wrong duration.')
        progress('Checking the finished excerpt peak headroom')
        levels = djmix.meter(temporary, require_loudness=False)
        peak = levels['true_peak_db']
        safety = min(0.0, -1.1 - peak)
        if np.isfinite(peak) and safety < 0:
            protected = temporary.with_name(uuid.uuid4().hex + '.wav')
            try:
                djmix.ffmpeg('-i', str(temporary), '-af', f'volume={safety:.9f}dB',
                             '-c:a', 'pcm_s24le', str(protected))
                os.replace(protected, temporary)
            finally:
                protected.unlink(missing_ok=True)
            levels = djmix.meter(temporary, require_loudness=False)
            peak = levels['true_peak_db']
        if peak > -1.0:
            raise djmix.MixError('The finished excerpt failed its peak headroom check.')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    # A boundary fade changes the signal's spectrum; check actual intersample
    # peaks rather than assuming that sample attenuation bounds true peak.
    return {**full, 'path': str(destination), 'duration': (end - start) / djmix.SR,
            'lufs': levels['lufs'] if np.isfinite(levels['lufs']) else None,
            'true_peak_db': peak if np.isfinite(peak) else full['true_peak_db'],
            'meter_scope': 'excerpt' if np.isfinite(peak) else 'full-mix upper bound; silent excerpt',
            'excerpt_attenuation_db': safety, 'cached_render': False,
            'processed_tracks': 0 if full.get('cached_render') else full['processed_tracks'],
            'reused_tracks': len(session.clips) if full.get('cached_render') else full['reused_tracks']}
