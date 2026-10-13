"""Cut the BeatStitch logo artwork into the website's brand images.

The master is a raster lockup on a dark background (web/tools/brand/beatstitch-lockup.png).
This removes the background and splits the emblem into its purple half, amber half and
stitches, so the page can turn the record and pull the halves apart. Pulled apart, each
half keeps the outlined socket where a stitch sat.

    .venv/bin/python web/tools/build_brand.py

Needs cwebp and ffmpeg on PATH.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PySide6.QtGui import QImage
from scipy import ndimage

WEB = Path(__file__).resolve().parents[1]
MASTER = WEB / 'tools/brand/beatstitch-lockup.png'
IMAGES = WEB / 'img'


def load(path: Path) -> np.ndarray:
    image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.width(), 4)[:, :, :3].astype(float)


def save(rgba: np.ndarray, path: Path):
    data = np.ascontiguousarray(np.clip(np.round(rgba), 0, 255).astype(np.uint8))
    image = QImage(data.data, data.shape[1], data.shape[0], data.shape[1] * 4, QImage.Format.Format_RGBA8888)
    image.save(str(path))


def webp(png: Path, destination: Path, width: int | None = None):
    resize = ['-resize', str(width), '0'] if width else []
    subprocess.run(['cwebp', '-quiet', '-q', '92', '-alpha_q', '100', '-m', '6', *resize, str(png), '-o', str(destination)],
                   check=True)


def matte(rgb: np.ndarray, background: np.ndarray):
    """Alpha from the distance to the flat background, then un-premultiply the color."""
    distance = np.sqrt(((rgb - background) ** 2).sum(axis=2))
    alpha = np.clip((distance - 6) / 34, 0, 1)
    safe = np.maximum(alpha, 1e-3)[..., None]
    color = np.clip((rgb - (1 - alpha[..., None]) * background) / safe, 0, 255)
    return color, alpha


def disk(radius: int):
    y, x = np.ogrid[-radius:radius + 1, -radius:radius + 1]
    return x * x + y * y <= radius * radius


def main():
    rgb = load(MASTER)
    background = np.median(rgb[:150, :150].reshape(-1, 3), axis=0)
    color, alpha = matte(rgb, background)
    luminance = rgb @ np.array([0.2126, 0.7152, 0.0722])

    # Emblem: the artwork left of the wordmark, cropped to a centered square.
    solid = alpha > 0.5
    ys, xs = np.nonzero(solid[:1300, :950])
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    half = int(max(xs.max() - xs.min(), ys.max() - ys.min()) / 2) + 14
    box = (slice(int(cy) - half, int(cy) - half + 2 * half), slice(int(cx) - half, int(cx) - half + 2 * half))
    e_color, e_alpha, e_light = color[box], alpha[box], luminance[box]
    size = e_alpha.shape[0]

    # Stitches: the light capsules, found from their centers, plus their outline.
    labels, _ = ndimage.label((e_light > 115) & (e_alpha > 0.9))
    centers = []
    for row, col in ((int(size * 0.29), int(size * 0.58)), (int(size * 0.69), int(size * 0.41))):
        window = labels[row - 25:row + 25, col - 25:col + 25]
        found = np.bincount(window[window > 0]).argmax()
        centers.append(found)
    core = np.isin(labels, centers)
    core = ndimage.binary_dilation(core, structure=disk(2))
    stitches = ndimage.binary_dilation(core, structure=disk(7)) & (e_alpha > 0.02)

    # Halves: what remains, split into the purple and amber pieces.
    rest = (e_alpha > 0.02) & ~stitches
    parts, count = ndimage.label(rest)
    sizes = ndimage.sum(np.ones_like(parts), parts, index=np.arange(1, count + 1))
    big = np.argsort(sizes)[-2:] + 1
    purple_id = max(big, key=lambda i: (e_color[parts == i][:, 2] - e_color[parts == i][:, 0]).mean())
    amber_id = [i for i in big if i != purple_id][0]
    # Assign stray fragments to the nearest big piece.
    _, (iy, ix) = ndimage.distance_transform_edt(~np.isin(parts, big), return_indices=True)
    owner = parts[iy, ix]
    masks = {'purple': rest & (owner == purple_id), 'amber': rest & (owner == amber_id)}

    layers = {}
    centers_px = {}
    for name, mask in masks.items():
        rgba = np.zeros((size, size, 4))
        rgba[..., :3] = e_color
        rgba[..., 3] = e_alpha * 255 * mask
        layers[name] = rgba
        ys, xs = np.nonzero(mask)
        centers_px[name] = (xs.mean(), ys.mean())

    stitch_layer = np.zeros((size, size, 4))
    stitch_layer[..., :3] = e_color
    stitch_layer[..., 3] = e_alpha * 255 * stitches
    layers['stitches'] = stitch_layer

    full = np.zeros((size, size, 4))
    full[..., :3] = e_color
    full[..., 3] = e_alpha * 255
    layers['full'] = full

    # The record's hole: background pixels nearest the middle of the emblem.
    hole = (e_alpha < 0.05)
    hole_labels, _ = ndimage.label(hole)
    middle = hole_labels[size // 2, size // 2]
    hy, hx = np.nonzero(hole_labels == middle) if middle else (np.array([size / 2]), np.array([size / 2]))

    # Separation direction: along the stitches, from the purple half to the amber half.
    (px, py), (ax, ay) = centers_px['purple'], centers_px['amber']
    direction = np.array([ax - px, ay - py])
    direction /= np.linalg.norm(direction)

    # Lockup for the navigation: emblem and wordmark, cropped to the artwork.
    region = alpha[:1300, :2700] > 0.25
    ys, xs = np.nonzero(region)
    pad = 6
    lock = (slice(ys.min() - pad, ys.max() + pad), slice(xs.min() - pad, xs.max() + pad))
    lockup = np.zeros(alpha[lock].shape + (4,))
    lockup[..., :3] = color[lock]
    lockup[..., 3] = alpha[lock] * 255

    IMAGES.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='beatstitch-brand-') as directory:
        work = Path(directory)
        for name, rgba in layers.items():
            save(rgba, work / f'{name}.png')
            webp(work / f'{name}.png', IMAGES / f'emblem-{name}.webp')
        save(lockup, work / 'lockup.png')
        height = 120
        webp(work / 'lockup.png', IMAGES / 'beatstitch-logo.webp', int(round(lockup.shape[1] * height / lockup.shape[0])))
        for target, edge, matte_color in (('favicon.png', 64, None), ('apple-touch-icon.png', 180, '0x121217')):
            filters = f'scale={edge}:{edge}:flags=lanczos'
            if matte_color:
                filters = f'color=c={matte_color}:s={edge}x{edge}[bg];[0:v]{filters}[fg];[bg][fg]overlay=format=auto'
                subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-i', str(work / 'full.png'),
                                '-filter_complex', filters, '-frames:v', '1', str(IMAGES / target)], check=True)
            else:
                subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-i', str(work / 'full.png'),
                                '-vf', filters, str(IMAGES / target)], check=True)
        subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-i', str(MASTER),
                        '-vf', 'scale=1200:-2:flags=lanczos,crop=1200:630', str(IMAGES / 'og.png')], check=True)

    geometry = {
        'size': size,
        'hole': [round(float(hx.mean()) / size * 100, 2), round(float(hy.mean()) / size * 100, 2)],
        'holeDiameter': round(float(2 * np.sqrt(len(hx) / np.pi)) / size * 100, 2),
        'apart': [round(float(direction[0]), 3), round(float(direction[1]), 3)],
        'lockup': [lockup.shape[1], lockup.shape[0]],
    }
    (WEB / 'tools/brand/geometry.json').write_text(json.dumps(geometry, indent=2) + '\n')
    print(json.dumps(geometry))


if __name__ == '__main__':
    main()
