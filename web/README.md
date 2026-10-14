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

`build_media.py` renders `tools/demo/demo-mix.djmix` with the real engine, builds
the "Without" comparison (original tempos and keys, hand-aligned first downbeats,
plain crossfade, level-matched), encodes the three listening examples and writes
waveform data, song colors and measured kick offsets to `data/media.js`.
`capture_screens.py` opens the same project in the app offscreen, using a temporary
data folder, and saves the screenshots to `img/`.

The demo project is a 2:52 excerpt of a saved mix of two songs by Marco van Hylckama
Vlieg: Lose Yourself In The Latent Space into Friday Night LFG, with an 8-bar blend
and the second song raised two semitones. The songs are not in the repository, so
both scripts need them at the paths stored in the project. Pass another project to
`build_media.py` to use different songs, then update the song names in `index.html`.
`build_brand.py` cuts the logo master in `tools/brand/` into the navigation logo,
favicons, share image and the three emblem layers the hero record is built from.

## Before publishing

The macOS, Windows and Linux cards in the download section link to the installers on
Google Drive (search `data-os` in `index.html`). The hero button takes its label and
link from the card that matches the visitor's OS and falls back to macOS. `og:image`
is relative; make it absolute once the site has a domain.
