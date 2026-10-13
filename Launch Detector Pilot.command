#!/bin/zsh
set -e
cd "$(dirname "$0")"
exec .venv/bin/python detector_pilot_review.py outputs/dataset-pilot/pilot.json
