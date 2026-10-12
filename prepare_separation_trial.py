"""Prepare a bounded, offline Demucs listening trial; never alter the source.

Run in the optional separation environment. The native player needs only the
prepared excerpts, not PyTorch, Demucs, network access, or the model weights.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import tempfile
import time

import numpy as np
import soundfile as sf

import djmix
from studio_cache import source_signature
from studio_process import check_cancelled
from studio_storage import require_space

from studio_separation_model import (MODEL_REPOSITORY, MODEL_REVISION, MODEL_FILE, MODEL_SHA256,
                                     MODEL_BYTES, VERSION, RATE, STEMS, VARIANTS, sha256, normalization, load_model)


def listening_versions(original, stems):
    if (set(stems) != set(STEMS) or original.ndim != 2 or original.shape[1] != 2
            or not len(original) or not np.isfinite(original).all()
            or any(a.shape != original.shape or not np.isfinite(a).all() for a in stems.values())):
        raise ValueError('Separation needs matching, finite stereo audio for every stem.')
    # Use the model's accompaniment, not subtraction that redistributes the
    # model's reconstruction error. Recombined exposes that error for audition.
    instrumental = stems['drums'] + stems['bass'] + stems['other']
    versions = dict(original=original, instrumental=instrumental, drums=stems['drums'],
                    vocals=stems['vocals'], recombined=instrumental + stems['vocals'])
    peak = max(float(np.max(np.abs(a))) for a in versions.values())
    gain = min(1., 10 ** (-2 / 20) / max(peak, 1e-12))
    return {name: np.asarray(audio * gain, dtype=np.float32) for name, audio in versions.items()}, gain


def waveform(audio, bins=720):
    envelope = np.max(np.abs(audio), axis=1)
    return [float(np.max(a)) for a in np.array_split(envelope, min(bins, len(envelope)))]



def prepare(source, start, duration, models, cache, progress=print):
    source, models, cache = Path(source).resolve(), Path(models).resolve(), Path(cache).resolve()
    if not all(math.isfinite(v) for v in (start, duration)) or start < 0 or not 8 <= duration <= 60:
        raise ValueError('Choose an 8–60 second passage starting at or after zero.')
    before = source_signature(source)
    identity = sha256(source)
    total = djmix.probe(source)['duration']
    if start + duration > total + .01:
        raise ValueError('The selected passage extends beyond the source song.')
    import importlib.metadata
    if importlib.metadata.version('demucs') != '4.1.0':
        raise ValueError('This trial requires Demucs 4.1.0 in its optional environment.')
    recipe = dict(version=VERSION, source=identity, start=start, duration=duration,
                  model=MODEL_SHA256, demucs='4.1.0', context=8., shifts=1, overlap=.25,
                  seed=0, device='cpu')
    key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
    folder = cache / key
    frames = round(duration * RATE)
    try:
        report = json.loads((folder / 'report.json').read_text())
        if report['recipe'] == recipe:
            for name in VARIANTS:
                info = sf.info(folder / f'{name}.wav')
                if (info.frames, info.samplerate, info.channels) != (frames, RATE, 2):
                    raise ValueError('Incomplete cached excerpt.')
                if sha256(folder / f'{name}.wav') != report['files'][name]:
                    raise ValueError('Changed cached excerpt.')
            if source_signature(source) != before:
                raise ValueError('The source changed during preparation.')
            progress('Reusing the prepared separation trial')
            return {**report, 'folder': str(folder), 'cached': True}
    except (OSError, ValueError, KeyError, RuntimeError):
        pass
    cache.mkdir(parents=True, exist_ok=True)
    require_space(cache, round((duration + 16) * RATE * 8 * 12), 'prepare separated excerpts')
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='.separation-', dir=cache) as temporary:
        stage = Path(temporary)
        context_start = max(0., start - 8.)
        context_end = min(total, start + duration + 8.)
        progress('Reading the passage with surrounding musical context')
        djmix.run([djmix.tool_path('ffmpeg'), '-nostdin', '-v', 'error', '-y', '-i', str(source),
                   '-ss', str(context_start), '-t', str(context_end - context_start), '-vn',
                   '-ac', '2', '-ar', '44100', '-c:a', 'pcm_f32le', str(stage / 'input.wav')])
        audio, rate = sf.read(stage / 'input.wav', dtype='float32', always_2d=True)
        if rate != 44100 or audio.shape[1] != 2 or not len(audio) or not np.isfinite(audio).all():
            raise ValueError('Could not decode a finite stereo passage.')
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        import torch
        from demucs.apply import apply_model
        from scipy.signal import resample_poly
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        torch.manual_seed(0); random.seed(0)
        progress('Loading the local separation model')
        model = load_model(models / MODEL_FILE)
        mean, scale = normalization(audio)
        tensor = torch.from_numpy(((audio - mean) / scale).T.copy())[None]

        def update(state):
            check_cancelled()
            if state.get('state') == 'start':
                progress(f"Separating musical parts · {state.get('segment_offset', 0) / 44100:.0f} seconds into the excerpt")

        progress('Separating vocals, drums, bass and accompaniment on CPU')
        with torch.inference_mode():
            separated = apply_model(model, tensor, device='cpu', shifts=1, split=True,
                                    overlap=.25, num_workers=0, callback=update)
        estimates = separated[0].cpu().numpy() * scale + mean
        if estimates.shape != (4, 2, len(audio)) or not np.isfinite(estimates).all():
            raise ValueError('The separator returned invalid or misaligned audio.')
        left = round((start - context_start) * RATE)

        def excerpt(a):
            converted = resample_poly(a, 160, 147, axis=0)
            result = np.asarray(converted[left:left + frames], dtype=np.float32)
            if result.shape != (frames, 2):
                raise ValueError('The decoded source was shorter than the requested passage.')
            return result

        original = excerpt(audio)
        stems = {name: excerpt(estimates[i].T) for i, name in enumerate(STEMS)}
        versions, gain = listening_versions(original, stems)
        residual = versions['recombined'] - versions['original']
        residual_ratio = float(np.linalg.norm(residual) / max(np.linalg.norm(versions['original']), 1e-12))
        progress('Preparing synchronized listening excerpts')
        for name, samples in versions.items():
            check_cancelled()
            sf.write(stage / f'{name}.wav', samples, RATE, subtype='FLOAT')
        for name, samples in stems.items():
            # Raw stems retain model amplitude for later experiments.
            sf.write(stage / f'stem-{name}.wav', samples, RATE, subtype='FLOAT')
        report = dict(recipe=recipe, folder=str(folder), source=str(source), source_signature=before,
                      title=source.stem, start=start, duration=frames / RATE, frames=frames,
                      listening_gain_db=20 * math.log10(gain),
                      reconstruction_error_db=20 * math.log10(max(residual_ratio, 1e-12)),
                      elapsed_seconds=time.monotonic() - started,
                      waveforms={name: waveform(samples) for name, samples in versions.items()},
                      peaks={name: float(np.max(np.abs(samples))) for name, samples in versions.items()},
                      files={name: sha256(stage / f'{name}.wav') for name in VARIANTS}, cached=False)
        if source_signature(source) != before or sha256(source) != identity:
            raise ValueError('The source changed during separation. No result was published.')
        check_cancelled()
        (stage / 'report.json').write_text(json.dumps(report, allow_nan=False), encoding='utf-8')
        folder.mkdir(exist_ok=True)
        # A manifest is published last, so interrupted work cannot look complete.
        for path in sorted(stage.iterdir(), key=lambda p: p.name == 'report.json'):
            if path.name != 'input.wav': path.replace(folder / path.name)
    progress(f'Separation ready · {report["elapsed_seconds"]:.1f} seconds processing')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--start', type=float, required=True)
    parser.add_argument('--duration', type=float, default=30)
    parser.add_argument('--models', type=Path, default=Path('outputs/stem-separation-trial/models'))
    parser.add_argument('--cache', type=Path, default=Path('outputs/stem-separation-trial/audio'))
    parser.add_argument('--report', type=Path, default=Path('outputs/stem-separation-trial/example-report.json'))
    arguments = parser.parse_args()
    report = prepare(arguments.source, arguments.start, arguments.duration, arguments.models, arguments.cache,
                     progress=lambda text: print(text, flush=True))
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    pending = arguments.report.with_suffix('.pending')
    pending.write_text(json.dumps(report, allow_nan=False), encoding='utf-8')
    pending.replace(arguments.report)


if __name__ == '__main__': main()
