#!/usr/bin/env python3
"""Open the standalone native mastering lab using the production A/B player."""
import sys
from studio_mastering_player import MasteringLab, WaveformView, TITLE, fmt_time, main

if __name__ == '__main__':
    sys.exit(main())
