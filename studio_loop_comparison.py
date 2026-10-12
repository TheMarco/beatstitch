"""Actual current/draft mix audio for a reviewable drum extension."""
from pathlib import Path
import json
import os
import tempfile

import numpy as np
import soundfile as sf

import djmix
from studio_cache import digest
from studio_drum_loops import _wave
from studio_engine import layout
from studio_process import check_cancelled
from studio_render_cache import audio_recipe, prepare_listening
from studio_storage import managed_cache, require_space
from studio_timing import corrected_time


def comparison_regions(session, extension, context='passage'):
    """Align a passage at its start, or a blend at the outgoing song's end."""
    draft, index, plan = extension['draft'], extension['index'], extension['plan']
    versions = (session, draft)
    positions = [layout(s) for s in versions]
    bar = 240 / session.target_bpm * djmix.SR
    if context == 'blend':
        pair = plan['pair']
        if pair is None: raise djmix.MixError('There is no neighboring blend on this side of the song.')
        anchors = [p[pair - 1]['end'] for p in positions]
        lo = max(*(p[pair - 1]['start'] + p[pair - 1]['incoming'] - a for p, a in zip(positions, anchors)),
                 -max(p[pair]['incoming'] for p in positions) - round(4 * bar))
        hi = min(*( (p[pair + 1]['start'] if pair + 1 < len(p) else p[pair]['end']) - a
                    for p, a in zip(positions, anchors)), round(4 * bar))
        note = 'Aligned where the outgoing song ends. The incoming song may enter at a different point in each version.'
    elif context == 'passage':
        clip = session.clips[index]; track = session.tracks[clip.track_id]
        rate = track.bpm / session.target_bpm
        loop_start = plan['start_frame'] / djmix.SR
        origins = [corrected_time(track, clip, clip.cue_in), draft.clips[index].cue_in]
        anchors = [p[index]['start'] + round((loop_start - origin) * rate * djmix.SR)
                   for p, origin in zip(positions, origins)]
        lo = max(-round(2 * bar), -min(anchors))
        span = round((plan['end_frame'] - plan['start_frame'] + plan['added_frames']) * rate)
        hi = min(span + round(2 * bar), max(p[-1]['end'] - a for p, a in zip(positions, anchors)))
        note = ('Aligned at the original loop start. Later music moves in the extended version. '
                'If one mix ends first, its remaining comparison time is silence.')
    else:
        raise djmix.MixError('Choose the repeated passage or its neighboring blend.')
    if not djmix.SR <= hi - lo <= 180 * djmix.SR:
        raise djmix.MixError('The comparison needs 1–180 seconds. Try fewer added copies or a shorter loop.')
    windows = [(a + lo, a + hi) for a in anchors]
    regions = [(start, min(end, p[-1]['end'])) for (start, end), p in zip(windows, positions)]
    if any(end <= start for start, end in regions):
        raise djmix.MixError('There is not enough current audio to compare this passage.')
    track = session.tracks[session.clips[index].track_id]
    rate = track.bpm / session.target_bpm
    # Show repeat boundaries in the draft's real mix time, even in blend mode.
    pos = positions[1][index]
    joins = [(pos['start'] + round((plan['end_frame'] + n * (plan['end_frame'] - plan['start_frame'])
                                  - draft.clips[index].cue_in * djmix.SR) * rate) - windows[1][0]) / djmix.SR
             for n in range(plan['repeats'])]
    from studio_tempo_curve import session_curve
    curves = [session_curve(s) for s in versions]
    frames, padding = hi - lo, [0, 0]
    if any(not c.identity for c in curves):
        origins = [int(c.output_frame(a)) for c, a in zip(curves, anchors)]
        spans = [c.output_span(*w) for c, w in zip(curves, windows)]
        common_start = min(a - o for (a, _), o in zip(spans, origins))
        common_end = max(b - o for (_, b), o in zip(spans, origins))
        frames = common_end - common_start
        padding = [int(c.output_frame(r[0])) - o - common_start for c, r, o in zip(curves, regions, origins)]
        joins = [(int(curves[1].output_frame(round(t * djmix.SR) + windows[1][0])) - origins[1] - common_start) / djmix.SR for t in joins]
        note += ' With gradual tempo, both versions are aligned at that musical anchor; any extra comparison space is silence.'
    if frames > 180 * djmix.SR:
        raise djmix.MixError('At this tempo the comparison exceeds three minutes. Use a shorter loop or fewer copies.')
    return {'regions': regions, 'frames': frames, 'padding': padding, 'note': note,
            'join_times': [t for t in joins if 0 <= t <= frames / djmix.SR]}


@managed_cache
def prepare_comparison(session, extension, cache_dir, progress=lambda _: None, *, context='passage'):
    bounds = comparison_regions(session, extension, context)
    versions = (session, extension['draft']); regions = bounds['regions']
    recipes = [audio_recipe(s, r)['fingerprint'] for s, r in zip(versions, regions)]
    key = [1, 'loop-comparison', context, bounds['frames'], *recipes]
    folder = Path(cache_dir) / 'alternatives' / digest(key)
    report = {'folder': str(folder), 'key': key, **bounds, 'duration': bounds['frames'] / djmix.SR}
    check_cancelled()
    try:
        saved = json.loads((folder / 'report.json').read_text())
        infos = [sf.info(folder / f'{name}.wav') for name in ('current', 'draft')]
        if saved['key'] == key and all(i.frames == bounds['frames'] and i.channels == 2 and i.samplerate == djmix.SR for i in infos):
            os.utime(folder / 'report.json', None)
            progress('Reusing this current/extended comparison')
            return {**saved, **report}
    except (OSError, ValueError, KeyError, RuntimeError):
        pass
    folder.parent.mkdir(parents=True, exist_ok=True)
    require_space(folder.parent, bounds['frames'] * 16, 'prepare the extension comparison')
    with tempfile.TemporaryDirectory(prefix='.loop-comparison-', dir=folder.parent) as temporary:
        stage = Path(temporary)
        renders = [prepare_listening(s, Path(cache_dir).parent / 'renders', r, progress, cache_dir)
                   for s, r in zip(versions, regions)]
        target = min(-18, *(r['lufs'] - r['true_peak_db'] - 2 for r in renders))
        for name, render, region, prefix in zip(('current', 'draft'), renders, regions, bounds['padding']):
            check_cancelled()
            audio, rate = sf.read(render['path'], dtype='float32', always_2d=True)
            if rate != djmix.SR or audio.shape != (round(render['duration'] * djmix.SR), 2) or not np.isfinite(audio).all():
                raise djmix.MixError('The comparison audio is incomplete. Prepare it again.')
            gain = target - render['lufs']
            audio *= 10 ** (gain / 20)
            audio = np.pad(audio, ((prefix, bounds['frames'] - prefix - len(audio)), (0, 0)))
            report[name + '_wave'] = _wave(audio)
            report[name + '_gain_db'] = gain
            sf.write(stage / f'{name}.wav', audio, djmix.SR, subtype='PCM_24')
        if [audio_recipe(s, r)['fingerprint'] for s, r in zip(versions, regions)] != recipes:
            raise djmix.MixError('A source changed while preparing the comparison. Prepare it again.')
        check_cancelled()
        report['listening_lufs'] = target
        (stage / 'report.json').write_text(json.dumps(report, allow_nan=False))
        folder.mkdir(exist_ok=True)
        for path in sorted(stage.iterdir(), key=lambda p: p.name == 'report.json'):
            os.replace(path, folder / path.name)
    return report
