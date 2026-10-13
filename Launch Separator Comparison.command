#!/bin/zsh
cd "${0:A:h}"
exec .venv/bin/python prototype_separation.py outputs/spleeter-research/comparison/report.json
