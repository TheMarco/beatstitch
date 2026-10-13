#!/usr/bin/env python3
"""Build standalone vector lockups from the approved emblem and lettering.

The PNG is a tracing reference only. Exported SVGs contain paths and gradients,
never embedded raster artwork or external fonts. Requires Pillow and NumPy.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)


def tag(name):
    return f"{{{SVG}}}{name}"


def contours(mask):
    """Walk foreground pixel boundaries, preserving the letter counters."""
    padded = np.pad(mask, 1)
    sides = (
        (mask & ~padded[:-2, 1:-1], (0, 0, 1, 0)),
        (mask & ~padded[1:-1, 2:], (1, 0, 1, 1)),
        (mask & ~padded[2:, 1:-1], (1, 1, 0, 1)),
        (mask & ~padded[1:-1, :-2], (0, 1, 0, 0)),
    )
    outgoing = defaultdict(list)
    remaining = set()
    for pixels, (dx1, dy1, dx2, dy2) in sides:
        yy, xx = np.nonzero(pixels)
        for y, x in zip(yy.tolist(), xx.tolist()):
            edge = (x + dx1, y + dy1, x + dx2, y + dy2)
            outgoing[edge[:2]].append(edge)
            remaining.add(edge)
    direction = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}
    turn_priority = {1: 0, 0: 1, 3: 2, 2: 3}
    loops = []
    while remaining:
        edge = min(remaining)
        start = edge[:2]
        points = [start]
        while True:
            remaining.remove(edge)
            end = edge[2:]
            if end == start:
                break
            points.append(end)
            options = [e for e in outgoing[end] if e in remaining]
            if not options:
                raise ValueError("Open contour in the lettering mask")
            old_dir = direction[(edge[2] - edge[0], edge[3] - edge[1])]
            edge = min(options, key=lambda e: turn_priority[
                (direction[(e[2] - e[0], e[3] - e[1])] - old_dir) % 4
            ])
        area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2)
                   in zip(points, points[1:] + points[:1])) / 2
        if abs(area) >= 3:
            loops.append((points, area))
    return loops


def simplify_open(points, tolerance):
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        start, stop = stack.pop()
        if stop <= start + 1:
            continue
        ax, ay = points[start]
        bx, by = points[stop]
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        largest, index = -1.0, None
        for i in range(start + 1, stop):
            x, y = points[i]
            if length_sq:
                u = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length_sq))
                distance_sq = (x - ax - u * dx) ** 2 + (y - ay - u * dy) ** 2
            else:
                distance_sq = (x - ax) ** 2 + (y - ay) ** 2
            if distance_sq > largest:
                largest, index = distance_sq, i
        if largest > tolerance * tolerance:
            keep.add(index)
            stack.extend(((start, index), (index, stop)))
    return [points[i] for i in sorted(keep)]


def simplify_loop(points, tolerance=2.0):
    anchor = min(range(len(points)), key=lambda i: points[i])
    rotated = points[anchor:] + points[:anchor]
    ax, ay = rotated[0]
    split = max(range(1, len(rotated)),
                key=lambda i: (rotated[i][0] - ax) ** 2 + (rotated[i][1] - ay) ** 2)
    first = simplify_open(rotated[:split + 1], tolerance)
    second = simplify_open(rotated[split:] + rotated[:1], tolerance)
    return first[:-1] + second[:-1]


def smooth_path(points):
    """Keep hard corners; turn gently bending contour sections into curves."""
    tangents = []
    for i, (x, y) in enumerate(points):
        px, py = points[i - 1]
        nx, ny = points[(i + 1) % len(points)]
        before, after = math.hypot(x - px, y - py), math.hypot(nx - x, ny - y)
        incoming = ((x - px) / before, (y - py) / before)
        outgoing = ((nx - x) / after, (ny - y) / after)
        cosine = incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
        if cosine < 0.78:
            tangents.append((0.0, 0.0))
            continue
        tx, ty = incoming[0] + outgoing[0], incoming[1] + outgoing[1]
        length = math.hypot(tx, ty)
        handle = min(before, after) * 0.28
        tangents.append((tx / length * handle, ty / length * handle))
    result = [f"M{points[0][0]} {points[0][1]}"]
    for i, (x, y) in enumerate(points):
        j = (i + 1) % len(points)
        nx, ny = points[j]
        tx, ty = tangents[i]
        ux, uy = tangents[j]
        if not any((tx, ty, ux, uy)):
            result.append(f"L{nx} {ny}")
        else:
            result.append(f"C{x + tx:.2f} {y + ty:.2f} {nx - ux:.2f} {ny - uy:.2f} {nx} {ny}")
    result.append("Z")
    return " ".join(result)


def outlined_wordmark(source):
    rgb = np.asarray(Image.open(source).convert("RGB"))
    mask = rgb.min(axis=2) > 180
    # The approved horizontal master places the emblem before this boundary.
    mask[:, :640] = False
    yy, xx = np.nonzero(mask)
    if not len(xx):
        raise ValueError("No ivory lettering found in the approved master")
    bounds = (int(xx.min()), int(yy.min()), int(xx.max() + 1), int(yy.max() + 1))
    x1, y1, x2, y2 = bounds
    loops = contours(mask[y1:y2, x1:x2])
    # Reconstruct the clean geometric letterforms rather than retaining small
    # bitmap edge distortions. Placement/proportions come from the approved art.
    glyphs = {
        "B": (148, "M8 0H102C132 0 148 17 148 42V57C148 72 141 77 130 81C142 84 148 97 148 112V122C148 147 130 161 105 161H8Q0 161 0 153V8Q0 0 8 0Z M40 39H95Q105 39 105 48V50Q105 58 95 58H40Z M40 101H95Q105 101 105 110V112Q105 122 95 122H40Z"),
        "E": (136, "M25 0H130Q136 0 136 6V33Q136 40 129 40H45Q41 40 41 44V58Q41 62 45 62H124V99H45Q41 99 41 103V117Q41 121 45 121H129Q136 121 136 128V155Q136 161 129 161H25Q0 161 0 136V25Q0 0 25 0Z"),
        "A": (171, "M0 161L59 7Q62 0 69 0H103Q111 0 114 7L171 161H128L113 126H58L43 161Z M85.5 55L64 96H107Z"),
        "T": (134, "M6 0H128Q134 0 134 6V34Q134 40 128 40H88V155Q88 161 82 161H52Q46 161 46 155V40H6Q0 40 0 34V6Q0 0 6 0Z"),
        "S": (146, "M42 0H140Q146 0 146 6V34Q146 40 140 40H49Q41 40 41 48Q41 56 49 56H104C133 56 146 72 146 100V118C146 145 129 161 104 161H6Q0 161 0 155V127Q0 121 6 121H97Q105 121 105 113Q105 105 97 105H42C13 105 0 89 0 64V42C0 19 19 0 42 0Z"),
        "I": (42, "M5 0H37Q42 0 42 5V156Q42 161 37 161H5Q0 161 0 156V5Q0 0 5 0Z"),
        "C": (138, "M36 0H132Q138 0 138 6V34Q138 40 132 40H50Q41 40 41 49V112Q41 121 50 121H132Q138 121 138 127V155Q138 161 132 161H36Q0 161 0 125V36Q0 0 36 0Z"),
        "H": (139, "M6 0H36Q42 0 42 6V59H97V6Q97 0 103 0H133Q139 0 139 6V155Q139 161 133 161H103Q97 161 97 155V102H42V155Q42 161 36 161H6Q0 161 0 155V6Q0 0 6 0Z"),
    }
    bounds_by_letter = []
    for points, area in loops:
        if area > 1000:
            bounds_by_letter.append((min(x for x, y in points), min(y for x, y in points),
                                     max(x for x, y in points), max(y for x, y in points)))
    bounds_by_letter.sort()
    if len(bounds_by_letter) != 10:
        raise ValueError("Expected all ten complete BEATSTITCH letter silhouettes")
    group = ET.Element(tag("g"), {"id": "wordmark", "fill": "#F6F3E9", "fill-rule": "evenodd"})
    for index, (letter, (gx1, gy1, gx2, gy2)) in enumerate(zip("BEATSTITCH", bounds_by_letter)):
        width, path = glyphs[letter]
        ET.SubElement(group, tag("path"), {"id": f"letter-{letter.lower()}-{index + 1}", "d": path,
            "transform": f"translate({gx1:g} {gy1:g}) scale({(gx2 - gx1) / width:g} {(gy2 - gy1) / 161:g})"})
    stats = {"source_bounds": bounds, "outer_contours": sum(a > 0 for _, a in loops),
             "counter_contours": sum(a < 0 for _, a in loops),
             "lettering": "clean geometric outlines rebuilt against the approved proportions",
             "outlined_glyphs": len(bounds_by_letter)}
    return group, x2 - x1, y2 - y1, stats


def document(width, height, background=None, description=""):
    root = ET.Element(tag("svg"), {"viewBox": f"0 0 {width:g} {height:g}",
                                   "width": f"{width:g}", "height": f"{height:g}",
                                   "role": "img", "aria-labelledby": "logo-title logo-description"})
    ET.SubElement(root, tag("title"), {"id": "logo-title"}).text = "Beatstitch"
    ET.SubElement(root, tag("desc"), {"id": "logo-description"}).text = description
    if background:
        ET.SubElement(root, tag("rect"), {"width": "100%", "height": "100%", "fill": background})
    return root


def add_symbol(root, symbol, x, y, size):
    sx, sy, sw, sh = map(float, symbol.get("viewBox", "0 0 512 512").split())
    if sw != sh:
        raise ValueError("Emblem must have a square viewBox")
    group = ET.SubElement(root, tag("g"), {"id": "emblem",
        "transform": f"translate({x:g} {y:g}) scale({size / sw:g}) translate({-sx:g} {-sy:g})"})
    for child in symbol:
        if child.tag not in (tag("title"), tag("desc"), tag("metadata")):
            group.append(deepcopy(child))


def add_wordmark(root, wordmark, x, y, scale, color):
    group = deepcopy(wordmark)
    group.set("fill", color)
    group.set("transform", f"translate({x:g} {y:g}) scale({scale:g})")
    root.append(group)


def save(root, destination):
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] in {"image", "script", "foreignObject", "text"}:
            raise ValueError("Export contains a raster, script, or font dependency")
        for key, value in node.attrib.items():
            if "data:image" in value or key.endswith("href") and not value.startswith("#"):
                raise ValueError("Export contains an external resource")
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wordmark-source", type=Path, required=True)
    parser.add_argument("--symbol", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--wordmark-only", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    letters, tw, th, stats = outlined_wordmark(args.wordmark_source)
    root = document(tw + 64, th + 64, description="Outlined Beatstitch wordmark; no font installation required.")
    add_wordmark(root, letters, 32, 32, 1, "#F6F3E9")
    save(root, args.output_dir / "beatstitch-wordmark.svg")
    if args.wordmark_only:
        print(json.dumps(stats, indent=2))
        return
    if not args.symbol:
        parser.error("--symbol is required for full lockups")
    symbol = ET.parse(args.symbol).getroot()
    description = "Purple and orange spliced vinyl grooves beside the outlined Beatstitch wordmark."
    for layout in ("horizontal", "stacked"):
        for suffix, background, color in (
            ("", None, "#F6F3E9"), ("-light", None, "#12131A"), ("-dark", "#101116", "#F6F3E9")
        ):
            if layout == "horizontal":
                canvas = document(2000, 600, background, description)
                add_symbol(canvas, symbol, 64, 64, 472)
                scale = 1370 / tw
                add_wordmark(canvas, letters, 582, (600 - th * scale) / 2, scale, color)
            else:
                canvas = document(1600, 1200, background, description.replace("beside", "above"))
                add_symbol(canvas, symbol, 480, 70, 640)
                scale = 1440 / tw
                add_wordmark(canvas, letters, 80, 770, scale, color)
            save(canvas, args.output_dir / f"beatstitch-{layout}{suffix}.svg")
    (args.output_dir / "vector-export.json").write_text(json.dumps({
        "format": "standalone SVG paths and gradients", "outlined_wordmark": stats,
        "font_dependencies": [], "raster_dependencies": [],
        "transparent_variants": ["horizontal", "stacked", "horizontal-light", "stacked-light"]
    }, indent=2) + "\n")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
