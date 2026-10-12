"""Provision the exact optional section pack from immutable upstream revisions."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT/'section-model-manifest.json').read_text(encoding='utf-8'))
EDM_REVISION = '2dd942f2f9e71ffd826346828eeaba1dd3ece56a'
MUQ_REVISION = '0562a57814f6f8bbd9fdea0a25921a2fce1a841a'
CONFIG_REVISION = '6b36ef01c6443c67ae7ed0822876d091ab50e4aa'
EDM_RAW = f'https://raw.githubusercontent.com/25ohms/EDM-98/{EDM_REVISION}/'
EDM_MEDIA = f'https://media.githubusercontent.com/media/25ohms/EDM-98/{EDM_REVISION}/'
MUQ = f'https://huggingface.co/OpenMuQ/MuQ-large-msd-iter/resolve/{MUQ_REVISION}/'
CHECKPOINT = {'size': 417979984,
              'sha256': '1412e207645e9a71adc09777714dd251ce7805cada9bf19518d2e455a977e165'}
SOURCES = {
    'edmformer.safetensors': EDM_MEDIA+'data/checkpoints/model.pt',
    'musicfm.pt': EDM_MEDIA+'data/checkpoints/pretrained_msd.pt',
    'musicfm-stats.json': EDM_RAW+'data/checkpoints/msd_stats.json',
    'edmformer.yaml': EDM_RAW+'configs/edmformer.yaml',
    'musicfm-config.json': ('https://huggingface.co/facebook/wav2vec2-conformer-rope-large-960h-ft/'
                           f'resolve/{CONFIG_REVISION}/config.json'),
    'muq/model.safetensors': MUQ+'model.safetensors',
    'muq/config.json': MUQ+'config.json',
}
CONFIG_GLOBALS = ('omegaconf.base.ContainerMetadata', 'omegaconf.dictconfig.DictConfig',
                  'omegaconf.nodes.AnyNode', 'omegaconf.listconfig.ListConfig',
                  'omegaconf.base.Metadata', 'typing.Any')


def sha256(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def verified(path, expected):
    return path.is_file() and path.stat().st_size == expected['size'] and sha256(path) == expected['sha256']


def download(url, path, expected):
    digest, count = hashlib.sha256(), 0
    with urllib.request.urlopen(url, timeout=60) as response, path.open('xb') as output:
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > expected['size']:
                raise ValueError('Section-model download exceeds the pinned size.')
            digest.update(chunk); output.write(chunk)
    if count != expected['size'] or digest.hexdigest() != expected['sha256']:
        raise ValueError('Section-model download differs from the pinned bytes.')


class IgnoredTrainingConfig:
    """Consume unused checkpoint metadata without importing its original classes."""
    def __setstate__(self, state):
        self.state = state


def convert_checkpoint(source, target):
    # Verify before importing the tensor loader, not only after conversion.
    if not verified(source, CHECKPOINT):
        raise ValueError('Unverified EDMFormer checkpoint; conversion refused.')
    import torch
    from safetensors.torch import save_file
    allowed = set(CONFIG_GLOBALS) | {'builtins.int', 'builtins.dict', 'builtins.list', 'collections.defaultdict'}
    if not set(torch.serialization.get_unsafe_globals_in_checkpoint(source)) <= allowed:
        raise ValueError('Unexpected checkpoint globals; conversion refused.')
    # The checkpoint includes optimizer/config metadata. Only model tensors are
    # retained. No original OmegaConf constructors or methods are executed.
    with torch.serialization.safe_globals(
            [(IgnoredTrainingConfig, name) for name in CONFIG_GLOBALS] + [int, dict, list, defaultdict]):
        checkpoint = torch.load(source, map_location='cpu', weights_only=True)
    state = checkpoint.get('model') if isinstance(checkpoint, dict) else None
    if (not isinstance(state, dict) or len(state) != 62
            or not all(isinstance(k, str) and isinstance(v, torch.Tensor) for k, v in state.items())):
        raise ValueError('Unexpected EDMFormer inference tensors.')
    save_file({key: value.detach().cpu().contiguous().clone() for key, value in state.items()}, str(target))
    if not verified(target, MANIFEST['edmformer.safetensors']):
        raise ValueError('Converted inference weights differ from the shipped model.')


def install(destination, manifest=MANIFEST, sources=SOURCES, progress=print):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    result = []
    for name, expected in manifest.items():
        target = destination/name
        if verified(target, expected):
            progress('Reusing verified ' + name)
            result.append({'file': name, 'state': 'reused', 'sha256': expected['sha256']})
            continue
        converted = name == 'edmformer.safetensors'
        incoming = CHECKPOINT if converted else expected
        needed = incoming['size'] + (expected['size'] if converted else 0) + 1024**3
        if shutil.disk_usage(destination).free < needed:
            raise OSError('Not enough free space to stage this model while keeping 1 GiB available: ' + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Keep earlier verified files on a later failure; the next run reuses
        # them. A damaged existing target is replaced only after verification.
        with tempfile.TemporaryDirectory(prefix='.section-download-', dir=target.parent) as temporary:
            work = Path(temporary); downloaded = work/'download'
            progress('Downloading verified section-model asset: ' + name)
            download(sources[name], downloaded, incoming)
            if converted:
                prepared = work/'model.safetensors'
                progress('Extracting verified inference tensors')
                convert_checkpoint(downloaded, prepared)
            else:
                prepared = downloaded
            prepared.replace(target)
        result.append({'file': name, 'state': 'installed', 'sha256': expected['sha256']})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=ROOT/'assets/sections')
    args = parser.parse_args()
    result = install(args.destination)
    print(json.dumps({'status': 'verified', 'files': result}, indent=2))


if __name__ == '__main__':
    main()
