#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ -x "dist/DJ Studio/DJ Studio" ]; then
    exec "dist/DJ Studio/DJ Studio" --data-dir "$PWD/outputs/studio" "$@"
fi
exec .venv/bin/python desktop_app.py --data-dir outputs/studio "$@"
