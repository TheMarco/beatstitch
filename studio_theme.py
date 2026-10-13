"""Visual system for DJ Studio: palette, type, stylesheet and drawn icons.

The interface follows the conventions of arrangement-based DAWs such as Ableton
Live: flat gray panels separated by dark gutters, compact controls, value boxes
you drag, and a color per song. Orange marks switched-on states and the current
selection. Green and red only report harmonic match or clash.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette,
                           QPen, QPixmap)

# Surfaces. Gutters sit between panels; fields are the darkest insets.
GUTTER = "#0E0E0E"
BASE = "#212121"
TITLE = "#2A2A2A"
LANE = "#1A1A1A"
LANE_ALT = "#1E1E1E"
FIELD = "#141414"
INK = LANE
RAISED = "#343434"
HOVER = "#3E3E3E"
PRESSED = "#4A4A4A"
LINE = "#2C2C2C"
LINE_STRONG = "#454545"
EDGE_HOVER = "#5A5A5A"

TEXT = "#DCDCDC"
TEXT_2 = "#BFBFBF"
TEXT_3 = "#B8B8B8"
DISABLED_TEXT = "#737373"

ACCENT = "#FFA234"
ACCENT_HOVER = "#FFB65E"
ACCENT_PRESSED = "#E88E22"
ON_ACCENT = "#1A1206"
PLAYING = "#5FD36B"

# Neutral waveform shades for places without a song color.
LOW = "#7D8796"
MID = "#AEB7C4"
HIGH = "#E6EAF0"
BANDS = (LOW, MID, HIGH)

GOOD = "#6FD08C"
BAD = "#FF6B5E"
BAD_TEXT = "#FFA99F"
SELECTION = "#3D4A60"

# One color per song, stable across sessions. Light, saturated, readable with dark text.
CLIP_COLORS = ("#FF8F8F", "#FFB35C", "#E9D45D", "#A8DF5E", "#5ED8A2", "#5CCBEA", "#8FA2FF", "#CF8FFF",
               "#FF8FC8", "#C9B38A")

SIZE_SMALL, SIZE_META, SIZE_BODY, SIZE_HEADING, SIZE_TITLE, SIZE_READOUT = 11, 11, 12, 13, 15, 16

# Barlow (SIL Open Font License, see assets/fonts/OFL.txt): a DIN-style grotesque,
# the lettering of mixer panels and rack gear. The platform face is the fallback.
FONT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / "fonts"

_family = None
_reduced_motion = None
_asset_dir = None


def _load_fonts():
    families = set()
    for path in sorted(FONT_DIR.glob("*.ttf")):
        # Qt only accepts absolute paths here.
        identity = QFontDatabase.addApplicationFont(str(path.resolve()))
        if identity >= 0:
            families.update(QFontDatabase.applicationFontFamilies(identity))
    return families


def family() -> str:
    """Barlow when bundled, otherwise the platform UI face (SF Pro on macOS, Segoe UI on Windows)."""
    global _family
    if _family is None and "Barlow" in _load_fonts():
        _family = "Barlow"
    if _family is None:
        system = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        available = set(QFontDatabase.families())
        # Headless/offscreen platforms report a generic alias; prefer a real UI face.
        preferred = [system] if system not in ("Sans Serif", "") else []
        preferred += ["SF Pro Text", "SF Pro", "Segoe UI Variable Text", "Segoe UI", "Inter", "Cantarell"]
        _family = next((name for name in preferred if name in available or name == system), system)
    return _family


def font(pixels: int = SIZE_BODY, weight: QFont.Weight = QFont.Weight.Normal, tabular: bool = False) -> QFont:
    result = QFont(family())
    result.setPixelSize(pixels)
    result.setWeight(weight)
    if tabular:
        result.setFeature(QFont.Tag("tnum"), 1)
    return result


def reduced_motion() -> bool:
    global _reduced_motion
    if _reduced_motion is None:
        _reduced_motion = False
        if sys.platform == "darwin":
            try:
                value = subprocess.run(["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                                       capture_output=True, text=True, timeout=2).stdout.strip()
                _reduced_motion = value == "1"
            except (OSError, subprocess.SubprocessError):
                pass
    return _reduced_motion


def color(value, alpha: float = 1.0) -> QColor:
    result = QColor(value)
    result.setAlphaF(alpha)
    return result


def mix(first, second, amount: float) -> QColor:
    a, b = QColor(first), QColor(second)
    return QColor(round(a.red() + (b.red() - a.red()) * amount), round(a.green() + (b.green() - a.green()) * amount),
                  round(a.blue() + (b.blue() - a.blue()) * amount))


def clip_color(identity: str) -> str:
    try:
        return CLIP_COLORS[int(identity[:8], 16) % len(CLIP_COLORS)]
    except (ValueError, TypeError):
        return CLIP_COLORS[sum(map(ord, str(identity))) % len(CLIP_COLORS)]


def clip_bands(base) -> tuple:
    """Waveform shades of a song color: deep lows, true mids, pale highs."""
    return mix(base, "#000000", .38), QColor(base), mix(base, "#FFFFFF", .62)


# Icons are drawn on a 24-unit grid with a 1.7-unit stroke, at twice the display size.

def _pen(painter, ink, width=1.7):
    pen = QPen(ink, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _draw(name, painter, ink):
    p = painter
    if name == "play":
        path = QPainterPath(QPointF(8, 5.5))
        path.lineTo(18.5, 12); path.lineTo(8, 18.5); path.closeSubpath()
        p.setPen(QPen(ink, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(ink); p.drawPath(path)
    elif name == "pause":
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(ink)
        p.drawRoundedRect(QRectF(7, 5.5, 3.6, 13), 1.2, 1.2)
        p.drawRoundedRect(QRectF(13.4, 5.5, 3.6, 13), 1.2, 1.2)
    elif name == "stop":
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(ink)
        p.drawRoundedRect(QRectF(7, 7, 10, 10), 2, 2)
    elif name == "rewind":
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(ink)
        p.drawRoundedRect(QRectF(6, 6.5, 2.2, 11), 1, 1)
        path = QPainterPath(QPointF(18, 6.5)); path.lineTo(9.5, 12); path.lineTo(18, 17.5); path.closeSubpath()
        p.drawPath(path)
    elif name in ("chevron-left", "chevron-right", "chevron-down"):
        _pen(p, ink, 1.8)
        if name == "chevron-left":
            p.drawPolyline([QPointF(14.5, 6.5), QPointF(9, 12), QPointF(14.5, 17.5)])
        elif name == "chevron-right":
            p.drawPolyline([QPointF(9.5, 6.5), QPointF(15, 12), QPointF(9.5, 17.5)])
        else:
            p.drawPolyline([QPointF(6.5, 9.5), QPointF(12, 15), QPointF(17.5, 9.5)])
    elif name == "search":
        _pen(p, ink)
        p.drawEllipse(QPointF(10.5, 10.5), 5.5, 5.5)
        p.drawLine(QPointF(14.6, 14.6), QPointF(19, 19))
    elif name == "plus":
        _pen(p, ink)
        p.drawLine(QPointF(12, 5.5), QPointF(12, 18.5)); p.drawLine(QPointF(5.5, 12), QPointF(18.5, 12))
    elif name == "minus":
        _pen(p, ink)
        p.drawLine(QPointF(5.5, 12), QPointF(18.5, 12))
    elif name == "folder":
        _pen(p, ink)
        path = QPainterPath(QPointF(4, 7.5))
        path.quadTo(4, 5.5, 6, 5.5); path.lineTo(9.5, 5.5); path.lineTo(11.5, 7.5); path.lineTo(18, 7.5)
        path.quadTo(20, 7.5, 20, 9.5); path.lineTo(20, 16.5); path.quadTo(20, 18.5, 18, 18.5)
        path.lineTo(6, 18.5); path.quadTo(4, 18.5, 4, 16.5); path.closeSubpath()
        p.drawPath(path)
    elif name == "export":
        _pen(p, ink)
        p.drawLine(QPointF(12, 4.5), QPointF(12, 14.5))
        p.drawPolyline([QPointF(8, 10.5), QPointF(12, 14.5), QPointF(16, 10.5)])
        p.drawPolyline([QPointF(5, 14), QPointF(5, 18.5), QPointF(19, 18.5), QPointF(19, 14)])
    elif name == "speaker":
        _pen(p, ink, 1.6)
        path = QPainterPath(QPointF(4.5, 9.5))
        path.lineTo(8, 9.5); path.lineTo(12, 6); path.lineTo(12, 18); path.lineTo(8, 14.5)
        path.lineTo(4.5, 14.5); path.closeSubpath()
        p.drawPath(path)
        p.drawArc(QRectF(10, 8, 6, 8), -60 * 16, 120 * 16)
        p.drawArc(QRectF(10, 5, 10, 14), -60 * 16, 120 * 16)
    elif name == "arrow-left":
        _pen(p, ink)
        p.drawLine(QPointF(19, 12), QPointF(5.5, 12)); p.drawPolyline([QPointF(10.5, 7), QPointF(5.5, 12), QPointF(10.5, 17)])
    elif name == "arrow-right":
        _pen(p, ink)
        p.drawLine(QPointF(5, 12), QPointF(18.5, 12)); p.drawPolyline([QPointF(13.5, 7), QPointF(18.5, 12), QPointF(13.5, 17)])
    elif name == "fit":
        _pen(p, ink)
        p.drawLine(QPointF(4.5, 12), QPointF(19.5, 12))
        p.drawPolyline([QPointF(8, 8.5), QPointF(4.5, 12), QPointF(8, 15.5)])
        p.drawPolyline([QPointF(16, 8.5), QPointF(19.5, 12), QPointF(16, 15.5)])
        p.drawLine(QPointF(3, 5), QPointF(3, 19)); p.drawLine(QPointF(21, 5), QPointF(21, 19))
    elif name == "close":
        _pen(p, ink)
        p.drawLine(QPointF(7, 7), QPointF(17, 17)); p.drawLine(QPointF(17, 7), QPointF(7, 17))
    elif name == "check":
        _pen(p, ink, 2.4)
        p.drawPolyline([QPointF(6, 12.5), QPointF(10, 16.5), QPointF(18, 7.5)])
    elif name == "trash":
        _pen(p, ink, 1.6)
        p.drawLine(QPointF(5, 7), QPointF(19, 7))
        p.drawPolyline([QPointF(9.5, 7), QPointF(9.5, 4.8), QPointF(14.5, 4.8), QPointF(14.5, 7)])
        path = QPainterPath(QPointF(6.8, 7)); path.lineTo(7.8, 19); path.lineTo(16.2, 19); path.lineTo(17.2, 7)
        p.drawPath(path)
    elif name == "order":
        _pen(p, ink, 1.6)
        for y, width in ((7, 14), (12, 10), (17, 6)):
            p.drawLine(QPointF(5, y), QPointF(5 + width, y))
    else:
        raise KeyError(name)


def pixmap(name: str, ink: str = TEXT, size: int = 16, alpha: float = 1.0) -> QPixmap:
    scale = 2
    result = QPixmap(size * scale, size * scale)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size * scale / 24, size * scale / 24)
    _draw(name, painter, color(ink, alpha))
    painter.end()
    result.setDevicePixelRatio(scale)
    return result


def icon(name: str, ink: str = TEXT_2, size: int = 16, active: str | None = None) -> QIcon:
    result = QIcon()
    result.addPixmap(pixmap(name, ink, size), QIcon.Mode.Normal)
    result.addPixmap(pixmap(name, active or TEXT, size), QIcon.Mode.Active)
    result.addPixmap(pixmap(name, DISABLED_TEXT, size, .45), QIcon.Mode.Disabled)
    return result


def _asset(name, ink, size=16):
    """Stylesheets need image files; draw them once per session."""
    global _asset_dir
    if _asset_dir is None:
        _asset_dir = Path(tempfile.mkdtemp(prefix="dj-studio-ui-"))
    path = _asset_dir / f"{name}-{ink.strip('#')}.png"
    if not path.exists():
        pixmap(name, ink, size).toImage().save(str(path))
    return path.as_posix()


def palette() -> QPalette:
    result = QPalette()
    roles = {
        QPalette.ColorRole.Window: BASE, QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: RAISED, QPalette.ColorRole.AlternateBase: BASE,
        QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: RAISED,
        QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.Highlight: SELECTION,
        QPalette.ColorRole.HighlightedText: TEXT, QPalette.ColorRole.ToolTipBase: HOVER,
        QPalette.ColorRole.ToolTipText: TEXT, QPalette.ColorRole.PlaceholderText: TEXT_3,
        QPalette.ColorRole.Link: ACCENT, QPalette.ColorRole.BrightText: TEXT,
        QPalette.ColorRole.Light: LINE_STRONG, QPalette.ColorRole.Midlight: LINE_STRONG,
        QPalette.ColorRole.Mid: LINE, QPalette.ColorRole.Dark: INK, QPalette.ColorRole.Shadow: INK,
    }
    for role, value in roles.items():
        result.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.PlaceholderText):
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED_TEXT))
    return result


def stylesheet() -> str:
    chevron = _asset("chevron-down", TEXT_2, 12)
    check = _asset("check", ON_ACCENT, 14)
    tick = _asset("check", ACCENT, 14)
    return f"""
QMainWindow, QDialog {{ background: {GUTTER}; }}
QDialog {{ background: {BASE}; }}
QWidget {{ color: {TEXT}; }}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{ placeholder-text-color: {TEXT_3}; }}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {{
    placeholder-text-color: {DISABLED_TEXT};
}}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: {SIZE_TITLE}px; font-weight: 600; }}
QLabel[role="heading"] {{ font-size: {SIZE_HEADING}px; font-weight: 600; }}
QLabel[role="boxTitle"] {{ font-size: 12px; font-weight: 600; color: {TEXT_2}; }}
QLabel[role="section"] {{ font-weight: 600; color: {TEXT}; }}
QLabel[role="secondary"] {{ color: {TEXT_2}; }}
QLabel[role="small"] {{ color: {TEXT_2}; font-size: {SIZE_SMALL}px; }}
QLabel[role="hint"] {{ color: {TEXT_3}; font-size: {SIZE_SMALL}px; }}
QLabel[role="field"] {{ color: {TEXT_2}; font-size: {SIZE_SMALL}px; }}
QLabel[role="readout"] {{ font-size: {SIZE_READOUT}px; font-weight: 500; }}
QLabel[role="lcd"] {{ background: {FIELD}; border: 1px solid {LINE_STRONG}; border-radius: 2px; padding: 1px 8px;
                      font-size: 15px; font-weight: 500; }}

QFrame#panel {{ background: {BASE}; border-radius: 4px; }}
QFrame#controlBar {{ background: {BASE}; border-radius: 4px; }}
QFrame#statusBar {{ background: {GUTTER}; }}
QFrame#box {{ background: {TITLE}; border-radius: 3px; }}
QFrame#boxTitle {{ background: {RAISED}; border-top-left-radius: 3px; border-top-right-radius: 3px; }}
QFrame#divider {{ background: {LINE}; max-height: 1px; min-height: 1px; border: 0; }}
QFrame#notice {{ background: {TITLE}; border: 1px solid {LINE_STRONG}; border-radius: 3px; }}
QFrame#notice[tone="error"] {{ border-color: {BAD}; }}

QPushButton {{ background: {RAISED}; color: {TEXT}; border: 1px solid {GUTTER}; border-radius: 2px;
              padding: 3px 9px; font-size: 12px; min-height: 16px; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: {PRESSED}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {DISABLED_TEXT}; background: {TITLE}; }}
QPushButton:checked {{ background: {ACCENT}; color: {ON_ACCENT}; border-color: {ACCENT_PRESSED}; }}
QPushButton:checked:hover {{ background: {ACCENT_HOVER}; }}
QPushButton:checked:disabled {{ background: #6B532F; color: #2A2116; }}
QPushButton[role="primary"] {{ background: {ACCENT}; color: {ON_ACCENT}; border-color: {ACCENT_PRESSED}; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[role="primary"]:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton[role="primary"]:disabled {{ background: {TITLE}; color: {DISABLED_TEXT}; border-color: {GUTTER}; }}
QPushButton[role="ghost"] {{ background: transparent; border-color: transparent; color: {TEXT_2}; }}
QPushButton[role="ghost"]:hover {{ background: {HOVER}; color: {TEXT}; }}
QPushButton[role="ghost"]:disabled {{ color: {DISABLED_TEXT}; }}
QPushButton[role="danger"] {{ color: {BAD_TEXT}; }}
QPushButton[role="danger"]:disabled {{ color: {DISABLED_TEXT}; }}

QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 2px; padding: 2px; }}
QToolButton:hover {{ background: {HOVER}; }}
QToolButton:pressed {{ background: {PRESSED}; }}
QToolButton:checked {{ background: {PRESSED}; }}
QToolButton:focus {{ border-color: {ACCENT}; }}
QToolButton[role="transport"] {{ background: {RAISED}; border: 1px solid {GUTTER}; }}
QToolButton[role="transport"]:hover {{ background: {HOVER}; }}
QToolButton[role="transport"]:checked {{ background: {RAISED}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QLineEdit {{ background: {FIELD}; border: 1px solid {LINE_STRONG}; border-radius: 2px; padding: 3px 6px;
            selection-background-color: {SELECTION}; font-size: 12px; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}

QComboBox {{ background: {FIELD}; border: 1px solid {LINE_STRONG}; border-radius: 2px; padding: 2px 22px 2px 6px;
            min-height: 16px; font-size: 12px; }}
QComboBox:hover {{ border-color: {EDGE_HOVER}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox:disabled {{ color: {DISABLED_TEXT}; border-color: {LINE}; }}
QComboBox::drop-down {{ border: 0; width: 18px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 9px; height: 9px; }}
QComboBox QAbstractItemView {{ background: {TITLE}; border: 1px solid {LINE_STRONG}; padding: 2px; outline: 0;
                              selection-background-color: {SELECTION}; selection-color: {TEXT}; }}

QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid {LINE_STRONG}; border-radius: 2px; background: {FIELD}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{check}"); }}

QSlider::groove:horizontal {{ height: 3px; background: {FIELD}; border: 1px solid {LINE_STRONG}; }}
QSlider::sub-page:horizontal {{ background: {TEXT_2}; }}
QSlider::handle:horizontal {{ width: 8px; height: 14px; margin: -6px 0; border-radius: 1px; background: {TEXT}; }}

QScrollArea {{ background: transparent; border: 0; }}
QScrollBar:horizontal {{ height: 9px; background: {LANE}; margin: 0; }}
QScrollBar:vertical {{ width: 9px; background: {BASE}; margin: 0; }}
QScrollBar::handle {{ background: {LINE_STRONG}; border-radius: 2px; min-width: 28px; min-height: 28px; margin: 2px; }}
QScrollBar::handle:hover {{ background: {EDGE_HOVER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QTableView {{ background: {BASE}; border: 0; outline: 0; gridline-color: transparent;
             selection-background-color: {SELECTION}; selection-color: {TEXT}; }}
QHeaderView {{ background: {TITLE}; border: 0; }}
QHeaderView::section {{ background: {TITLE}; color: {TEXT_2}; border: 0; border-right: 1px solid {BASE};
                       padding: 3px 6px; font-size: 11px; font-weight: 600; }}
QHeaderView::section:hover {{ color: {TEXT}; }}
QSplitter::handle {{ background: {GUTTER}; }}

QProgressBar {{ background: {FIELD}; border: 0; max-height: 3px; min-height: 3px; }}
QProgressBar::chunk {{ background: {ACCENT}; }}

QMenuBar {{ background: {GUTTER}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {HOVER}; }}
QMenu {{ background: {TITLE}; border: 1px solid {LINE_STRONG}; padding: 4px; }}
QMenu::item {{ padding: 4px 24px 4px 10px; }}
QMenu::item:selected {{ background: {SELECTION}; }}
QMenu::item:disabled {{ color: {DISABLED_TEXT}; }}
QMenu::indicator {{ width: 13px; height: 13px; }}
QMenu::indicator:checked {{ image: url("{tick}"); }}
QMenu::separator {{ height: 1px; background: {LINE_STRONG}; margin: 4px 2px; }}
QToolTip {{ background: {TITLE}; color: {TEXT}; border: 1px solid {LINE_STRONG}; padding: 4px 6px; }}
"""
