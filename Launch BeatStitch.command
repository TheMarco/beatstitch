#!/bin/zsh
cd "${0:A:h}" || exit 1
if [[ -x "dist/BeatStitch.app/Contents/MacOS/BeatStitch" ]]; then
    exec "dist/BeatStitch.app/Contents/MacOS/BeatStitch" "$@"
fi
if [[ -x "dist/DJ Studio.app/Contents/MacOS/BeatStitch" ]]; then
    exec "dist/DJ Studio.app/Contents/MacOS/BeatStitch" "$@"
fi
exec .venv/bin/python desktop_app.py "$@"
