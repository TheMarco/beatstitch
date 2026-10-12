"""Inference-only PANNs CNN10 adapter; all audio stays on this computer.

Adapted from Qiuqiang Kong's audioset_tagging_cnn (MIT), revision
d2f4b8c18eab44737fcc0de1248ae21eb43f6aa4. See assets/models/PANNS-LICENSE.MIT.
The FFT frontend replaces the equivalent fixed DFT convolutions. No training,
augmentation, checkpoint code execution, or runtime model downloads are used.
"""
import torch
from torch import nn
from torch.nn import functional as F


class Block(nn.Module):
    def __init__(self, incoming, outgoing):
        super().__init__()
        self.conv1 = nn.Conv2d(incoming, outgoing, 3, padding=1, bias=False)
        self.conv2 = nn.Conv2d(outgoing, outgoing, 3, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(outgoing)
        self.bn2 = nn.BatchNorm2d(outgoing)

    def forward(self, x):
        x = F.relu(self.bn1(self.conv1(x)))
        return F.avg_pool2d(F.relu(self.bn2(self.conv2(x))), 2)


class ContentModel(nn.Module):
    def __init__(self, checkpoint):
        super().__init__()
        self.bn0 = nn.BatchNorm2d(64)
        for i, (a, b) in enumerate(((1, 64), (64, 128), (128, 256), (256, 512)), 1):
            setattr(self, f'conv_block{i}', Block(a, b))
        self.fc1 = nn.Linear(512, 512)
        self.fc_audioset = nn.Linear(512, 527)
        state = torch.load(checkpoint, map_location='cpu', weights_only=True)['model']
        self.register_buffer('mel', state.pop('logmel_extractor.melW'))
        for name in ('real', 'imag'):
            weight = state.pop(f'spectrogram_extractor.stft.conv_{name}.weight')
            if weight.shape != (513, 1, 1024): raise ValueError('Unexpected content model frontend.')
        if self.mel.shape != (513, 64): raise ValueError('Unexpected content model filter bank.')
        result = self.load_state_dict(state, strict=False)
        if result.missing_keys != ['mel'] or result.unexpected_keys:
            raise ValueError('Unexpected content model parameters.')
        self.register_buffer('window', torch.hann_window(1024))
        self.eval()

    def forward(self, audio):
        spectrum = torch.stft(audio, n_fft=1024, hop_length=320, window=self.window,
                              center=True, pad_mode='reflect', return_complex=True)
        power = spectrum.abs().square().transpose(1, 2) @ self.mel
        x = (10 * torch.log10(power.clamp_min(1e-10)))[:, None, :, :]
        x = self.bn0(x.transpose(1, 3)).transpose(1, 3)
        for i in range(1, 5): x = getattr(self, f'conv_block{i}')(x)
        x = x.mean(3)
        return self.fc_audioset(F.relu(self.fc1(x.amax(2) + x.mean(2)))).sigmoid()
