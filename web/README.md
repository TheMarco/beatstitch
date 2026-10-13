# BeatStitch website

A static, single-page site with no build step and no third-party requests. The
demo mix, comparison audio, waveforms and screenshots all come from the app.

## Preview

Browsers block audio decoding on pages opened from disk, so serve the folder:

```sh
python3 -m http.server 8000 --directory web
```

Then open http://localhost:8000.

## Rebuild the media

From the repository root, with the app's environment (FFmpeg and Rubber Band on PATH):

```sh
.venv/bin/python web/tools/build_media.py
QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2 .venv/bin/python web/tools/capture_screens.py
.venv/bin/python web/tools/build_brand.py
```

`build_media.py` renders the bundled demo with the real engine, builds the
"Without" comparison (original tempos, hand-aligned first downbeats, plain
crossfade, level-matched), encodes the three listening examples and writes
waveform data to `data/media.js`. `capture_screens.py` opens the demo in the app
offscreen, using a temporary data folder, and saves the screenshots to `img/`.
`build_brand.py` cuts the logo master in `tools/brand/` into the navigation logo,
favicons, share image and the three emblem layers the hero record is built from.

## Before publishing

The download buttons for macOS, Windows and Linux all point to
`https://github.com/TheMarco/dj-studio/releases/latest` (search `data-download` in
`index.html`); the hero button's label follows the visitor's OS. The repository is
private, so update the links or publish a release first. `og:image` is relative; make it absolute once the
site has a domain.
