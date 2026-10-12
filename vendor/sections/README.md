# Pinned section inference sources

This subset supports the optional, experimental local EDMFormer analysis.

- EDM-98: https://github.com/25ohms/EDM-98, revision `2dd942f2f9e71ffd826346828eeaba1dd3ece56a`, CC BY 4.0. Inference package and constants are unmodified.
- MusicFM: https://github.com/minzwon/musicfm, revision `b83ebedb401bcef639b26b05c0c8bee1dc2dfe71`. MIT code; see the included dual-license notice. Flash-attention implementation is omitted.
- MuQ: https://github.com/tencent-ailab/MuQ, revision `28847ea50cd31ac4b8b6a7dacc051ad7d1c7606a`. MIT code. The package initializer is narrowed to MuQ only; MuLan, training code and flash-attention implementation are omitted. **MuQ model weights are CC BY-NC 4.0**; see `MuQ-LICENSE-weights`.

The app adapter replaces remote model loading with verified local files, uses safe tensor/weights-only checkpoint loading, and applies the separately tested attention/frontend optimizations in `studio_section_attention.py`. Configuration and weight hashes are pinned in `section-model-manifest.json`. The optional pack is intended for this noncommercial local prototype; it is not a claim that all weights are cleared for commercial distribution. MusicFM MSD weight provenance and terms need further review before public distribution.

Research/model attribution: EDMFormer / EDM-98 by 25ohms; MusicFM by Minz Won and collaborators; MuQ by Tencent AI Lab and collaborators. No source song is included or uploaded by this integration.
