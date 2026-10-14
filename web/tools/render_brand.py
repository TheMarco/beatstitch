"""Render the website's favicon, Apple touch icon and share image from the CSS brand mark.

The mark is drawn by styles.css; tools/brand/brand.html lays out each export. This
renders that page offscreen with Qt WebEngine at twice the final size and scales down:

    QT_QPA_PLATFORM=offscreen .venv/bin/python web/tools/render_brand.py

The favicon is rendered on black and on white and the two are compared, which gives
its exact transparency. Open web/tools/brand/brand.html through a local server to see
the mark at every size.
"""
from __future__ import annotations

import functools
import http.server
import os
import sys
import threading
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu --disable-gpu-compositing')
os.environ['QT_SCALE_FACTOR'] = '2'

import numpy as np  # noqa: E402
from PySide6.QtCore import QTimer, QUrl, Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

WEB = Path(__file__).resolve().parents[1]
IMAGES = WEB / 'img'
SCALE = 2


def pixels(image: QImage) -> np.ndarray:
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.width(), 4)[:, :, :3] / 255


def to_image(rgba: np.ndarray) -> QImage:
    data = np.ascontiguousarray(np.clip(np.round(rgba * 255), 0, 255).astype(np.uint8))
    image = QImage(data.data, data.shape[1], data.shape[0], data.shape[1] * 4, QImage.Format.Format_RGBA8888)
    return image.copy()


def shrink(image: QImage, width: int, height: int) -> QImage:
    """Halve repeatedly in premultiplied color, so edges and transparency stay clean."""
    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    while image.width() >= width * 2:
        image = image.scaled(image.width() // 2, image.height() // 2, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def main():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(WEB)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_address[1]}/tools/brand/brand.html'

    app = QApplication(sys.argv[:1])
    view = QWebEngineView()
    shots = {}
    # (name, query, hash, CSS width, CSS height)
    # The icon draws at its final size, so it gets the small-mark rules (no grooves).
    jobs = [('icon-black', '?matte=%23000', 'icon', 64, 64), ('icon-white', '?matte=%23fff', 'icon', 64, 64),
            ('touch', '?job=touch', 'touch', 180, 180), ('og', '?job=og', 'og', 1200, 630)]

    def run(index=0):
        if index == len(jobs):
            app.quit()
            return
        name, query, target, width, height = jobs[index]
        view.resize(width, height)

        def loaded(ok):
            view.loadFinished.disconnect(loaded)
            if not ok:
                raise SystemExit(f'Could not load {base}')
            # Give the self-hosted fonts and gradients a moment to settle.
            QTimer.singleShot(1200, lambda: (shots.__setitem__(name, view.grab().toImage()), run(index + 1)))

        view.loadFinished.connect(loaded)
        view.load(QUrl(f'{base}{query}#{target}'))

    view.show()
    QTimer.singleShot(0, run)
    QTimer.singleShot(60000, app.quit)
    app.exec()
    if len(shots) != len(jobs):
        raise SystemExit('Rendering timed out.')

    black, white = pixels(shots['icon-black']), pixels(shots['icon-white'])
    alpha = np.clip(1 - (white - black).mean(axis=2), 0, 1)
    color = np.clip(black / np.maximum(alpha, 1e-4)[..., None], 0, 1)
    icon = to_image(np.dstack([color, alpha]))
    shrink(icon, 64, 64).save(str(IMAGES / 'favicon.png'))
    shrink(shots['touch'], 180, 180).convertToFormat(QImage.Format.Format_RGB32).save(str(IMAGES / 'apple-touch-icon.png'))
    shrink(shots['og'], 1200, 630).convertToFormat(QImage.Format.Format_RGB32).save(str(IMAGES / 'og.png'))
    for name in ('favicon.png', 'apple-touch-icon.png', 'og.png'):
        image = QImage(str(IMAGES / name))
        print('saved', name, image.width(), image.height())


if __name__ == '__main__':
    main()
