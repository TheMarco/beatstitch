# BeatStitch Mac release

The release is a native Apple Silicon application for macOS 14 or newer. Keep the
application archive, matching source companion, checksums and this README together
on the same download page. The source companion contains the application snapshot,
covered library sources, original notices, patches and build recipes; a link to a
moving Git branch is not a substitute. Nothing here publishes a release by itself.

The app is free and all features are available without a donation. About includes
an entirely optional link: “If you like this tool, consider buying me a coffee.”
The original app code is GPL-3.0-or-later. Model/asset licenses remain separate.
The optional advanced download includes MuQ weights under CC BY-NC 4.0. The creator accepted the
uncertainty about this specific optional-support arrangement without obtaining
publisher clarification. This decision does not change MuQ's license or grant
permission for commercial model use. Spleeter code and weights are MIT licensed.

## Run the app

Extract the application archive and move BeatStitch.app to a folder of your choice,
usually Applications. Developer ID signing and Apple notarization are tracked in
[MAC_SIGNING.md](MAC_SIGNING.md). The default installer contains no model weights.
The starter screen and File → **Downloads & features** offer the three recommended small
detectors (125 MB total), separate vocal removal (146 MB), and optional advanced
build/drop detection (3.07 GB download). Users may skip all downloads and mix with
manual controls. Downloads show progress, cancellation and retry; installed models
are stored separately in user data and survive updates. Analysis runs locally and
offline after installation. About → Credits & licenses works offline.

An existing installation continues using the established DJ Studio data folder.
The visible name is BeatStitch; projects, preferences, library and prepared audio
are not moved or discarded. The macOS bundle identifier is deliberately retained.
Quit an old instance before launching the new one.

## Rebuild the application

Unpack the application snapshot in the source companion. Use Python 3.13.5 on
Apple Silicon and the supplied `requirements-release-macos.txt` for this release's
Python dependency versions. The credits also identify optional support imports
collected from this build environment; those may differ in a fresh environment.
All application and model preparers are in that snapshot:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-release-macos.txt
.venv/bin/python build_demo_assets.py
.venv/bin/python build_listening_lessons.py
.venv/bin/python rebuild_qt_cocoa.py
.venv/bin/python build_desktop.py --check
```

Provide FFmpeg, ffprobe and Rubber Band on the build PATH. The audio-tool sources
and original formulae are included separately in the companion, with exact
versions and configure/build records. The model preparers verify pinned identities;
Spleeter conversion needs NumPy, not TensorFlow. All inference runtimes are included
by default; their weights are user-selected downloads. A developer-only offline
edition can be built with `--bundle-models` after provisioning verified weights.
Check the real HTTPS key-model download and frozen inference without developer
assets using `BeatStitch --check-model-download /new/isolated/report-folder`.
Check advanced workers using external models, never by adding them to the app:

```sh
.venv/bin/python -m ci.check_optional_workers \
  --executable 'dist/BeatStitch.app/Contents/MacOS/BeatStitch' \
  --resources 'dist/BeatStitch.app/Contents/Frameworks' \
  --output dist/optional-package-check --model-dir /path/to/test-models \
  --sections --separation
```

## Rebuild or replace libraries

The companion's REBUILD.txt, upstream archives and original formulae describe the
native builds. All covered libraries are dynamically linked. Upstream Qt 6.11.2
and Qt for Python 6.11.2 source archives include their configure/CMake/setup build
scripts. The separate Qt Cocoa repair includes the matching source, patch and
standalone rebuild script. GCC 13.4.0's source and original Homebrew Darwin patch
match SciPy's Fortran libraries; provenance records both UUIDs and code-section
identities. Build dependency tools can come from your own system.

Rebuild the entire app with replacement wheels/libraries, or replace a compatible
dynamic library in Contents/Frameworks, preserving required symlink layout and
install names. PyInstaller rewrites dynamic-library paths to bundle-relative
locations. For an edited bundle, sign your own copy again:

```sh
codesign --force --deep --sign - '/path/to/BeatStitch.app'
codesign --verify --deep --strict '/path/to/BeatStitch.app'
```

No BeatStitch activation key or permission is required to rebuild or replace a
library. Matching source is supplied; byte-for-byte recreation of a publisher's
signed wheel is not claimed. Original build host paths may be adapted to your
prefix and SDK. Windows/Linux binaries need their own corresponding-source sets.

## Prepare a source companion

`tools/stage_release_sources.py` combines the reviewed local source receipts,
current app snapshot and final native-binary inventory. `release_source_bundle.py`
verifies and packages that manifest. It refuses missing/changed files and existing
output destinations. The receipts record bytes and declared coverage; they are
not a warranty of legal clearance for every possible use.

```sh
.venv/bin/python tools/stage_release_sources.py \
  --source-root outputs/release-sources --app 'dist/BeatStitch.app'
.venv/bin/python release_source_bundle.py \
  --manifest outputs/release-sources/source-manifest.json \
  --source-root outputs/release-sources \
  --output 'dist/release/BeatStitch-mac-sources.zip'
```

Do not include private songs, audio previews, autosaves, preferences, cache folders,
research Demucs weights or credentials. Publish the final app and source checksums
alongside the downloads. Rebuild and recheck after changing dependencies.
