"""Isolated, local-only EDMFormer inference. Invoked by studio_song_sections."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch


def publish(path, value):
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')
    pending.replace(path)


def infer(source, work):
    from studio_song_sections import model_directory, MAX_DURATION
    import djmix
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / 'vendor' / 'sections'))
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    import torch
    from edm98.inference.pipeline import InferencePipeline
    from muq import MuQ
    from musicfm.model.musicfm_25hz import MusicFM25Hz
    from safetensors.torch import load_file
    from transformers import Wav2Vec2ConformerConfig
    from studio_section_attention import enable_efficient_attention, enable_bounded_frontend

    torch.set_num_threads(min(4, os.cpu_count() or 1))
    # A fixed seed also makes architecture initialization reproducible, although
    # every trained tensor is replaced by strictly loaded checkpoint values.
    torch.manual_seed(0)
    models = model_directory()
    device = 'mps' if torch.backends.mps.is_available() else 'cpu'
    duration = djmix.probe(source)['duration']
    if not 2 <= duration <= MAX_DURATION:
        raise ValueError('The current source is outside the 2-second to 30-minute analysis limit.')

    def progress(value=0, desc=''):
        if desc.startswith('Extracting MuQ embeddings'):
            desc = desc.replace('Extracting MuQ embeddings', 'Listening for musical patterns · pass 1 of 2')
        elif desc.startswith('Extracting MusicFM embeddings'):
            desc = desc.replace('Extracting MusicFM embeddings', 'Listening for musical patterns · pass 2 of 2')
        elif desc.startswith('Running EDMFormer'):
            desc = desc.replace('Running EDMFormer', 'Comparing likely section labels')
        else:
            desc = {'Loading audio': 'Reading the original song',
                    'Postprocessing segments': 'Placing suggested section boundaries',
                    'Done': 'Section suggestions ready'}.get(desc, desc)
        publish(work / 'progress.json', str(desc)[:2000])

    class LocalPipeline(InferencePipeline):
        def efficient(self, model, frontend):
            if enable_efficient_attention(model) != 12:
                raise RuntimeError('Unsupported section encoder architecture.')
            enable_bounded_frontend(frontend)
            return model.to(self.device).eval()

        def _create_muq_model(self):
            # No Hub fetch and no arbitrary checkpoint object deserialization.
            model = MuQ(json.loads((models / 'muq/config.json').read_text()))
            model.load_state_dict(load_file(str(models / 'muq/model.safetensors'), device='cpu'), strict=True)
            return self.efficient(model, model.model.conv)

        def _create_musicfm_model(self):
            config = Wav2Vec2ConformerConfig.from_json_file(models / 'musicfm-config.json')
            with patch.object(Wav2Vec2ConformerConfig, 'from_pretrained', return_value=config):
                model = MusicFM25Hz(is_flash=False, stat_path=models / 'musicfm-stats.json', model_path=None)
            state = torch.load(models / 'musicfm.pt', map_location='cpu', weights_only=True)['state_dict']
            if not all(key.startswith('model.') for key in state):
                raise RuntimeError('Unexpected MusicFM checkpoint keys.')
            model.load_state_dict({key[6:]: value for key, value in state.items()}, strict=True)
            del state
            return self.efficient(model, model.conv)

    progress(desc='Preparing original audio for section analysis')
    # Decode through the bundled FFmpeg so every supported source format works.
    # A hard duration cap also bounds a source whose metadata understates length.
    audio = work / 'source.wav'
    djmix.run([djmix.tool_path('ffmpeg'), '-nostdin', '-v', 'error', '-y', '-i', str(source),
               '-t', str(MAX_DURATION + 1), '-vn', '-ac', '1', '-ar', '24000', '-c:a', 'pcm_f32le', str(audio)])
    if djmix.probe(audio)['duration'] > MAX_DURATION:
        raise ValueError('Decoded source exceeds the 30-minute section-analysis limit.')
    progress(desc='Loading build/drop model · ' + ('Apple GPU' if device == 'mps' else 'CPU'))
    with patch('edm98.inference.pipeline.load_checkpoint',
               side_effect=lambda checkpoint_path, device=None: {'model': load_file(str(checkpoint_path), device='cpu')}):
        pipeline = LocalPipeline(checkpoint_path=models / 'edmformer.safetensors',
                                 config_path=models / 'edmformer.yaml',
                                 musicfm_stat_path=models / 'musicfm-stats.json',
                                 musicfm_model_path=models / 'musicfm.pt', device=device,
                                 low_memory=True, offline=True, hf_cache_dir=work / 'hf')
    def load_prepared(path, sr):
        import soundfile as sf
        samples, rate = sf.read(path, dtype='float32', always_2d=False)
        if rate != 24000 or sr != rate or samples.ndim != 1:
            raise ValueError('Expected the prepared 24 kHz mono source.')
        return samples, rate

    # FFmpeg already decoded and resampled this file. Reading its floats directly
    # avoids a second general-purpose decoder and Librosa's runtime JIT caches.
    with patch('edm98.inference.pipeline._load_audio_backend',
               return_value=SimpleNamespace(load=load_prepared)):
        sections = pipeline.predict_file(audio, progress_callback=progress)
    return {'sections': sections, 'device': device}


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 2: return 2
    source, work = map(Path, arguments)
    try:
        result = infer(source, work)
    except Exception as error:
        publish(work / 'result.json', {'error': f'{type(error).__name__}: {error}'[:1200]})
        return 1
    publish(work / 'result.json', result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
