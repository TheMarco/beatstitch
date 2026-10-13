"""Capture the website's product screenshots from the real app with the bundled demo open.

Uses an isolated temporary data folder, so your library and projects are untouched:

    QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2 .venv/bin/python web/tools/capture_screens.py

Needs cwebp on PATH. Images are written to web/img at twice their CSS size.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_SCALE_FACTOR', '2')

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import studio_theme as T  # noqa: E402
from desktop_app import Studio  # noqa: E402


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except AttributeError:
        pass
    app.setPalette(T.palette())
    app.setFont(T.font(T.SIZE_BODY))
    app.setStyleSheet(T.stylesheet())
    images = ROOT / 'web/img'
    images.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='beatstitch-shots-') as directory:
        work = Path(directory)
        window = Studio(work / 'data')
        window.resize(1440, 900)
        window.show()

        def settle(seconds=60):
            deadline = time.monotonic() + seconds
            while window.jobs and time.monotonic() < deadline:
                QTest.qWait(20)
            for _ in range(40):
                app.processEvents()
                QTest.qWait(20)

        def shot(name, widget, wait=800, crop_height=None):
            QTest.qWait(wait)
            settle()
            png = work / f'{name}.png'
            image = widget.grab()
            image.save(str(png))
            crop = ['-crop', '0', '0', str(image.width()), str(crop_height)] if crop_height else []
            subprocess.run(['cwebp', '-quiet', '-q', '86', '-m', '6', *crop, str(png), '-o', str(images / f'{name}.webp')],
                           check=True)
            print('saved', name, image.width(), crop_height or image.height())

        window.open_demo()
        settle()
        if window.help_window:
            window.help_window.close()
        settle()
        # Waveform bands load in the background after the demo opens; let their banner clear.
        shot('app-arrangement', window, wait=6000)

        window.edit_blend()
        settle(120)
        window.transition_editor.resize(1300, 820)
        # The top of the editor: shared bar grid, beat alignment, checks and overlap controls.
        shot('app-blend-editor', window.transition_editor, crop_height=1222)
        window.transition_editor.close()
        settle()

        window.show_help('beginner-course')
        settle()
        window.help_window.resize(1100, 760)
        shot('app-course', window.help_window)
        window.help_window.close()
        settle()
        window.close()
        app.processEvents()


if __name__ == '__main__':
    main()
