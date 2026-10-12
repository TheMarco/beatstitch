"""Actual processed mix comparison around a separated overlap."""
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf

import djmix
from studio_cache import digest
from studio_drum_loops import _wave
from studio_render_cache import audio_recipe, prepare_listening
from studio_storage import managed_cache, require_space
from studio_transition_audio import pair_range


@managed_cache
def prepare_comparison(session, result, cache_dir, progress=lambda _: None):
    versions = (session, result['draft'])
    region = pair_range(session, result['plan']['pair'])
    from studio_tempo_curve import render_span
    start, end = render_span(session, region)
    frames = end - start
    recipes = [audio_recipe(s, region)['fingerprint'] for s in versions]
    key = [1, 'separation-comparison', *recipes]
    folder = Path(cache_dir) / 'alternatives' / digest(key)
    try:
        report = json.loads((folder / 'report.json').read_text())
        if report['key'] == key and all((sf.info(folder / (n + '.wav')).frames,
                                        sf.info(folder / (n + '.wav')).samplerate,
                                        sf.info(folder / (n + '.wav')).channels) == (frames, djmix.SR, 2)
                                       for n in ('current', 'draft')):
            os.utime(folder / 'report.json', None); progress('Reusing this blend comparison'); return report
    except (OSError, ValueError, KeyError, RuntimeError): pass
    folder.parent.mkdir(parents=True, exist_ok=True)
    require_space(folder.parent, frames * 16, 'compare the separated blend')
    with tempfile.TemporaryDirectory(prefix='.separation-ab-', dir=folder.parent) as temp:
        stage = Path(temp)
        renders = [prepare_listening(s, Path(cache_dir).parent / 'renders', region, progress, cache_dir) for s in versions]
        target = min(-18, *(r['lufs'] - r['true_peak_db'] - 2 for r in renders))
        report = dict(key=key, folder=str(folder), frames=frames, duration=frames / djmix.SR, join_times=[])
        for name, render in zip(('current', 'draft'), renders):
            audio, rate = sf.read(render['path'], dtype='float32', always_2d=True)
            if rate != djmix.SR or audio.shape != (frames, 2) or not np.isfinite(audio).all():
                raise djmix.MixError('The blend comparison is incomplete. Prepare it again.')
            gain = target - render['lufs']; audio *= 10 ** (gain / 20)
            report[name + '_gain_db'] = gain; report[name + '_wave'] = _wave(audio)
            sf.write(stage / (name + '.wav'), audio, rate, subtype='PCM_24')
        if [audio_recipe(s, region)['fingerprint'] for s in versions] != recipes:
            raise djmix.MixError('A source changed during comparison. Prepare it again.')
        (stage / 'report.json').write_text(json.dumps(report, allow_nan=False))
        folder.mkdir(exist_ok=True)
        for path in sorted(stage.iterdir(), key=lambda p: p.name == 'report.json'): path.replace(folder / path.name)
    return report
