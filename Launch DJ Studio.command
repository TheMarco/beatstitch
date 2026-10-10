#!/bin/zsh
cd "${0:A:h}" || exit 1
if [[ -x "dist/DJ Studio.app/Contents/MacOS/DJ Studio" ]]; then
    exec "dist/DJ Studio.app/Contents/MacOS/DJ Studio" --data-dir "$PWD/outputs/studio" "$@"
fi
exec .venv/bin/python desktop_app.py --data-dir outputs/studio "$@"
