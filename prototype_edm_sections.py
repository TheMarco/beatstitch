"""Offline EDMFormer trial; deliberately separate from the packaged application.

Run with outputs/edmformer-research/.venv/bin/python. The research folder holds
the pinned upstream sources, local model files and architecture configuration.
This produces listening candidates, not calibrated musical ground truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

MODEL_ID = {
    'edm98_revision': '2dd942f2f9e71ffd826346828eeaba1dd3ece56a',
    'musicfm_revision': 'b83ebedb401bcef639b26b05c0c8bee1dc2dfe71',
    'muq_revision': '28847ea50cd31ac4b8b6a7dacc051ad7d1c7606a',
    'edmformer_sha256': '1412e207645e9a71adc09777714dd251ce7805cada9bf19518d2e455a977e165',
    'musicfm_sha256': '218b483a0256ddef736267425fabb166fd97008983696bb9270def464b47bded',
    'muq_sha256': '273febab2be02872c37d2c37e48a9d6c52c1c9392f3eeeabd498efa281ccb7a6',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', type=Path)
    parser.add_argument('--research', type=Path, default=Path('outputs/edmformer-research'))
    parser.add_argument('--device', choices=('cpu', 'mps'), default='cpu')
    parser.add_argument('--attention', choices=('sdpa', 'original'), default='sdpa')
    parser.add_argument('--frontend', choices=('chunked', 'original'), default='chunked')
    args = parser.parse_args()
    root, source = args.research.resolve(), args.audio.resolve(strict=True)
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    sys.path.insert(0, str(root / 'edm98/src'))
    sys.path.insert(0, str(root))  # MusicFM's namespace package.

    import numpy as np
    import torch
    from edm98.inference.pipeline import InferencePipeline
    from muq import MuQ
    from musicfm.model.musicfm_25hz import MusicFM25Hz
    from safetensors.torch import load_file
    from transformers import Wav2Vec2ConformerConfig

    torch.set_num_threads(4)
    before = [str(source), source.stat().st_size, source.stat().st_mtime_ns]
    runtime = {'torch': torch.__version__, 'device': args.device, 'attention': args.attention, 'frontend': args.frontend}
    if args.device == 'mps' and args.attention == 'sdpa':
        runtime['query_chunk'] = 256
    identity = hashlib.sha256(json.dumps([1, before, MODEL_ID, runtime], sort_keys=True).encode()).hexdigest()
    result_dir = root / 'results' / identity
    result_dir.mkdir(parents=True, exist_ok=True)
    started, stages = time.monotonic(), []

    def progress(value, desc=''):
        elapsed = round(time.monotonic() - started, 2)
        stage = {'seconds': elapsed, 'stage': desc}
        if args.device == 'mps':
            stage['gpu_driver_bytes'] = torch.mps.driver_allocated_memory()
        try:
            import resource
            stage['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
        except ImportError:
            pass
        stages.append(stage)
        print(f'{elapsed:8.2f}s · {desc}', flush=True)

    class LocalPipeline(InferencePipeline):
        def efficient(self, model, label):
            if args.attention == 'sdpa':
                from studio_section_attention import enable_efficient_attention
                count = enable_efficient_attention(model)
                if count != 12:
                    raise RuntimeError(f'Unexpected {label} architecture: {count} rotary attention layers, expected 12.')
                progress(0, f'{label} · efficient attention in {count} layers')
            if args.frontend == 'chunked':
                from studio_section_attention import enable_bounded_frontend
                enable_bounded_frontend(model.model.conv if label == 'MuQ' else model.conv)
                progress(0, f'{label} · bounded convolution with full boundary context')
            return model

        def _create_muq_model(self):
            # Load verified local safetensors, with no implicit network access.
            model = MuQ.from_pretrained(str(root / 'muq-weights'), local_files_only=True).to(self.device).eval()
            return self.efficient(model, 'MuQ')

        def _create_musicfm_model(self):
            # Upstream hardcodes a remote architecture config. Substitute the
            # pinned local copy only while constructing this isolated model.
            config = Wav2Vec2ConformerConfig.from_json_file(root / 'musicfm-config.json')
            with patch.object(Wav2Vec2ConformerConfig, 'from_pretrained', return_value=config):
                model = MusicFM25Hz(is_flash=False, stat_path=self.musicfm_stat_path,
                                    model_path=self.musicfm_model_path)
            return self.efficient(model.to(self.device).eval(), 'MusicFM')

        def cached_features(self, kind, audio, start, extract):
            file = result_dir / f'{kind}-{start}.npz'
            if file.is_file():
                with np.load(file, allow_pickle=False) as data:
                    values = [torch.from_numpy(data[k]) for k in ('short', 'long')]
                progress(0, f'Reusing {kind} features at {start}s')
                return tuple(values)
            values = extract(audio, start)
            temporary = file.with_suffix('.pending')
            with temporary.open('wb') as handle:
                np.savez_compressed(handle, short=values[0].numpy(), long=values[1].numpy())
            temporary.replace(file)
            return values

        def _extract_muq_block(self, audio, start):
            return self.cached_features('muq', audio, start, super()._extract_muq_block)

        def _extract_musicfm_block(self, audio, start):
            return self.cached_features('musicfm', audio, start, super()._extract_musicfm_block)

    checkpoints = root / 'edm98/data/checkpoints'
    progress(0, 'Loading EDMFormer')
    # Upstream passes torch.device to safetensors, which expects a string.
    # Load on CPU; the constructor then moves the initialized model to MPS.
    with patch('edm98.inference.pipeline.load_checkpoint',
               side_effect=lambda checkpoint_path, device=None: {'model': load_file(str(checkpoint_path), device='cpu')}):
        pipeline = LocalPipeline(checkpoint_path=checkpoints / 'model.safetensors',
                                 config_path=root / 'edm98/configs/edmformer.yaml',
                                 musicfm_stat_path=checkpoints / 'msd_stats.json',
                                 musicfm_model_path=checkpoints / 'pretrained_msd.pt',
                                 device=args.device, low_memory=True, offline=True,
                                 hf_cache_dir=root / 'hf-cache')
    sections = pipeline.predict_file(source, progress_callback=progress)
    after = [str(source), source.stat().st_size, source.stat().st_mtime_ns]
    if after != before:
        raise RuntimeError('Source changed during the trial; discard these results.')
    elapsed = time.monotonic() - started
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    except ImportError:
        rss = None
    report = {'source': before, 'device': args.device, 'elapsed_seconds': elapsed,
              'peak_rss_bytes': rss, 'model_identity': MODEL_ID, 'runtime': runtime,
              'model': 'EDMFormer', 'sections': sections, 'stages': stages,
              'notice': 'Experimental candidates. Musical labels and boundaries need listening validation.'}
    result = result_dir / 'sections.json'
    result.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps(report, indent=2, allow_nan=False), flush=True)
    print(f'Saved {result}', flush=True)


if __name__ == '__main__':
    main()
