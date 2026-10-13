"""Spleeter's licensed four-stem U-Nets on the existing PyTorch runtime.

Adapted from Deezer Spleeter 2.4.2 (MIT); retain vendor/spleeter/LICENSE.
Weights and architecture are unchanged. TensorFlow SAME padding, raw encoder
skip connections, decoder activation order and inference batch normalization
are preserved. No TensorFlow dependency is required by this module.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

STEMS = ('vocals', 'drums', 'bass', 'other')
WIDTHS = (16, 32, 64, 128, 256, 512)


class UNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder = nn.ModuleList(nn.Conv2d(a, b, 5, stride=2)
                                     for a, b in zip((2, *WIDTHS[:-1]), WIDTHS))
        self.encoder_bn = nn.ModuleList(nn.BatchNorm2d(c, eps=.001) for c in WIDTHS)
        self.decoder = nn.ModuleList(nn.ConvTranspose2d(a, b, 5, stride=2)
                                     for a, b in zip((512, 512, 256, 128, 64, 32), (256, 128, 64, 32, 16, 1)))
        self.decoder_bn = nn.ModuleList(nn.BatchNorm2d(c, eps=.001) for c in (256, 128, 64, 32, 16, 1))
        self.final = nn.Conv2d(1, 2, 4, dilation=2, padding=3)

    def forward(self, magnitude):
        if magnitude.ndim != 4 or magnitude.shape[1] != 2 or any(n % 64 for n in magnitude.shape[2:]):
            raise ValueError('The stereo spectrogram must have dimensions divisible by 64.')
        value = magnitude
        encoded = []
        for index, (layer, normalizer) in enumerate(zip(self.encoder, self.encoder_bn)):
            value = layer(F.pad(value, (1, 2, 1, 2)))
            encoded.append(value)
            # Upstream feeds raw conv6 to the decoder; its batch6 is unused.
            if index < 5:
                value = F.elu(normalizer(value))
        for index, (layer, normalizer) in enumerate(zip(self.decoder, self.decoder_bn)):
            height, width = value.shape[-2:]
            value = layer(value)[..., 1:1 + height * 2, 1:1 + width * 2]
            value = normalizer(F.elu(value))
            if index < 5:
                value = torch.cat((encoded[4-index], value), dim=1)
        return torch.sigmoid(self.final(value)) * magnitude


def variable_name(kind, index):
    return kind + (f'_{index}' if index else '')


def state_from_tensorflow(get_tensor, instrument_index):
    """Map only known numeric checkpoint tensors; never execute a saved graph."""
    state = {}
    for category, count, offset, kind in (
            ('encoder', 6, instrument_index * 7, 'conv2d'),
            ('decoder', 6, instrument_index * 6, 'conv2d_transpose')):
        for index in range(count):
            scope = variable_name(kind, offset + index)
            weight = get_tensor(scope + '/kernel')
            state[f'{category}.{index}.weight'] = torch.from_numpy(weight.transpose(3, 2, 0, 1).copy())
            state[f'{category}.{index}.bias'] = torch.from_numpy(get_tensor(scope + '/bias').copy())
    for category, offset in (('encoder_bn', 0), ('decoder_bn', 6)):
        for index in range(6):
            scope = variable_name('batch_normalization', instrument_index * 12 + offset + index)
            for destination, source in (('weight', 'gamma'), ('bias', 'beta'),
                                        ('running_mean', 'moving_mean'), ('running_var', 'moving_variance')):
                state[f'{category}.{index}.{destination}'] = torch.from_numpy(get_tensor(scope + '/' + source).copy())
            state[f'{category}.{index}.num_batches_tracked'] = torch.tensor(0, dtype=torch.int64)
    scope = variable_name('conv2d', instrument_index * 7 + 6)
    state['final.weight'] = torch.from_numpy(get_tensor(scope + '/kernel').transpose(3, 2, 0, 1).copy())
    state['final.bias'] = torch.from_numpy(get_tensor(scope + '/bias').copy())
    if any(t.dtype not in (torch.float32, torch.int64) or not torch.isfinite(t).all() for t in state.values()):
        raise ValueError('The separation checkpoint contains unexpected tensors.')
    return state


def load_models(path):
    models = {}
    with np.load(Path(path), allow_pickle=False) as archive:
        if len(archive.files) != 296:
            raise ValueError('Unexpected Spleeter tensor count.')
        for index, stem in enumerate(STEMS):
            model = UNet()
            model.load_state_dict(state_from_tensorflow(lambda name: archive[name], index), strict=True)
            models[stem] = model.eval()
    return models


def separate_waveform(audio, models, progress=lambda value: None, *, bins=1536, extension='average'):
    """Official ratio masks and Hann analysis/synthesis, at 44.1 kHz stereo.

    1536 bins is Spleeter's supported 16 kHz setting. The upstream 'average'
    extension preserves the remaining high frequencies and unity reconstruction.
    Inputs must already be bounded, decoded source passages, not entire mixes.
    """
    audio = np.asarray(audio, dtype='float32')
    if (audio.ndim != 2 or audio.shape[1] != 2 or not 1 <= len(audio) <= 256*44100
            or not np.isfinite(audio).all() or bins not in (1024,1536,2048)
            or extension not in ('zeros','average') or set(models) != set(STEMS)):
        raise ValueError('Invalid stereo passage or Spleeter configuration.')
    length, hop, partition = 4096, 1024, 512
    waveform = torch.from_numpy(audio.T.copy())
    padded = F.pad(waveform, (length, 0))
    frames = (padded.shape[1]+hop-1)//hop
    padded = F.pad(padded, (0, (frames-1)*hop+length-padded.shape[1]))
    window = torch.hann_window(length, periodic=True)
    with torch.inference_mode():
        spectrum = torch.stft(padded, n_fft=length, hop_length=hop, window=window,
                              center=False, return_complex=True)
        outputs = {name: torch.zeros((2,(frames-1)*hop+length)) for name in STEMS}
        blocks = (frames+partition-1)//partition
        for number, start in enumerate(range(0,frames,partition),1):
            progress(f'Separating musical parts · passage {number} of {blocks}')
            stop = min(frames,start+partition); count = stop-start
            magnitude = spectrum[:,:bins,start:stop].abs().transpose(1,2).unsqueeze(0)
            magnitude = F.pad(magnitude,(0,0,0,partition-count))
            estimates = {name:model(magnitude).square() for name,model in models.items()}
            denominator = sum(estimates.values())+1e-10
            for name in STEMS:
                mask = (estimates[name]+1e-10/len(STEMS))/denominator
                tail = mask.mean(dim=-1,keepdim=True) if extension=='average' else torch.zeros_like(mask[...,:1])
                mask = torch.cat((mask,tail.expand(*tail.shape[:-1],length//2+1-bins)),dim=-1)
                masked = mask[0,:,:count].transpose(1,2)*spectrum[:,:,start:stop]
                slices = torch.fft.irfft(masked,n=length,dim=1).transpose(1,2)*window*(2/3)
                reconstructed = F.fold(slices.transpose(1,2).reshape(1,2*length,count),
                                       output_size=(1,(count-1)*hop+length),
                                       kernel_size=(1,length),stride=(1,hop))[0,:,0]
                offset = start*hop
                outputs[name][:,offset:offset+reconstructed.shape[1]] += reconstructed
        return {name: value[:,length:length+len(audio)].T.numpy().copy() for name,value in outputs.items()}
