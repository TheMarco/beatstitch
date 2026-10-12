# macOS Qt accessibility repair

This is a locally built Qt 6.11.2 Cocoa platform plugin for arm64. It applies
[Qt change 765434](https://codereview.qt-project.org/c/qt/qtbase/+/765434), revision
`c7fd3f34b997bb363be15650647665b3b6b8a5f4`, for QTBUG-149612. The upstream change
was still under review when checked on October 12, 2026; this is not an official
Qt patch release.

The two ownership guards stop synthetic accessibility cells from deleting their
parent table's interface. Selected songs remain available to accessibility
clients. No accessibility feature is disabled, and no music processing changes.

The unpatched control and patched candidate used the same application and build
recipe. In the unpatched control, selecting Andromeda, appending Voyager, and
opening More settings reproduced SIGSEGV in
`QMacAccessibilityElement::accessibilitySelectedChildren`. The patched candidate
passed that sequence, multi-selection, three empty-to-selected filter cycles,
four simple/advanced view changes, Undo, and native Open/cancel. Evidence is in
`outputs/qt-accessibility-research/`. This does not establish that every macOS
accessibility or VoiceOver interaction has been tested.

## Packaging

`build_desktop.py` checks the plugin, build recipe, patch, exact QtCore/QtGui
runtime hashes, architecture, version, and matching source archive before
building on macOS. It replaces the staging bundle's Cocoa plugin, includes this
notice, licenses and source, then signs and verifies the completed app. Windows
and Linux builds do not use this plugin. An unknown Mac runtime fails the build
until the fix has been reviewed and rebuilt for it.

## Rebuilding

Use Python with the pinned PySide6 Qt 6.11.2 arm64 runtime, Xcode's compiler,
CMake, and the matching Qt 6.11.2 development SDK (tested with Homebrew qtbase).
The SDK's generated GUI feature header is verified. Its Vulkan feature is
disabled in a local header overlay to match the official PySide6 macOS runtime,
which contains no Vulkan implementation. System SDK files are never edited.

From the checkout:

```sh
.venv/bin/python rebuild_qt_cocoa.py --sdk /opt/homebrew/opt/qtbase
```

From the bundled `Contents/Resources/qt-cocoa-fix` source package, run its
`rebuild_qt_cocoa.py` using a Python environment with that same PySide6 runtime,
passing `--work-dir` for a writable build directory. The bundled source archive
is used without downloading. In a checkout, the helper downloads the official
archive only if absent, verifies its pinned SHA-256, extracts the Cocoa sources,
applies the exact reviewed changes, and builds the plugin. It never installs or
publishes its result. Compiler/signature differences can change the binary hash;
repeat the native regression and full package checks before updating the pinned
binary and manifest.

The original Qt sources are supplied as `qtbase-v6.11.2.tar.gz` in the app's
source package; the checkout keeps that verified download under `assets/qt/`.
`upstream.patch` includes the original author's changes and regression test.
Qt copyright and license notices remain in the source files and `LICENSES/`.
