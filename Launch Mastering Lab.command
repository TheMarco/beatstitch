#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
exec .venv/bin/python prototype_mastering_player.py --folder outputs/mastering-prototype
