"""Replace paint only in the supplied logo; preserve all original geometry."""
from pathlib import Path
import copy
import hashlib
import json
import xml.etree.ElementTree as ET

from PySide6.QtCore import QByteArray
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "assets/branding/beatstitch/refined"
SOURCE = FOLDER / "friend-original.svg"
OUTPUT = FOLDER / "beatstitch-refined.svg"
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
tree = ET.parse(SOURCE)
root = tree.getroot()
original = copy.deepcopy(root)
measure = copy.deepcopy(root)
for i, element in enumerate(measure.iter()):
    element.set("id", f"measure-{i}")
renderer = QSvgRenderer(QByteArray(ET.tostring(measure)))
bounds = {}
for i, element in enumerate(root.iter()):
    bounds[id(element)] = renderer.boundsOnElement(f"measure-{i}")

defs = ET.Element(f"{{{NS}}}defs")


def gradient(name, colors, **attrs):
    kind = attrs.pop("kind", "linearGradient")
    grad = ET.SubElement(defs, f"{{{NS}}}{kind}", {
        "id": name, "gradientUnits": "userSpaceOnUse", **attrs,
    })
    for position, color in colors:
        ET.SubElement(grad, f"{{{NS}}}stop", {
            "offset": str(position), "stop-color": color,
        })


gradient("purple-enamel", [(0, "#f1c2ff"), (.10, "#df8cff"),
         (.22, "#b63dff"), (.36, "#8a0aff"), (.56, "#5800ee"),
         (.78, "#7400f4"), (1, "#a621ff")],
         kind="radialGradient", cx="0", cy="0", r="2",
         gradientTransform="matrix(145 122 -83 99 257 221)")
gradient("amber-enamel", [(0, "#fff5b8"), (.07, "#ffe96d"),
         (.15, "#ffd22d"), (.26, "#ffb20b"), (.40, "#f78400"),
         (.60, "#ff9900"), (.78, "#ffd13b"), (1, "#fff0ac")],
         kind="radialGradient", cx="0", cy="0", r="2",
         gradientTransform="matrix(153 -154 104 99 473 484)")
gradient("purple-rim", [(0, "#fff0ff"), (.40, "#e39bff"),
         (.75, "#b745ff"), (1, "#9b20fc")], x1="210", y1="160", x2="310", y2="550")
gradient("amber-rim", [(0, "#fff8b0"), (.5, "#ffd22b"),
         (1, "#ffbc1e")], x1="470", y1="174", x2="390", y2="580")
gradient("purple-cut", [(0, "#29075c"), (.52, "#170533"),
         (1, "#370576")], x1="210", y1="180", x2="320", y2="440")
gradient("amber-cut", [(0, "#763307"), (.55, "#4c1c05"),
         (1, "#763005")], x1="515", y1="260", x2="430", y2="540")
gradient("purple-edge", [(0, "#6000dd"), (1, "#330773")],
         x1="150", y1="200", x2="260", y2="550")
gradient("amber-edge", [(0, "#a74900"), (.5, "#783000"),
         (1, "#a34100")], x1="600", y1="200", x2="340", y2="585")
for name, x, y in [("upper", 376, 246), ("lower", 289, 450)]:
    for suffix, colors in [
        ("stitch", [(0, "#c570ff"), (.23, "#dc8dfa"), (.5, "#f6b4b4"),
                    (.73, "#ffcc57"), (1, "#ffba17")]),
        ("stitch-rim", [(0, "#f1caff"), (.4, "#ffe2db"), (1, "#fff2a1")]),
    ]:
        gradient(f"{name}-{suffix}", colors,
                 x1=str(x), y1=str(y), x2=str(x+73), y2=str(y+37))

# Original root-level path numbers identify existing rim/groove geometry.
purple_rim = {3, 4, 7, 8, 90, 97, 100, 128}
amber_rim = {12, 14, 46, 66, 72, 96, 133}
purple_cuts = {16, 24, 26, 45, 51, 77, 93}
amber_cuts = {15, 49, 69, 82, 83, 98, 103, 106, 108, 111, 129, 136}
amber_edges = {10, 19, 27, 140}
upper = {29, 30, 31, 36, 37, 38, 39, 41, 42, 44, 47}
lower = {114, 116, 117, 119, 120, 121, 124, 130}


def paint(name):
    return f"url(#{name})"


for i, element in enumerate(list(root)):
    if not element.tag.endswith("path"):
        continue
    color = element.get("fill", "")
    if color == "#101114" or 54 <= i <= 63:
        continue  # Original background, negative space, and lettering.
    if i in upper | lower:
        prefix = "upper" if i in upper else "lower"
        suffix = "stitch-rim" if i in {29, 114} else "stitch"
        target = f"{prefix}-{suffix}"
    elif i in purple_rim:
        target = "purple-rim"
    elif i in amber_rim:
        target = "amber-rim"
    elif i in purple_cuts:
        target = "purple-cut"
    elif i in amber_cuts:
        target = "amber-cut"
    elif i in amber_edges:
        target = "amber-edge"
    elif i == 75:
        target = "purple-edge"
    else:
        b = bounds[id(element)]
        target = "purple-enamel" if b.center().x() + .575*b.center().y() < 560 else "amber-enamel"
    element.set("fill", paint(target))

# These pre-existing strokes cover seams between the traced color regions.
# Give them the same continuous paint field as the surfaces above them.
for element in root.iter():
    if element.get("stroke") is None:
        continue
    b = bounds[id(element)]
    if b.x() > 640:
        continue
    if b.x() >= 365 and b.right() <= 463 and b.y() >= 230 and b.bottom() <= 294:
        target = "upper-stitch"
    elif b.x() >= 275 and b.right() <= 375 and b.y() >= 431 and b.bottom() <= 496:
        target = "lower-stitch"
    else:
        target = "purple-enamel" if b.center().x()+.575*b.center().y() < 560 else "amber-enamel"
    element.set("stroke", paint(target))


def structure(element):
    """Only paint values may differ; paths and every other attribute must match."""
    return (element.tag, {k: v for k, v in element.attrib.items() if k not in {"fill", "stroke"}},
            [structure(child) for child in element])


assert structure(root) == structure(original), "Geometry or document structure changed"
root.insert(0, defs)
tree.write(OUTPUT, encoding="utf-8", xml_declaration=True)
print(OUTPUT)
print(json.dumps({"original_paths": sum(e.tag.endswith("path") for e in original.iter()),
                  "geometry_unchanged": True,
                  "original_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest()}, indent=2))
