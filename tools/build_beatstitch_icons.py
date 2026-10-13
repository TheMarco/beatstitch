"""Make native app icons from the user's PNG, without redrawing the artwork.

Run with the project's Python/PySide6 environment. Icon generation is a maintainer
step; generated formats are checked in for builds on each platform.
"""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'assets/about'
source = QImage(str(DEST / 'beatstitch.png'))
assert not source.isNull() and (source.width(), source.height()) == (2172, 724)
# Crop away the wordmark and margins; retain the painted emblem and its glow.
emblem = source.copy(119, 106, 512, 512)


def render(size):
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    painter.scale(size/1024, size/1024)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(52, 52, 920, 920), 206, 206)
    painter.setClipPath(clip)
    painter.fillRect(QRectF(52, 52, 920, 920), QColor('#101114'))
    painter.drawImage(QRectF(124, 124, 776, 776), emblem)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, 'PNG')
    return bytes(data)


images = {size: render(size) for size in (16, 32, 48, 64, 128, 256, 512, 1024)}
(DEST / 'beatstitch-icon.png').write_bytes(images[1024])
sizes = (16, 32, 48, 64, 128, 256)
header = struct.pack('<HHH', 0, 1, len(sizes))
offset = 6 + 16*len(sizes)
records, payload = [], []
for size in sizes:
    data = images[size]
    records.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
    payload.append(data)
    offset += len(data)
(DEST / 'beatstitch.ico').write_bytes(header + b''.join(records + payload))
if sys.platform == 'darwin':
    with tempfile.TemporaryDirectory(prefix='beatstitch-icons-') as folder:
        iconset = Path(folder) / 'Beatstitch.iconset'
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            (iconset / f'icon_{size}x{size}.png').write_bytes(images[size])
            (iconset / f'icon_{size}x{size}@2x.png').write_bytes(images[size*2])
        subprocess.run(['iconutil', '-c', 'icns', '-o', str(DEST / 'beatstitch.icns'), str(iconset)], check=True)
print('Packaged the PNG emblem as a 1024px PNG, Windows ICO, and macOS ICNS.')
