"""Visual system for DJ Studio: palette, type, stylesheet and drawn icons.

Color carries meaning. Cool blues are sound (waveforms). Amber marks things the
user acts on: play, preview, the selection, cue handles. Mint and coral only
report harmonic match or clash.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette,
                           QPen, QPixmap)

# Surfaces, darkest first. The stage (timeline) sits below the panes so waveforms read first.
INK = "#0A0C18"
BASE = "#10121F"
RAISED = "#171A2C"
HOVER = "#1F2338"
PRESSED = "#272C46"
LINE = "#1E2236"
LINE_STRONG = "#2E3350"
EDGE_HOVER = "#3C4266"

TEXT = "#E8EAF4"
TEXT_2 = "#A2A7C2"
TEXT_3 = "#7A80A0"

ACCENT = "#FFB547"
ACCENT_HOVER = "#FFC46E"
ACCENT_PRESSED = "#EBA13A"
ON_ACCENT = "#1B1306"

# Waveform bands, low to high.
LOW = "#3D63FF"
MID = "#33C3E8"
HIGH = "#E4F4FF"
BANDS = (LOW, MID, HIGH)

GOOD = "#4FD1A1"
BAD = "#FF7A6B"
SELECTION = "#33395E"

SIZE_SMALL, SIZE_META, SIZE_BODY, SIZE_HEADING, SIZE_TITLE, SIZE_READOUT = 12, 13, 14, 16, 20, 30

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


def color(value: str, alpha: float = 1.0) -> QColor:
    result = QColor(value)
    result.setAlphaF(alpha)
    return result


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
    result.addPixmap(pixmap(name, TEXT_3, size, .45), QIcon.Mode.Disabled)
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
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(TEXT_3))
    return result


def stylesheet() -> str:
    chevron = _asset("chevron-down", TEXT_2, 12)
    check = _asset("check", ON_ACCENT, 14)
    tick = _asset("check", ACCENT, 14)
    return f"""
QMainWindow, QDialog {{ background: {BASE}; }}
QWidget {{ color: {TEXT}; }}
QLabel {{ background: transparent; }}
QLabel[role="readout"] {{ font-size: {SIZE_READOUT}px; font-weight: 500; }}
QLabel[role="small"] {{ color: {TEXT_2}; font-size: {SIZE_META}px; }}
QLabel[role="title"] {{ font-size: {SIZE_TITLE}px; font-weight: 600; }}
QLabel[role="heading"] {{ font-size: {SIZE_HEADING}px; font-weight: 600; }}
QLabel[role="section"] {{ font-size: {SIZE_BODY}px; font-weight: 600; color: {TEXT}; }}
QLabel[role="secondary"] {{ color: {TEXT_2}; }}
QLabel[role="hint"] {{ color: {TEXT_3}; font-size: {SIZE_META}px; }}
QLabel[role="good"] {{ color: {GOOD}; }}
QLabel[role="bad"] {{ color: {BAD}; }}

QFrame#topBar, QFrame#statusBar {{ background: {BASE}; }}
QFrame#topBar {{ border-bottom: 1px solid {LINE}; }}
QFrame#statusBar {{ border-top: 1px solid {LINE}; }}
QFrame#pane {{ background: {BASE}; }}
QFrame#stage {{ background: {INK}; }}
QFrame#transitionBar {{ background: {BASE}; border-top: 1px solid {LINE}; }}
QFrame#divider {{ background: {LINE}; max-height: 1px; min-height: 1px; border: 0; }}
QFrame#notice {{ background: {RAISED}; border: 1px solid {LINE_STRONG}; border-radius: 8px; }}
QFrame#notice[tone="error"] {{ border-color: {BAD}; }}

QPushButton {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE_STRONG}; border-radius: 6px;
              padding: 6px 12px; font-weight: 500; min-height: 18px; }}
QPushButton:hover {{ background: {HOVER}; border-color: {EDGE_HOVER}; }}
QPushButton:pressed {{ background: {PRESSED}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {TEXT_3}; background: {BASE}; border-color: {LINE}; }}
QPushButton[role="primary"] {{ background: {ACCENT}; color: {ON_ACCENT}; border-color: {ACCENT}; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[role="primary"]:pressed {{ background: {ACCENT_PRESSED}; border-color: {ACCENT_PRESSED}; }}
QPushButton[role="primary"]:focus {{ border-color: {HIGH}; }}
QPushButton[role="primary"]:disabled {{ background: {RAISED}; color: {TEXT_3}; border-color: {LINE}; }}
QPushButton[role="ghost"] {{ background: transparent; border-color: transparent; color: {TEXT_2}; }}
QPushButton[role="ghost"]:hover {{ background: {HOVER}; color: {TEXT}; }}
QPushButton[role="ghost"]:disabled {{ color: {TEXT_3}; background: transparent; }}
QPushButton[role="danger"] {{ background: transparent; border-color: transparent; color: {BAD}; }}
QPushButton[role="danger"]:hover {{ background: rgba(255, 122, 107, 0.12); }}
QPushButton[role="danger"]:disabled {{ color: {TEXT_3}; background: transparent; }}
QPushButton:checked {{ background: {ACCENT}; color: {ON_ACCENT}; border-color: {ACCENT}; }}

QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: {HOVER}; }}
QToolButton:pressed {{ background: {PRESSED}; }}
QToolButton:focus {{ border-color: {ACCENT}; }}
QToolButton:checked {{ background: {PRESSED}; }}
QToolButton#play {{ background: {ACCENT}; border: 0; border-radius: 20px; }}
QToolButton#play:hover {{ background: {ACCENT_HOVER}; }}
QToolButton#play:pressed {{ background: {ACCENT_PRESSED}; }}
QToolButton#play:focus {{ border: 2px solid {HIGH}; }}
QToolButton#play:disabled {{ background: {RAISED}; }}

QLineEdit {{ background: {RAISED}; border: 1px solid {LINE}; border-radius: 6px; padding: 6px 8px;
            selection-background-color: {SELECTION}; }}
QLineEdit:hover {{ border-color: {LINE_STRONG}; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}

QAbstractSpinBox {{ background: {RAISED}; border: 1px solid {LINE_STRONG}; border-radius: 6px; padding: 5px 8px;
                   selection-background-color: {SELECTION}; min-height: 18px; }}
QAbstractSpinBox:hover {{ border-color: {EDGE_HOVER}; }}
QAbstractSpinBox:focus {{ border-color: {ACCENT}; }}
QAbstractSpinBox:disabled {{ color: {TEXT_3}; background: {BASE}; border-color: {LINE}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: 0; }}
QAbstractSpinBox[role="readout"] {{ background: transparent; border-color: transparent; font-size: 22px; font-weight: 600;
                                   padding: 2px 4px; }}
QAbstractSpinBox[role="readout"]:hover {{ background: {RAISED}; border-color: {LINE_STRONG}; }}
QAbstractSpinBox[role="readout"]:focus {{ background: {RAISED}; border-color: {ACCENT}; }}

QComboBox {{ background: {RAISED}; border: 1px solid {LINE_STRONG}; border-radius: 6px; padding: 5px 26px 5px 10px;
            min-height: 18px; }}
QComboBox:hover {{ border-color: {EDGE_HOVER}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox:disabled {{ color: {TEXT_3}; background: {BASE}; border-color: {LINE}; }}
QComboBox::drop-down {{ border: 0; width: 24px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 10px; height: 10px; }}
QComboBox[role="quiet"] {{ background: transparent; border-color: transparent; color: {TEXT_2}; }}
QComboBox[role="quiet"]:hover {{ background: {RAISED}; border-color: {LINE_STRONG}; color: {TEXT}; }}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {LINE_STRONG}; padding: 4px; outline: 0;
                              selection-background-color: {HOVER}; selection-color: {TEXT}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {LINE_STRONG}; border-radius: 4px; background: {RAISED}; }}
QCheckBox::indicator:hover {{ border-color: {EDGE_HOVER}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{check}"); }}

QSlider::groove:horizontal {{ height: 4px; background: {LINE_STRONG}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {TEXT_2}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; background: {TEXT}; }}
QSlider::handle:horizontal:hover {{ background: #FFFFFF; }}

QScrollArea {{ background: transparent; border: 0; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; margin: 0; }}
QScrollBar:vertical {{ width: 8px; background: transparent; margin: 0; }}
QScrollBar::handle {{ background: {LINE_STRONG}; border-radius: 4px; min-width: 32px; min-height: 32px; }}
QScrollBar::handle:hover {{ background: {TEXT_3}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QListView {{ background: transparent; border: 0; outline: 0; }}
QSplitter::handle {{ background: {LINE}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QProgressBar {{ background: {LINE}; border: 0; border-radius: 1px; max-height: 3px; min-height: 3px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 1px; }}

QMenuBar {{ background: {BASE}; border-bottom: 1px solid {LINE}; }}
QMenuBar::item {{ padding: 6px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {HOVER}; border-radius: 4px; }}
QMenu {{ background: {RAISED}; border: 1px solid {LINE_STRONG}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {HOVER}; }}
QMenu::item:disabled {{ color: {TEXT_3}; }}
QMenu::indicator {{ width: 14px; height: 14px; }}
QMenu::indicator:checked {{ image: url("{tick}"); }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 4px; }}
QToolTip {{ background: {HOVER}; color: {TEXT}; border: 1px solid {LINE_STRONG}; padding: 6px 8px; border-radius: 4px; }}
QMessageBox QLabel {{ font-size: {SIZE_BODY}px; }}
"""
