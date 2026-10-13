#!/usr/bin/env python3
"""Refine the vector emblem against the approved raster reference.

The source supplies silhouettes only; the result is editable SVG geometry,
gradients, and highlights. Pillow and NumPy are development dependencies.
"""
import argparse
from copy import deepcopy
import math
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageFilter

from build_beatstitch_lockups import contours, simplify_loop, smooth_path, tag, save


def add_stops(gradient, stops):
    gradient[:] = []
    for offset, color, opacity in stops:
        attributes = {"offset": str(offset), "stop-color": color}
        if opacity != 1:
            attributes["stop-opacity"] = str(opacity)
        ET.SubElement(gradient, tag("stop"), attributes)


def silhouette(mask):
    mask = soften_mask(mask, 1.15)
    loops = [(points, area) for points, area in contours(mask) if area > 100]
    return max(loops, key=lambda item: item[1])[0]


def soften_mask(mask, radius):
    blurred = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(blurred) >= 128


def groove_path(curve, width):
    """A smooth, tapered carving with the measured curve endpoints."""
    (ax, ay), (bx, by), (cx, cy), (dx, dy) = curve
    left, right = [], []
    for i in range(33):
        t = i / 32
        u = 1 - t
        x = u ** 3 * ax + 3 * u * u * t * bx + 3 * u * t * t * cx + t ** 3 * dx
        y = u ** 3 * ay + 3 * u * u * t * by + 3 * u * t * t * cy + t ** 3 * dy
        tx = 3 * u * u * (bx - ax) + 6 * u * t * (cx - bx) + 3 * t * t * (dx - cx)
        ty = 3 * u * u * (by - ay) + 6 * u * t * (cy - by) + 3 * t * t * (dy - cy)
        length = math.hypot(tx, ty)
        half = width / 2 * math.sin(math.pi * t) ** .65
        left.append((round(x - ty / length * half, 2), round(y + tx / length * half, 2)))
        right.append((round(x + ty / length * half, 2), round(y - tx / length * half, 2)))
    # Omit duplicate zero-width endpoints on the return side.
    return smooth_path(left + right[-2:0:-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--symbol", required=True)
    args = parser.parse_args()
    root = ET.parse(args.symbol).getroot()
    definitions = deepcopy(root.find(tag("defs")))
    gradient = {element.get("id"): element for element in definitions}
    for name, coordinates, stops in (
        ("purpleEnamel", (180, 145, 380, 550), [
            (0, "#C353FF", 1), (.16, "#8A12FF", 1), (.40, "#6300EF", 1),
            (.69, "#6800F1", 1), (.86, "#8800FE", 1), (1, "#A52AFF", 1)]),
        ("amberEnamel", (530, 180, 360, 580), [
            (0, "#FFB500", 1), (.22, "#FF9300", 1), (.49, "#FF7800", 1),
            (.74, "#FFB400", 1), (.89, "#FFCC29", 1), (1, "#FF9700", 1)])
    ):
        item = gradient[name]
        item.set("gradientUnits", "userSpaceOnUse")
        for key, value in zip(("x1", "y1", "x2", "y2"), coordinates):
            item.set(key, str(value))
        add_stops(item, stops)
    for name, values, stops in (
        ("purpleSheen", (270, 185, 190), [(0, "#F8C5FF", .58), (.37, "#D37EFF", .18), (1, "#A448FF", 0)]),
        ("amberSheen", (490, 465, 180), [(0, "#FFF5AC", .66), (.35, "#FFF093", .28), (1, "#FFDD4E", 0)])
    ):
        item = gradient[name]
        item.set("gradientUnits", "userSpaceOnUse")
        for key, value in zip(("cx", "cy", "r"), values):
            item.set(key, str(value))
        add_stops(item, stops)

    rgb = np.asarray(Image.open(args.reference).convert("RGB"))[:, :640].astype(float)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    purple = silhouette((b > 110) & (b - r > 12) & (b - g > 60))
    amber = silhouette((r > 120) & (r - b > 60) & (g > 30))
    all_color = (rgb.max(axis=2) > 120) & (rgb.max(axis=2) - rgb.min(axis=2) > 70)
    stitches = sorted([(p, a) for p, a in contours(soften_mask(all_color, 1.0)) if 2000 < a < 4000],
                      key=lambda pair: min(y for x, y in pair[0]))
    if len(stitches) != 2:
        raise ValueError("Expected exactly two stitch connector silhouettes")
    artwork = ET.Element(tag("g"), {"id": "beatstitch-symbol",
        "transform": "translate(24 34) scale(1.0131004) translate(-146 -143)"})
    for name, points, gradient_id, stroke, shadow in (
        ("purple", purple, "purpleEnamel", "#CE64FF", "#160055"),
        ("amber", amber, "amberEnamel", "#FFCB24", "#6B1C00")
    ):
        half = ET.SubElement(artwork, tag("g"), {"id": f"disc-{name}"})
        data = smooth_path(simplify_loop(points, 2.5))
        ET.SubElement(half, tag("path"), {"id": f"{name}-half", "d": data,
            "fill": f"url(#{gradient_id})", "stroke": stroke, "stroke-width": "1.1",
            "stroke-linejoin": "round"})
        ET.SubElement(half, tag("path"), {"id": f"{name}-sheen", "d": data,
            "fill": f"url(#{name}Sheen)"})
        # Clean vector curves replace pixel-level shading breaks in the carving.
        carving = (
            [((355, 190), (210, 174), (90, 290), (247, 402)),
             ((352, 228), (236, 212), (151, 327), (280, 419)),
             ((319, 261), (261, 262), (218, 334), (296, 407))]
            if name == "purple" else
            [((518, 266), (593, 399), (531, 520), (396, 536)),
             ((492, 289), (550, 402), (505, 486), (399, 501)),
             ((464, 319), (509, 406), (478, 451), (409, 464))]
        )
        grooves = ET.SubElement(half, tag("g"), {"id": f"{name}-carved-grooves", "fill": shadow})
        for index, curve in enumerate(carving):
            ET.SubElement(grooves, tag("path"), {"id": f"{name}-groove-{index + 1}",
                "d": groove_path(curve, 6.8 - index * .5), "opacity": ".9"})
    connectors = ET.SubElement(artwork, tag("g"), {"id": "stitch-connectors"})
    for index, (points, _) in enumerate(stitches):
        identifier = "stitchTop" if index == 0 else "stitchBottom"
        cx = (min(x for x, y in points) + max(x for x, y in points)) / 2
        cy = (min(y for x, y in points) + max(y for x, y in points)) / 2
        placement = f"rotate(28 {cx:g} {cy:g})"
        ET.SubElement(connectors, tag("rect"), {"id": f"stitch-{index + 1}",
            "x": f"{cx - 49:g}", "y": f"{cy - 15:g}", "width": "98", "height": "30",
            "rx": "15", "transform": placement,
            "fill": f"url(#{identifier})", "stroke": "#25101D", "stroke-width": "3.5",
            "stroke-linejoin": "round"})
        ET.SubElement(connectors, tag("rect"), {"x": f"{cx - 43:g}", "y": f"{cy - 11:g}",
            "width": "86", "height": "7", "rx": "3.5", "transform": placement,
            "fill": "#FFF4DF", "opacity": ".3"})
    root[:] = [node for node in root if node.tag in (tag("title"), tag("desc"))]
    root.append(definitions)
    root.append(artwork)
    save(root, args.symbol)
    print({"halves": 2, "stitch_connectors": len(stitches), "raster_embedded": False})


if __name__ == "__main__":
    main()
