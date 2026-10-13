# Pinned section inference sources

This subset supports the optional, experimental local EDMFormer analysis.

- EDM-98: https://github.com/25ohms/EDM-98, revision `2dd942f2f9e71ffd826346828eeaba1dd3ece56a`, CC BY 4.0. Inference package and constants are unmodified.
- MusicFM: https://github.com/minzwon/musicfm, revision `b83ebedb401bcef639b26b05c0c8bee1dc2dfe71`. MIT code; see the included dual-license notice. Flash-attention implementation is omitted.
- MuQ: https://github.com/tencent-ailab/MuQ, revision `28847ea50cd31ac4b8b6a7dacc051ad7d1c7606a`. MIT code. The package initializer is narrowed to MuQ only; MuLan, training code and flash-attention implementation are omitted. **MuQ model weights are CC BY-NC 4.0**; see `MuQ-LICENSE-weights`.

The app adapter replaces remote model loading with verified local files, uses safe tensor/weights-only checkpoint loading, and applies the separately tested attention/frontend optimizations in `studio_section_attention.py`. Configuration and weight hashes are pinned in `section-model-manifest.json`. MuQ permits noncommercial redistribution, so the weights remain bundled in the optional noncommercial section edition. The GPL application code is licensed separately. Donations are voluntary and never unlock access; donationware alone does not establish that every distribution or use is NonCommercial.

MusicFM identity and published terms were checked on 2026-10-13. Our `musicfm.pt` (SHA-256 `218b483a0256ddef736267425fabb166fd97008983696bb9270def464b47bded`, 1,316,802,088 bytes) matches `pretrained_msd.pt` in the author's [MIT-labeled model repository](https://huggingface.co/minzwon/MusicFM/blob/main/README.md), revision `4513b38bc25ad1d227b1980819b9691ba97f4d87`. This establishes the publisher and model license label. In [the author's commercial-use reply](https://github.com/minzwon/musicfm/issues/6#issuecomment-4948654100), he does not confirm that all third-party rights for a particular commercial use have been cleared. The model's MIT label does not establish permissions for the underlying training recordings. MuQ's noncommercial terms still prevent treating this entire pack as unrestricted for commercial model use. A core/Spleeter edition can omit the section pack; direct downloads are not required solely because a license is noncommercial.

Research/model attribution: EDMFormer / EDM-98 by 25ohms; MusicFM by Minz Won and collaborators; MuQ by Tencent AI Lab and collaborators. No source song is included or uploaded by this integration.
