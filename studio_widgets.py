"""Custom-painted widgets for DJ Studio, in the idiom of arrangement-based DAWs."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import (QAbstractTableModel, QEvent, QMimeData, QModelIndex, QPointF, QRectF, QSize, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QComboBox, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate,
                               QTableView, QToolButton, QWidget)

import djmix
import studio_theme as T
from studio_engine import layout
from studio_waveform import BINS_PER_SECOND

TRACK_MIME = "application/x-dj-track"
CLIP_MIME = "application/x-dj-clip"

_MAJOR_CAMELOT = {0: "8B", 7: "9B", 2: "10B", 9: "11B", 4: "12B", 11: "1B", 6: "2B", 1: "3B", 8: "4B", 3: "5B", 10: "6B", 5: "7B"}
_MINOR_CAMELOT = {9: "8A", 4: "9A", 11: "10A", 6: "11A", 1: "12A", 8: "1A", 3: "2A", 10: "3A", 5: "4A", 0: "5A", 7: "6A", 2: "7A"}
_MAJOR_NAMES = ("C", "D♭", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B")
_MINOR_NAMES = ("C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "G♯", "A", "B♭", "B")
DARK_TEXT = "#141414"


def parsed_key(key):
    try:
        return djmix.parse_key(key) if key else None
    except djmix.MixError:
        return None


def camelot(key) -> str:
    parsed = parsed_key(key) if isinstance(key, str) else key
    if not parsed:
        return ""
    return (_MINOR_CAMELOT if parsed[1] == "minor" else _MAJOR_CAMELOT)[parsed[0]]


def display_key(key, short=False) -> str:
    """Conventional spelling for display: B♭ minor, not A# minor."""
    parsed = parsed_key(key) if isinstance(key, str) else key
    if not parsed:
        return ""
    pitch, mode = parsed
    name = (_MINOR_NAMES if mode == "minor" else _MAJOR_NAMES)[pitch]
    if short:
        return name + ("m" if mode == "minor" else "")
    return f"{name} {mode}"


def clock(seconds, tenths=False) -> str:
    seconds = max(0.0, float(seconds))
    if tenths:
        total = int(round(seconds * 10))
        return f"{total // 600}:{total // 10 % 60:02d}.{total % 10}"
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def bar_position(seconds, bpm) -> str:
    """Bars, beats and sixteenths in 4/4, counted from 1 like a DAW position display."""
    sixteenths = int(max(0.0, seconds) / (15 / bpm) + 1e-6)
    return f"{sixteenths // 16 + 1}.{sixteenths // 4 % 4 + 1}.{sixteenths % 4 + 1}"


def band_columns(bands, first_bin: float, last_bin: float, columns: int):
    """Pool band envelopes into pixel columns, returning (columns, 3) or None."""
    if bands is None or len(bands) < 2 or columns < 1:
        return None
    first, last = max(0.0, first_bin), min(float(len(bands)), last_bin)
    if last - first < 1:
        return None
    edges = np.linspace(first, last, columns + 1)
    if last - first >= columns:
        # Mostly average energy with some peak: pure maxima saturate when zoomed out.
        starts = np.floor(edges[:-1]).astype(int)
        segment = bands[:math.ceil(last)]
        counts = np.diff(np.append(starts, len(segment)))[:, None]
        mean = np.add.reduceat(segment, starts, axis=0) / np.maximum(counts, 1)
        return .4 * np.maximum.reduceat(segment, starts, axis=0) + .6 * mean
    centers = np.clip(((edges[:-1] + edges[1:]) / 2).astype(int), 0, len(bands) - 1)
    return bands[centers]


BAND_WEIGHTS = (0.94, 0.62, 0.34)


def paint_bands(painter: QPainter, rect: QRectF, columns, colors=None, alpha: float = 1.0):
    """Mirrored three-band waveform, low band behind, high band in front."""
    if columns is None or not len(columns):
        return
    colors = colors or [QColor(c) for c in T.BANDS]
    count = len(columns)
    xs = rect.left() + (np.arange(count) + 0.5) * rect.width() / count
    xs = np.concatenate(([rect.left()], xs, [rect.right()]))
    middle, half = rect.center().y(), rect.height() / 2
    painter.setPen(Qt.PenStyle.NoPen)
    for band in range(3):
        amplitude = np.concatenate(([0.0], np.maximum(columns[:, band] * BAND_WEIGHTS[band], 0.012), [0.0])) * half
        top = [QPointF(x, middle - a) for x, a in zip(xs, amplitude)]
        bottom = [QPointF(x, middle + a) for x, a in zip(xs[::-1], amplitude[::-1])]
        painter.setBrush(T.color(colors[band], alpha))
        painter.drawPolygon(QPolygonF(top + bottom))


def paint_envelope(painter: QPainter, rect: QRectF, values, ink, alpha=1.0):
    """Mirrored single-color envelope from values in 0..1."""
    if values is None or not len(values) or rect.width() < 1:
        return
    count = len(values)
    xs = rect.left() + (np.arange(count) + .5) * rect.width() / count
    middle, half = rect.center().y(), rect.height() / 2
    top = [QPointF(x, middle - max(.5, v * half)) for x, v in zip(xs, values)]
    bottom = [QPointF(x, middle + max(.5, v * half)) for x, v in zip(xs[::-1], values[::-1])]
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(T.color(ink, alpha))
    painter.drawPolygon(QPolygonF(top + bottom))


def peak_columns(waveform, start: float, end: float, count: int):
    """Coarse library peaks (fractions of the song) as a placeholder while bands load."""
    if not waveform or count < 1:
        return None
    values = np.asarray(waveform, dtype=float)
    positions = np.clip((start + (np.arange(count) + .5) / count * (end - start)) * len(values), 0, len(values) - 1)
    return values[positions.astype(int)] * .8


def gain_curves(progress, incoming: bool, bass_swap: bool, length_bars: float):
    """Mirror of studio_engine._weights for display: (low, high) gains for 0..1 progress."""
    high = np.sin(progress * np.pi / 2) if incoming else np.cos(progress * np.pi / 2)
    if not bass_swap:
        return high, high
    width = min(1.0, 1.0 / max(1e-6, length_bars))
    swap = np.clip((progress - (1 - width) / 2) / max(1e-6, width), 0, 1)
    low = (1 - np.cos(swap * np.pi)) / 2
    return (low if incoming else 1 - low), high


# Controls ---------------------------------------------------------------------------------

class ComboBox(QComboBox):
    """Ignores the scroll wheel unless focused, so scrolling a panel never edits values."""

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


def tool_button(icon_name, tooltip, callback=None, size=14):
    widget = QToolButton()
    widget.setIcon(T.icon(icon_name, size=size))
    widget.setIconSize(QSize(size, size))
    widget.setToolTip(tooltip)
    widget.setAccessibleName(tooltip)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    if callback:
        widget.clicked.connect(callback)
    return widget


class _InlineEditor(QLineEdit):
    cancelled = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(event)


class ValueBox(QWidget):
    """A DAW number box: drag up or down to change it, hold Shift for fine steps,
    double-click or start typing to enter a value, arrow keys to nudge."""

    committed = Signal(float)

    def __init__(self, minimum, maximum, step=1.0, fine=None, decimals=0, suffix="", text=None, parse=None,
                 width=64, height=20, pixels_per_step=4, font_size=12, parent=None):
        super().__init__(parent)
        self.minimum, self.maximum = minimum, maximum
        self.step, self.fine = step, fine if fine is not None else step
        self.decimals, self.suffix = decimals, suffix
        self._text, self._parse = text, parse
        self._value = minimum
        self._origin = None
        self._moved = False
        self.pixels = pixels_per_step
        self.font_size = font_size
        self.editor = None
        self.setFixedSize(width, height)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def step_size(self, fine=False):
        size = self.fine if fine else self.step
        return size() if callable(size) else size

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = min(self.maximum, max(self.minimum, value))
        self.update()

    def setMaximum(self, maximum):
        self.maximum = maximum
        self._value = min(self._value, maximum)
        self.update()

    def display(self):
        return self._text(self._value) if self._text else f"{self._value:.{self.decimals}f}{self.suffix}"

    def parse(self, text):
        if self._parse:
            return self._parse(text)
        return float(text.replace(self.suffix.strip(), "").replace("+", "").strip())

    def commit(self, value):
        old = self._value
        self.setValue(round(value, max(self.decimals, 6)))
        if self._value != old:
            self.committed.emit(self._value)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        active = self.hasFocus() or self._moved
        hovered = self.underMouse() and self.isEnabled()
        painter.setPen(QPen(QColor(T.ACCENT if active else T.EDGE_HOVER if hovered else T.LINE_STRONG), 1))
        painter.setBrush(QColor(T.FIELD))
        painter.drawRoundedRect(rect, 2, 2)
        painter.setFont(T.font(self.font_size, QFont.Weight.Medium, tabular=True))
        painter.setPen(QColor(T.TEXT_3 if not self.isEnabled() else T.ACCENT if self._moved else T.TEXT))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.display())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._origin = (event.position().y(), self._value)
            self._moved = False
            self.setFocus(Qt.FocusReason.MouseFocusReason)

    def mouseMoveEvent(self, event):
        if not self._origin:
            return
        start_y, start_value = self._origin
        steps = int((start_y - event.position().y()) / self.pixels)
        if steps or self._moved:
            self._moved = True
            fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.setValue(round(start_value + steps * self.step_size(fine), max(self.decimals, 6)))

    def mouseReleaseEvent(self, event):
        if self._origin:
            start_value = self._origin[1]
            moved = self._moved
            self._origin, self._moved = None, False
            if moved and self._value != start_value:
                self.committed.emit(self._value)
            self.update()

    def mouseDoubleClickEvent(self, event):
        self.edit()

    def edit(self, initial=None):
        if self.editor or not self.isEnabled():
            return
        editor = _InlineEditor(self)
        editor.setFont(T.font(self.font_size, tabular=True))
        editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        editor.setGeometry(self.rect())
        editor.setText(initial if initial is not None else self.display())
        if initial is None:
            editor.selectAll()
        self.editor = editor
        done = {"value": False}

        def finish(accept=True):
            if done["value"]:
                return
            done["value"] = True
            text = editor.text()
            editor.deleteLater()
            self.editor = None
            self.setFocus()
            if accept:
                try:
                    self.commit(self.parse(text))
                except (ValueError, TypeError):
                    pass

        editor.editingFinished.connect(finish)
        editor.cancelled.connect(lambda: finish(False))
        editor.show()
        editor.setFocus()

    def keyPressEvent(self, event):
        key, fine = event.key(), bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.commit(self._value + (1 if key == Qt.Key.Key_Up else -1) * self.step_size(fine))
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.edit()
        elif event.text() and event.text() in "0123456789.-+:":
            self.edit(event.text())
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta:
            self.commit(self._value + (1 if delta > 0 else -1) * self.step_size())


class Knob(QWidget):
    """Bipolar rotary control. Drag vertically, double-click to reset to zero."""

    committed = Signal(int)

    def __init__(self, minimum=-12, maximum=12, unit="st", parent=None):
        super().__init__(parent)
        self.minimum, self.maximum, self.unit = minimum, maximum, unit
        self._value = 0
        self._origin = None
        self.setFixedSize(52, 50)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = int(min(self.maximum, max(self.minimum, value)))
        self.update()

    def commit(self, value):
        old = self._value
        self.setValue(value)
        if self._value != old:
            self.committed.emit(self._value)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        dial = QRectF((self.width() - 30) / 2, 2, 30, 30)
        painter.setPen(QPen(QColor(T.LINE_STRONG), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.drawArc(dial, 225 * 16, -270 * 16)
        fraction = (self._value - self.minimum) / (self.maximum - self.minimum)
        angle = 225 - 270 * fraction
        if self._value:
            painter.setPen(QPen(QColor(T.ACCENT if enabled else T.TEXT_3), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            painter.drawArc(dial, 90 * 16, int((angle - 90) * 16))
        center = dial.center()
        radians = math.radians(angle)
        tip = QPointF(center.x() + math.cos(radians) * 10, center.y() - math.sin(radians) * 10)
        painter.setPen(QPen(QColor(T.TEXT if enabled else T.TEXT_3), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, tip)
        if self.hasFocus():
            painter.setPen(QPen(T.color(T.ACCENT, .7), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(dial.adjusted(-3, -3, 3, 3))
        painter.setFont(T.font(11, QFont.Weight.Medium, tabular=True))
        painter.setPen(QColor(T.TEXT if enabled else T.TEXT_3))
        text = f"{self._value:+d} {self.unit}" if self._value else f"0 {self.unit}"
        painter.drawText(QRectF(0, 34, self.width(), 16), Qt.AlignmentFlag.AlignCenter, text)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._origin = (event.position().y(), self._value)
            self.setFocus(Qt.FocusReason.MouseFocusReason)

    def mouseMoveEvent(self, event):
        if self._origin:
            start_y, start_value = self._origin
            self.setValue(start_value + int((start_y - event.position().y()) / 7))

    def mouseReleaseEvent(self, event):
        if self._origin:
            start_value = self._origin[1]
            self._origin = None
            if self._value != start_value:
                self.committed.emit(self._value)

    def mouseDoubleClickEvent(self, event):
        self.commit(0)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Right):
            self.commit(self._value + 1)
        elif event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Left):
            self.commit(self._value - 1)
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        if self.hasFocus() and event.angleDelta().y():
            self.commit(self._value + (1 if event.angleDelta().y() > 0 else -1))
        else:
            event.ignore()


def toggle(text, tooltip=""):
    """A text button that stays lit while switched on."""
    widget = QPushButton(text)
    widget.setCheckable(True)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setToolTip(tooltip)
    return widget


class ElidedLabel(QLabel):
    """Single-line label that shortens with an ellipsis and shows the full text as a tooltip."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)
        self.set_text(text)

    def set_text(self, text):
        self._full = text
        self._elide()
        self.updateGeometry()

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(int(self.fontMetrics().horizontalAdvance(self._full)) + 2)
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(24)
        return hint

    def text_full(self):
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width()))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class LevelMeter(QWidget):
    """Stereo peak meter from -48 dBFS, green to amber to red, with a 1.2 s peak hold."""

    FLOOR = -48.0

    def __init__(self, parent=None, vertical=False):
        super().__init__(parent)
        self.vertical = vertical
        if not vertical:
            self.setFixedSize(120, 14)
        self.setToolTip("Mix level before output volume")
        self.clear()

    def clear(self):
        self.levels = [self.FLOOR, self.FLOOR]
        self.peaks = [self.FLOOR, self.FLOOR]
        self.held = [0.0, 0.0]
        self.update()

    def set_levels(self, left, right, now):
        for channel, value in enumerate((left, right)):
            level = 20 * math.log10(max(value, 1e-6))
            self.levels[channel] = max(level, self.levels[channel] - 1.6)
            if level >= self.peaks[channel] or now - self.held[channel] > 1.2:
                self.peaks[channel], self.held[channel] = level, now
        self.update()

    def decay(self):
        self.levels = [max(self.FLOOR, level - 2.4) for level in self.levels]
        self.peaks = [max(self.FLOOR, peak - 1.2) for peak in self.peaks]
        self.update()
        return any(level > self.FLOOR for level in self.levels + self.peaks)

    @classmethod
    def paint_bars(cls, painter, rect, levels, peaks, vertical):
        gap = 2
        for channel in range(2):
            if vertical:
                width = (rect.width() - gap) / 2
                bar = QRectF(rect.left() + channel * (width + gap), rect.top(), width, rect.height())
            else:
                height = (rect.height() - gap) / 2
                bar = QRectF(rect.left(), rect.top() + channel * (height + gap), rect.width(), height)
            painter.fillRect(bar, QColor(T.FIELD))
            fraction = min(1.0, max(0.0, (levels[channel] - cls.FLOOR) / -cls.FLOOR))
            for start, end, ink in ((0, 36 / 48, T.PLAYING), (36 / 48, 45 / 48, "#E9D45D"), (45 / 48, 1, T.BAD)):
                lit = min(fraction, end) - start
                if lit <= 0:
                    continue
                if vertical:
                    painter.fillRect(QRectF(bar.left(), bar.bottom() - (start + lit) * bar.height(), bar.width(),
                                            lit * bar.height()), QColor(ink))
                else:
                    painter.fillRect(QRectF(bar.left() + start * bar.width(), bar.top(), lit * bar.width(), bar.height()),
                                     QColor(ink))
            peak = (peaks[channel] - cls.FLOOR) / -cls.FLOOR
            if peaks[channel] > cls.FLOOR + 1:
                ink = QColor(T.BAD if peak > 45 / 48 else T.TEXT)
                if vertical:
                    painter.fillRect(QRectF(bar.left(), bar.bottom() - min(1, peak) * bar.height(), bar.width(), 1.5), ink)
                else:
                    painter.fillRect(QRectF(bar.left() + min(1, peak) * bar.width() - 1.5, bar.top(), 1.5, bar.height()), ink)

    def paintEvent(self, event):
        painter = QPainter(self)
        self.paint_bars(painter, QRectF(self.rect()), self.levels, self.peaks, self.vertical)


class Notice(QFrame):
    """Inline message with optional actions. Info notices dismiss themselves after a few seconds."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notice")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 4, 4)
        row.setSpacing(8)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.message, 1)
        self.actions_row = QHBoxLayout()
        self.actions_row.setSpacing(4)
        row.addLayout(self.actions_row)
        self.close_button = tool_button("close", "Dismiss", self.hide, 12)
        row.addWidget(self.close_button)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, text, tone="info", actions=(), timeout=6000):
        while self.actions_row.count():
            item = self.actions_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for index, (label, callback) in enumerate(actions):
            button = QPushButton(label)
            button.setProperty("role", "primary" if index == 0 else "ghost")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, c=callback: (self.hide(), c()))
            self.actions_row.addWidget(button)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)
        self.message.setText(text)
        self.show()
        self.timer.stop()
        if tone == "info" and not actions and timeout:
            self.timer.start(timeout)


# Arrangement ------------------------------------------------------------------------------

class Timeline(QAbstractScrollArea):
    """Arrangement view: locators and a bar ruler on top, songs alternating on lanes A and B,
    the rendered mix on its own lane, a time ruler below and lane headers on the right."""

    trackDropped = Signal(str, int)
    clipMoved = Signal(str, int)
    selected = Signal(str)
    seekRequested = Signal(float)
    transitionSelected = Signal(int)
    viewChanged = Signal()

    PAD, LOCATORS, RULER, TIME_RULER, HEADERS, MIX, CLIP_HEADER = 10, 16, 34, 18, 132, 48, 17

    def __init__(self, session, bands, meter, parent=None):
        super().__init__(parent)
        self.session, self.bands, self.meter = session, bands, meter
        self.selection, self.transition = "", 0
        self.harmony = {}
        self.scale = 2.0
        self.playhead = None
        self.mix_audio = None
        self.drop_index = None
        self.drag_origin = self.drag_clip = None
        self.ruler_drag = None
        self.positions = []
        self._labels = []
        self._static = None
        self.setAcceptDrops(True)
        self.setMinimumHeight(230)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.horizontalScrollBar().valueChanged.connect(self._scrolled)
        self.viewport().setMouseTracking(True)
        self.setAccessibleName("Arrangement")
        self.setAccessibleDescription("Drag songs here from the browser. Drag clips to reorder them.")

    # Geometry
    def duration(self):
        return self.positions[-1]["end"] / djmix.SR if self.positions else 0.0

    def area_width(self):
        return max(60, self.viewport().width() - self.HEADERS)

    def x(self, seconds):
        return self.PAD + seconds * self.scale - self.horizontalScrollBar().value()

    def time_at(self, x):
        return max(0.0, (x - self.PAD + self.horizontalScrollBar().value()) / self.scale)

    def lanes(self):
        """(lane top, lane height, mix top, mix bottom)."""
        bottom = self.viewport().height() - self.TIME_RULER
        mix_top = bottom - self.MIX
        height = max(44.0, (mix_top - self.RULER - 2) / 2)
        return self.RULER, height, mix_top, bottom

    def lane_top(self, lane):
        top, height, _, _ = self.lanes()
        return top + lane * (height + 1)

    def clip_rect(self, position, index):
        _, height, _, _ = self.lanes()
        return QRectF(self.x(position["start"] / djmix.SR), self.lane_top(index % 2) + 1,
                      position["length"] / djmix.SR * self.scale, height - 2)

    def visible_range(self):
        return self.time_at(0), self.time_at(self.area_width())

    # State
    def refresh(self):
        try:
            self.positions = layout(self.session)
        except djmix.MixError:
            self.positions = []
        span = max(self.duration(), 60)
        bar = self.horizontalScrollBar()
        bar.setRange(0, max(0, round(span * self.scale + 2 * self.PAD - self.area_width())))
        bar.setPageStep(self.area_width())
        bar.setSingleStep(24)
        self.invalidate()
        self.viewChanged.emit()

    def invalidate(self):
        self._static = None
        self.viewport().update()

    def set_playhead(self, seconds):
        self.playhead = seconds
        self.viewport().update()

    def fit(self):
        self.scale = max(.05, (self.area_width() - 2 * self.PAD) / max(30.0, self.duration()))
        self.refresh()
        self.horizontalScrollBar().setValue(0)

    def zoom(self, factor, anchor=None):
        anchor = self.area_width() / 2 if anchor is None else anchor
        moment = self.time_at(anchor)
        self.scale = min(400.0, max(.05, self.scale * factor))
        self.refresh()
        self.horizontalScrollBar().setValue(round(self.PAD + moment * self.scale - anchor))

    def center_on(self, seconds):
        self.horizontalScrollBar().setValue(round(self.PAD + seconds * self.scale - self.area_width() / 2))

    def _scrolled(self, _):
        self.invalidate()
        self.viewChanged.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()

    # Painting
    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        if self._static is None or self._static.deviceIndependentSize().toSize() != self.viewport().size():
            self._static = self._render_static()
        painter.drawPixmap(0, 0, self._static)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _, _, mix_top, bottom = self.lanes()
        painter.save()
        painter.setClipRect(QRectF(0, 0, self.area_width(), self.viewport().height()))
        if self.drop_index is not None:
            if self.positions:
                index = self.drop_index
                seconds = self.positions[index]["start"] / djmix.SR if index < len(self.positions) else self.duration()
                x = self.x(seconds)
            else:
                x = self.PAD
            painter.fillRect(QRectF(x - 1, self.LOCATORS, 2, bottom - self.LOCATORS), QColor(T.ACCENT))
        if self.playhead is not None and self.positions:
            x = self.x(self.playhead)
            painter.fillRect(QRectF(x - .5, self.LOCATORS, 1, bottom - self.LOCATORS), QColor("#F2F2F2"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#F2F2F2"))
            painter.drawPolygon(QPolygonF([QPointF(x - 5, self.LOCATORS), QPointF(x + 5, self.LOCATORS),
                                           QPointF(x, self.LOCATORS + 6)]))
        painter.restore()
        self._paint_headers(painter)
        painter.end()

    def _paint_headers(self, painter):
        left = self.area_width()
        width, height = self.HEADERS, self.viewport().height()
        top, lane, mix_top, bottom = self.lanes()
        painter.fillRect(QRectF(left, 0, width, height), QColor(T.GUTTER))
        painter.fillRect(QRectF(left + 1, 0, width - 1, self.RULER - 1), QColor(T.TITLE))
        counts = [0, 0]
        for index in range(len(self.positions)):
            counts[index % 2] += 1
        rows = [("A", "Deck A", self.lane_top(0), lane, counts[0]), ("B", "Deck B", self.lane_top(1), lane, counts[1]),
                ("M", "Mix", mix_top, self.MIX, None)]
        for letter, name, y, h, count in rows:
            box = QRectF(left + 1, y + 1, width - 1, h - 1)
            painter.fillRect(box, QColor(T.TITLE))
            painter.fillRect(QRectF(box.left() + 8, box.top() + 7, 16, 16), QColor(T.RAISED))
            painter.setFont(T.font(11, QFont.Weight.DemiBold))
            painter.setPen(QColor(T.TEXT_2))
            painter.drawText(QRectF(box.left() + 8, box.top() + 7, 16, 16), Qt.AlignmentFlag.AlignCenter, letter)
            painter.setFont(T.font(12, QFont.Weight.Medium))
            painter.setPen(QColor(T.TEXT))
            painter.drawText(QRectF(box.left() + 30, box.top() + 7, width - 40, 16),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
            if count is not None and h > 44:
                painter.setFont(T.font(11))
                painter.setPen(QColor(T.TEXT_3))
                painter.drawText(QRectF(box.left() + 30, box.top() + 24, width - 40, 14),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                 f"{count} {'song' if count == 1 else 'songs'}")
            if count is None:
                LevelMeter.paint_bars(painter, QRectF(box.right() - 18, box.top() + 6, 10, box.height() - 12),
                                      self.meter.levels, self.meter.peaks, True)
        painter.fillRect(QRectF(left + 1, bottom, width - 1, self.TIME_RULER), QColor(T.TITLE))

    def _render_static(self):
        ratio = self.viewport().devicePixelRatioF()
        size = self.viewport().size()
        pixmap = QPixmap(max(1, int(size.width() * ratio)), max(1, int(size.height() * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QColor(T.LANE))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.area_width()
        painter.setClipRect(QRectF(0, 0, width, size.height()))
        self._paint_lanes(painter, width)
        self._labels = []
        if self.positions:
            for index in range(1, len(self.positions)):
                self._paint_overlap(painter, index)
            for index, (clip, position) in enumerate(zip(self.session.clips, self.positions)):
                self._paint_clip(painter, index, clip, position, width)
        else:
            top, lane, _, _ = self.lanes()
            painter.setFont(T.font(13, QFont.Weight.Medium))
            painter.setPen(QColor(T.TEXT_2))
            painter.drawText(QRectF(0, top, width, 2 * lane), Qt.AlignmentFlag.AlignCenter,
                             "Drag songs here from the browser, or double-click one to add it")
        self._paint_mix(painter, width)
        self._paint_rulers(painter, width)
        painter.end()
        return pixmap

    def _bar_steps(self):
        bar = 240 / self.session.target_bpm
        major = 1
        while major * bar * self.scale < 56:
            major *= 2
        return bar, major

    def _paint_lanes(self, painter, width):
        top, lane, mix_top, bottom = self.lanes()
        painter.fillRect(QRectF(0, self.lane_top(0), width, lane), QColor(T.LANE))
        painter.fillRect(QRectF(0, self.lane_top(1), width, lane), QColor(T.LANE_ALT))
        painter.fillRect(QRectF(0, mix_top, width, self.MIX), QColor("#171717"))
        for y in (self.lane_top(1) - 1, mix_top - 1, bottom - 1):
            painter.fillRect(QRectF(0, y, width, 1), QColor(T.GUTTER))
        bar, major = self._bar_steps()
        beat = bar / 4
        first, last = self.time_at(0), self.time_at(width)
        if beat * self.scale >= 12:
            step, kind = beat, "beat"
        elif bar * self.scale >= 6:
            step, kind = bar, "bar"
        else:
            step, kind = bar * major, "major"
        tick = math.floor(first / step) * step
        while tick <= last + step:
            x = self.x(tick)
            bars = tick / bar
            if abs(bars - round(bars)) < 1e-6 and round(bars) % major == 0:
                ink = "#303030"
            elif kind == "beat" and abs(bars - round(bars)) > 1e-6:
                ink = "#1F1F1F"
            else:
                ink = "#262626"
            painter.fillRect(QRectF(x, top, 1, bottom - top), QColor(ink))
            tick += step

    def _paint_rulers(self, painter, width):
        _, _, mix_top, bottom = self.lanes()
        painter.fillRect(QRectF(0, 0, width, self.LOCATORS), QColor(T.BASE))
        painter.fillRect(QRectF(0, self.LOCATORS, width, self.RULER - self.LOCATORS), QColor(T.TITLE))
        painter.fillRect(QRectF(0, self.RULER - 1, width, 1), QColor(T.GUTTER))
        bar, major = self._bar_steps()
        first, last = self.time_at(0), self.time_at(width)
        painter.setFont(T.font(11, tabular=True))
        index = math.floor(first / bar / major) * major
        while index * bar <= last + bar * major:
            x = self.x(index * bar)
            painter.fillRect(QRectF(x, self.LOCATORS + 2, 1, self.RULER - self.LOCATORS - 3), QColor(T.LINE_STRONG))
            painter.setPen(QColor(T.TEXT_2))
            painter.drawText(QRectF(x + 3, self.LOCATORS, 60, self.RULER - self.LOCATORS - 4),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(index + 1))
            minor = 1 if major <= 4 else major // 4
            for sub in range(minor, major, minor):
                painter.fillRect(QRectF(self.x((index + sub) * bar), self.RULER - 5, 1, 4), QColor(T.LINE_STRONG))
            index += major
        # Transition locators, with the blend shown as a brace under the bar numbers.
        for index in range(1, len(self.positions)):
            position = self.positions[index]
            left = self.x(position["start"] / djmix.SR)
            right = self.x((position["start"] + position["incoming"]) / djmix.SR)
            current = index == self.transition
            ink = QColor(T.ACCENT if current else T.TEXT_2)
            painter.fillRect(QRectF(left, self.RULER - 4, max(2, right - left), 3), T.color(ink, .9 if current else .45))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            painter.drawPolygon(QPolygonF([QPointF(left, 2), QPointF(left + 9, 2), QPointF(left + 9, 10),
                                           QPointF(left + 4.5, 14), QPointF(left, 10)]))
            state = self.harmony.get(index, "unknown")
            text = f"{index} → {index + 1}"
            font = T.font(11, QFont.Weight.Medium)
            painter.setFont(font)
            advance = QFontMetricsF(font).horizontalAdvance(text)
            painter.setPen(ink)
            painter.drawText(QRectF(left + 13, 0, advance + 4, self.LOCATORS), Qt.AlignmentFlag.AlignVCenter, text)
            dot = {"good": T.GOOD, "bad": T.BAD}.get(state, T.TEXT_3)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(dot))
            painter.drawEllipse(QPointF(left + 20 + advance, self.LOCATORS / 2), 3, 3)
            self._labels.append((QRectF(left - 2, 0, advance + 30, self.LOCATORS), index))
        painter.fillRect(QRectF(0, bottom, width, self.TIME_RULER), QColor(T.TITLE))
        painter.fillRect(QRectF(0, bottom, width, 1), QColor(T.GUTTER))
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if s * self.scale >= 64), 1200)
        tick = math.floor(first / step) * step
        painter.setFont(T.font(11, tabular=True))
        while tick <= last + step:
            x = self.x(tick)
            painter.fillRect(QRectF(x, bottom + 1, 1, 5), QColor(T.LINE_STRONG))
            painter.setPen(QColor(T.TEXT_3))
            painter.drawText(QRectF(x + 3, bottom + 1, 60, self.TIME_RULER - 2),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, clock(tick))
            tick += step

    def _paint_overlap(self, painter, index):
        position = self.positions[index]
        left = self.x(position["start"] / djmix.SR)
        right = self.x((position["start"] + position["incoming"]) / djmix.SR)
        top, lane, _, _ = self.lanes()
        current = index == self.transition
        painter.fillRect(QRectF(left, top, right - left, 2 * lane + 1),
                         T.color(T.ACCENT, .07) if current else T.color("#FFFFFF", .03))

    def _paint_clip(self, painter, index, clip, position, width):
        rect = self.clip_rect(position, index)
        if rect.right() < -2 or rect.left() > width + 2:
            return
        track = self.session.tracks[clip.track_id]
        selected = clip.id == self.selection
        base = T.clip_color(track.id)
        painter.save()
        path = QPainterPath()
        path.addRoundedRect(rect, 2, 2)
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        painter.fillPath(path, T.mix(base, T.LANE, .8))
        header = QRectF(rect.left(), rect.top(), rect.width(), self.CLIP_HEADER)
        painter.fillRect(header, T.mix(base, "#FFFFFF", .22) if selected else QColor(base))
        wave = QRectF(rect.left(), header.bottom() + 2, rect.width(), rect.height() - self.CLIP_HEADER - 4)
        visible_left, visible_right = max(wave.left(), -4), min(wave.right(), width + 4)
        if visible_right > visible_left:
            span = clip.cue_out - clip.cue_in
            start = clip.cue_in + (visible_left - wave.left()) / wave.width() * span
            end = clip.cue_in + (visible_right - wave.left()) / wave.width() * span
            target = QRectF(visible_left, wave.top(), visible_right - visible_left, wave.height())
            columns = band_columns(self.bands.get(track.id), start * BINS_PER_SECOND, end * BINS_PER_SECOND,
                                   max(1, min(4000, int(target.width()))))
            if columns is not None:
                paint_bands(painter, target, columns, T.clip_bands(base))
            else:
                paint_envelope(painter, target, peak_columns(track.waveform, start / track.duration,
                                                             end / track.duration, max(1, int(target.width() / 2))),
                               T.mix(base, T.LANE, .4))
        self._paint_curves(painter, index, clip, position, wave)
        painter.restore()
        # Title bar text: name on the left, key and tempo on the right; the name stays in view.
        visible = QRectF(max(rect.left(), 0.0), rect.top(), min(rect.right(), float(width)) - max(rect.left(), 0.0),
                         self.CLIP_HEADER)
        painter.save()
        painter.setClipRect(visible.adjusted(4, 0, -4, 0))
        meta_font, name_font = T.font(11, tabular=True), T.font(11, QFont.Weight.DemiBold)
        metrics, name_metrics = QFontMetricsF(meta_font), QFontMetricsF(name_font)
        processed = djmix.transpose(parsed_key(track.key), clip.shift)
        parts = []
        if processed:
            parts.append(display_key(processed, True) + (f" ({clip.shift:+d})" if clip.shift else ""))
        stretch = (self.session.target_bpm / track.bpm - 1) * 100
        parts.append(f"{track.bpm:.1f}" + (f" {stretch:+.1f}%" if abs(stretch) >= .05 else ""))
        if clip.stabilize:
            parts.append("Stabilized")
        meta = "   ".join(parts)
        meta_width = metrics.horizontalAdvance(meta)
        room = visible.width() - 14
        show_meta = room - meta_width > min(120, name_metrics.horizontalAdvance(track.name) + 12)
        name_room = room - (meta_width + 12 if show_meta else 0)
        painter.setPen(QColor(DARK_TEXT))
        painter.setFont(name_font)
        painter.drawText(QRectF(visible.left() + 6, visible.top(), max(0, name_room), self.CLIP_HEADER),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         name_metrics.elidedText(track.name, Qt.TextElideMode.ElideRight, max(0, name_room)))
        if show_meta:
            painter.setFont(meta_font)
            painter.setPen(T.color(DARK_TEXT, .78))
            painter.drawText(QRectF(visible.right() - 6 - meta_width - 2, visible.top(), meta_width + 2, self.CLIP_HEADER),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, meta)
        painter.restore()
        painter.setPen(QPen(QColor("#F2F2F2"), 1) if selected else QPen(T.mix(base, "#000000", .55), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(.5, .5, -.5, -.5), 2, 2)

    def _paint_curves(self, painter, index, clip, position, wave):
        """Gain envelopes over the blend: solid for the full band, dashed for the bass."""
        segments = []
        if index:
            segments.append((position["start"], position["incoming"], True, clip))
        if index + 1 < len(self.positions):
            following = self.positions[index + 1]
            segments.append((following["start"], following["incoming"], False, self.session.clips[index + 1]))
        bar_frames = 240 / self.session.target_bpm * djmix.SR
        for start, length, incoming, owner in segments:
            left, right = self.x(start / djmix.SR), self.x((start + length) / djmix.SR)
            if right - left < 6:
                continue
            progress = np.linspace(0, 1, 64)
            low, high = gain_curves(progress, incoming, owner.bass_swap, length / bar_frames)
            xs = left + progress * (right - left)
            curves = [(high, QPen(T.color("#FFFFFF", .85), 1.3))]
            if owner.bass_swap:
                curves.append((low, QPen(T.color("#FFFFFF", .5), 1.1, Qt.PenStyle.DashLine)))
            for gains, pen in curves:
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(QPolygonF([QPointF(x, wave.bottom() - 2 - g * (wave.height() - 4))
                                                for x, g in zip(xs, gains)]))

    def _paint_mix(self, painter, width):
        _, _, mix_top, _ = self.lanes()
        rect = QRectF(0, mix_top + 3, width, self.MIX - 7)
        audio = self.mix_audio
        if not audio:
            painter.setFont(T.font(11))
            painter.setPen(QColor(T.TEXT_3))
            painter.drawText(rect.adjusted(10, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             "Rendered audio appears here after a preview or a full render")
            return
        start, end, levels, current = audio
        left, right = self.x(start), self.x(end)
        block = QRectF(max(left, -2), rect.top(), min(right, width + 2) - max(left, -2), rect.height())
        if block.width() <= 0:
            return
        painter.fillRect(QRectF(left, rect.top(), right - left, rect.height()), QColor("#232323" if current else "#1C1C1C"))
        if levels is not None and len(levels):
            seconds = len(levels) / BINS_PER_SECOND
            first = (block.left() - left) / max(1e-6, right - left) * seconds * BINS_PER_SECOND
            last = (block.right() - left) / max(1e-6, right - left) * seconds * BINS_PER_SECOND
            peaks = levels.max(axis=1)
            count = max(1, int(block.width()))
            edges = np.linspace(max(0, first), min(len(peaks), last), count + 1).astype(int)
            values = np.array([peaks[a:max(a + 1, b)].max() if a < len(peaks) else 0 for a, b in zip(edges[:-1], edges[1:])])
            reference = max(float(np.percentile(peaks, 99)), 1e-4)
            paint_envelope(painter, block.adjusted(0, 3, 0, -3), np.clip(values / reference, 0, 1),
                           T.TEXT_2 if current else T.TEXT_3, .9 if current else .5)
        painter.setPen(QPen(QColor(T.LINE_STRONG), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(left, rect.top(), right - left, rect.height()).adjusted(.5, .5, -.5, -.5))
        if not current:
            painter.setFont(T.font(11))
            painter.setPen(QColor(T.TEXT_2))
            painter.drawText(block.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             "Changed since this render")

    # Interaction
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        if point.x() >= self.area_width():
            return
        for rect, index in self._labels:
            if rect.contains(point):
                self.transitionSelected.emit(index)
                return
        if point.y() < self.RULER:
            self.ruler_drag = (point, self.time_at(point.x()), self.scale, False)
            return
        self.drag_origin, self.drag_clip = point, None
        for index, position in enumerate(self.positions):
            if self.clip_rect(position, index).contains(point):
                self.drag_clip = position["clip_id"]
                self.selected.emit(self.drag_clip)
                return
        self.seekRequested.emit(self.time_at(point.x()))

    def mouseMoveEvent(self, event):
        point = event.position()
        if self.ruler_drag:
            origin, moment, scale, _ = self.ruler_drag
            delta = point - origin
            if delta.manhattanLength() > 3:
                self.ruler_drag = (origin, moment, scale, True)
                # Drag down to zoom in, up to zoom out, sideways to scroll: as in a DAW ruler.
                self.scale = min(400.0, max(.05, scale * 1.012 ** delta.y()))
                self.refresh()
                self.horizontalScrollBar().setValue(round(self.PAD + moment * self.scale - point.x()))
            return
        if not event.buttons():
            if point.x() >= self.area_width():
                self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            elif any(rect.contains(point) for rect, _ in self._labels):
                self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            elif point.y() < self.RULER:
                self.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
            elif any(self.clip_rect(p, i).contains(point) for i, p in enumerate(self.positions)):
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self.drag_clip and self.drag_origin and (point - self.drag_origin).manhattanLength() > 8:
            index = next(i for i, p in enumerate(self.positions) if p["clip_id"] == self.drag_clip)
            rect = self.clip_rect(self.positions[index], index).intersected(QRectF(0, 0, self.area_width(), self.viewport().height()))
            data = QMimeData()
            data.setData(CLIP_MIME, self.drag_clip.encode())
            drag = QDrag(self)
            drag.setMimeData(data)
            if self._static is not None and rect.width() > 4:
                ratio = self._static.devicePixelRatio()
                image = self._static.copy(int(rect.left() * ratio), int(rect.top() * ratio),
                                          int(min(rect.width(), 360) * ratio), int(rect.height() * ratio))
                image.setDevicePixelRatio(ratio)
                faded = QPixmap(image.size())
                faded.setDevicePixelRatio(ratio)
                faded.fill(Qt.GlobalColor.transparent)
                fader = QPainter(faded)
                fader.setOpacity(.75)
                fader.drawPixmap(0, 0, image)
                fader.end()
                drag.setPixmap(faded)
                drag.setHotSpot((self.drag_origin - rect.topLeft()).toPoint())
            self.drag_clip = None
            drag.exec(Qt.DropAction.MoveAction)

    def mouseReleaseEvent(self, event):
        if self.ruler_drag:
            origin, moment, _, moved = self.ruler_drag
            self.ruler_drag = None
            if not moved:
                self.seekRequested.emit(moment)

    def wheelEvent(self, event):
        modifiers = event.modifiers()
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self.zoom(1.0015 ** event.angleDelta().y(), event.position().x())
            event.accept()
            return
        delta = event.pixelDelta() if not event.pixelDelta().isNull() else event.angleDelta()
        amount = delta.x() if abs(delta.x()) > abs(delta.y()) else delta.y()
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - amount)
        event.accept()

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.NativeGesture and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.zoom(1 + event.value(), event.position().x())
            return True
        return super().viewportEvent(event)

    def insertion(self, x):
        seconds = self.time_at(x)
        for index, position in enumerate(self.positions):
            if seconds < (position["start"] + position["length"] / 2) / djmix.SR:
                return index
        return len(self.positions)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TRACK_MIME) or event.mimeData().hasFormat(CLIP_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        self.drop_index = self.insertion(min(event.position().x(), self.area_width()))
        self.viewport().update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.drop_index = None
        self.viewport().update()

    def dropEvent(self, event):
        index = self.insertion(min(event.position().x(), self.area_width()))
        mime = event.mimeData()
        self.drop_index = None
        self.viewport().update()
        if mime.hasFormat(TRACK_MIME):
            self.trackDropped.emit(bytes(mime.data(TRACK_MIME)).decode(), index)
        elif mime.hasFormat(CLIP_MIME):
            self.clipMoved.emit(bytes(mime.data(CLIP_MIME)).decode(), index)
        event.acceptProposedAction()


class Overview(QWidget):
    """The whole mix in one strip: lane A above, lane B below, with the visible range outlined."""

    navigate = Signal(float)

    def __init__(self, timeline: Timeline, parent=None):
        super().__init__(parent)
        self.timeline = timeline
        self.playhead = None
        self._static = None
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Overview of the whole mix. Click or drag to move there.")
        self.setAccessibleName("Mix overview")
        timeline.viewChanged.connect(self.update)

    def invalidate(self):
        self._static = None
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._static = None

    def span(self):
        return self.width() - self.timeline.HEADERS

    def _x(self, seconds, duration):
        return seconds / duration * self.span()

    def _render(self):
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QColor(T.LANE))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        timeline = self.timeline
        duration = timeline.duration()
        half = (self.height() - 3) / 2
        if duration > 0:
            for index, (clip, position) in enumerate(zip(timeline.session.clips, timeline.positions)):
                track = timeline.session.tracks[clip.track_id]
                base = T.clip_color(track.id)
                left = self._x(position["start"] / djmix.SR, duration)
                right = self._x(position["end"] / djmix.SR, duration)
                rect = QRectF(left, 1 + (index % 2) * (half + 1), max(1, right - left), half)
                painter.fillRect(rect, T.mix(base, T.LANE, .72))
                columns = band_columns(timeline.bands.get(track.id), clip.cue_in * BINS_PER_SECOND,
                                       clip.cue_out * BINS_PER_SECOND, max(1, int(rect.width())))
                if columns is not None:
                    paint_envelope(painter, rect.adjusted(0, 1, 0, -1), np.clip(columns[:, 0] * .9, 0, 1), base)
        painter.fillRect(QRectF(self.span(), 0, self.width() - self.span(), self.height()), QColor(T.GUTTER))
        painter.end()
        return pixmap

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._static is None:
            self._static = self._render()
        painter.drawPixmap(0, 0, self._static)
        duration = self.timeline.duration()
        if duration > 0:
            first, last = self.timeline.visible_range()
            left, right = self._x(first, duration), self._x(min(last, duration), duration)
            painter.setPen(QPen(QColor(T.TEXT), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(left, .5, max(3, right - left), self.height() - 1))
            if self.playhead is not None:
                painter.fillRect(QRectF(self._x(self.playhead, duration) - .5, 0, 1, self.height()), QColor("#F2F2F2"))

    def _go(self, x):
        duration = self.timeline.duration()
        if duration > 0 and x < self.span():
            self.navigate.emit(min(duration, max(0.0, x / max(1, self.span()) * duration)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._go(event.position().x())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._go(event.position().x())


class CueEditor(QWidget):
    """Sample display: the whole song with Start and End markers that snap to bars of the analyzed grid."""

    cuesChanged = Signal(float, float)

    RULER = 16

    def __init__(self, bands, parent=None):
        super().__init__(parent)
        self.bands = bands
        self.track = self.clip = None
        self.blend_in = self.blend_out = 0
        self.dragging = None
        self.preview = None
        self.setMinimumHeight(90)
        self.setMouseTracking(True)
        self.setAccessibleName("Start and end markers")

    def set_clip(self, track, clip, blend_in_bars, blend_out_bars):
        self.track, self.clip = track, clip
        self.blend_in, self.blend_out = blend_in_bars, blend_out_bars
        self.preview = None
        self.update()

    def cues(self):
        return self.preview if self.preview else (self.clip.cue_in, self.clip.cue_out)

    def wave_rect(self):
        return QRectF(0, self.RULER, self.width(), self.height() - self.RULER)

    def x_of(self, seconds):
        return seconds / self.track.duration * self.width()

    def time_at(self, x):
        return min(self.track.duration, max(0.0, x / self.width() * self.track.duration))

    def snap(self, seconds, modifiers):
        if modifiers & Qt.KeyboardModifier.AltModifier or not self.track.bpm:
            return seconds
        step = 60 / self.track.bpm if modifiers & Qt.KeyboardModifier.ShiftModifier else 240 / self.track.bpm
        origin = self.track.cue_in
        return min(self.track.duration, max(0.0, origin + round((seconds - origin) / step) * step))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        wave = self.wave_rect()
        painter.fillRect(QRectF(0, 0, self.width(), self.RULER), QColor(T.RAISED))
        painter.fillRect(wave, QColor(T.FIELD))
        if not self.track:
            return
        base = T.clip_color(self.track.id)
        columns = band_columns(self.bands.get(self.track.id), 0, self.track.duration * BINS_PER_SECOND,
                               max(1, int(wave.width())))
        if columns is not None:
            paint_bands(painter, wave.adjusted(0, 4, 0, -4), columns, T.clip_bands(base))
        else:
            paint_envelope(painter, wave.adjusted(0, 4, 0, -4),
                           peak_columns(self.track.waveform, 0, 1, max(1, int(wave.width() / 2))), T.mix(base, T.LANE, .4))
        cue_in, cue_out = self.cues()
        left, right = self.x_of(cue_in), self.x_of(cue_out)
        shade = T.color("#000000", .62)
        painter.fillRect(QRectF(0, wave.top(), left, wave.height()), shade)
        painter.fillRect(QRectF(right, wave.top(), self.width() - right, wave.height()), shade)
        bar = 240 / self.track.bpm if self.track.bpm else 0
        for bars, at_start in ((self.blend_in, True), (self.blend_out, False)):
            if bars and bar:
                extent = bars * bar / self.track.duration * self.width()
                zone = QRectF(left, wave.top(), extent, wave.height()) if at_start else QRectF(right - extent, wave.top(), extent, wave.height())
                painter.fillRect(zone, T.color("#FFFFFF", .08))
        if bar:
            pixels = bar / self.track.duration * self.width()
            every = 1
            while every * pixels < 34:
                every *= 2
            index = math.ceil(-self.track.cue_in / bar / every) * every
            painter.setFont(T.font(10, tabular=True))
            while True:
                seconds = self.track.cue_in + index * bar
                if seconds > self.track.duration:
                    break
                x = self.x_of(seconds)
                painter.fillRect(QRectF(x, 3, 1, self.RULER - 4), QColor(T.LINE_STRONG))
                painter.setPen(QColor(T.TEXT_3))
                painter.drawText(QRectF(x + 3, 0, 40, self.RULER), Qt.AlignmentFlag.AlignVCenter, str(index + 1))
                index += every
        for name, x in (("Start", left), ("End", right)):
            ink = QColor(T.ACCENT_HOVER if self.dragging == name else T.ACCENT)
            painter.fillRect(QRectF(x - .5, 0, 1, self.height()), ink)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            if name == "Start":
                flag = QPolygonF([QPointF(x, 0), QPointF(x + 9, 0), QPointF(x + 9, 7), QPointF(x, 12)])
            else:
                flag = QPolygonF([QPointF(x, 0), QPointF(x - 9, 0), QPointF(x - 9, 7), QPointF(x, 12)])
            painter.drawPolygon(flag)

    def _handle_at(self, x):
        if not self.track:
            return None
        cue_in, cue_out = self.cues()
        distances = {"Start": abs(x - self.x_of(cue_in)), "End": abs(x - self.x_of(cue_out))}
        name = min(distances, key=distances.get)
        return name if distances[name] <= 9 else None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled() and self.track:
            self.dragging = self._handle_at(event.position().x())
            if self.dragging:
                self.preview = (self.clip.cue_in, self.clip.cue_out)
                self.update()

    def mouseMoveEvent(self, event):
        if not self.track:
            return
        x = event.position().x()
        if not self.dragging:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._handle_at(x) else Qt.CursorShape.ArrowCursor)
            return
        seconds = self.snap(self.time_at(x), event.modifiers())
        minimum = 240 / self.track.bpm if self.track.bpm else 1
        cue_in, cue_out = self.preview
        if self.dragging == "Start":
            cue_in = min(seconds, cue_out - minimum)
        else:
            cue_out = max(seconds, cue_in + minimum)
        self.preview = (max(0.0, cue_in), min(self.track.duration, cue_out))
        self.update()

    def mouseReleaseEvent(self, event):
        if self.dragging and self.preview:
            cue_in, cue_out = self.preview
            changed = (cue_in, cue_out) != (self.clip.cue_in, self.clip.cue_out)
            self.dragging = None
            if changed:
                self.cuesChanged.emit(cue_in, cue_out)
            else:
                self.preview = None
            self.update()


# Browser ----------------------------------------------------------------------------------

class LibraryModel(QAbstractTableModel):
    TrackRole = Qt.ItemDataRole.UserRole + 1
    COLUMNS = ("Name", "BPM", "Key", "Time")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.ids = []
        self.query = ""
        self.order = (-1, Qt.SortOrder.AscendingOrder)
        self.context = None
        self.context_track = None
        self.arranged = {}

    def rebuild(self):
        self.beginResetModel()
        tracks = list(self.session.tracks.values()) if self.session else []
        needle = self.query.casefold().strip()
        if needle:
            tracks = [t for t in tracks if needle in t.name.casefold()]
        column, direction = self.order

        def key_order(track):
            code = camelot(track.key)
            return (not code, int(code[:-1]) if code else 0, code[-1:] if code else "")

        sorters = {0: lambda t: t.name.casefold(), 1: lambda t: (t.bpm is None, t.bpm or 0),
                   2: key_order, 3: lambda t: t.duration}
        if column in sorters:
            tracks.sort(key=sorters[column], reverse=direction == Qt.SortOrder.DescendingOrder)
        self.ids = [t.id for t in tracks]
        self.arranged = {}
        if self.session:
            for index, clip in enumerate(self.session.clips):
                self.arranged.setdefault(clip.track_id, []).append(index + 1)
        self.endResetModel()

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self.order = (column, order)
        self.rebuild()

    def track(self, row):
        return self.session.tracks.get(self.ids[row]) if self.session and 0 <= row < len(self.ids) else None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.ids)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self.COLUMNS[section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int((Qt.AlignmentFlag.AlignLeft if section == 0 else Qt.AlignmentFlag.AlignRight)
                           | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        track = self.track(index.row())
        if track is None:
            return None
        if role == self.TrackRole:
            return track.id
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            column = index.column()
            if column == 0:
                return track.name
            if column == 1:
                return f"{track.bpm:.1f}" if track.bpm else ""
            if column == 2:
                return f"{camelot(track.key)} {display_key(track.key, True)}" if track.key else ""
            return clock(track.duration)
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [track.name, track.status]
            if self.arranged.get(track.id):
                lines.append("In the mix as song " + ", ".join(map(str, self.arranged[track.id])))
            relation = self.relation(track)
            if relation:
                lines.append(f"Fits the selected clip: {relation}")
            lines.append(track.path)
            return "\n".join(lines)
        return None

    def relation(self, track):
        if not self.context or track.id == self.context_track:
            return None
        other = parsed_key(track.key)
        return djmix.relationship(self.context, other) if other else None

    def flags(self, index):
        base = super().flags(index)
        return base | Qt.ItemFlag.ItemIsDragEnabled if index.isValid() else base

    def mimeTypes(self):
        return [TRACK_MIME]

    def mimeData(self, indexes):
        data = QMimeData()
        if indexes:
            data.setData(TRACK_MIME, self.ids[indexes[0].row()].encode())
        return data


class LibraryDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        model = index.model()
        track = model.track(index.row())
        if track is None:
            return
        painter.save()
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.fillRect(rect, QColor(T.SELECTION if selected else "#292929"))
        column = index.column()
        text_rect = rect.adjusted(6, 0, -6, 0)
        pending = track.bpm is None and track.status == "Pending analysis"
        font = T.font(12, tabular=column != 0)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if column == 0 else Qt.AlignmentFlag.AlignRight)
        if column == 0:
            if model.arranged.get(track.id):
                painter.fillRect(QRectF(text_rect.left(), rect.center().y() - 4, 8, 8), QColor(T.clip_color(track.id)))
            text_rect.setLeft(text_rect.left() + 14)
            painter.setPen(QColor(T.TEXT))
            painter.drawText(text_rect, align, metrics.elidedText(track.name, Qt.TextElideMode.ElideRight, int(text_rect.width())))
        elif pending and column in (1, 2):
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(T.LINE_STRONG))
            width = 30 if column == 1 else 40
            painter.drawRoundedRect(QRectF(text_rect.right() - width, rect.center().y() - 4, width, 8), 2, 2)
        elif column == 1:
            painter.setPen(QColor(T.TEXT if track.bpm else T.BAD))
            painter.drawText(text_rect, align, f"{track.bpm:.1f}" if track.bpm else "Needs BPM")
        elif column == 2:
            text = f"{camelot(track.key)} {display_key(track.key, True)}" if track.key else "?"
            painter.setPen(QColor(T.TEXT_2 if track.key else T.TEXT_3))
            painter.drawText(text_rect, align, text)
            if model.relation(track):
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(T.GOOD))
                painter.drawEllipse(QPointF(text_rect.right() - metrics.horizontalAdvance(text) - 8, rect.center().y()), 3, 3)
        else:
            painter.setPen(QColor(T.TEXT_3))
            painter.drawText(text_rect, align, clock(track.duration))
        painter.restore()


class LibraryTable(QTableView):
    addRequested = Signal(str)
    filesDropped = Signal(list)

    def __init__(self, model: LibraryModel, parent=None):
        super().__init__(parent)
        self.setModel(model)
        self.setItemDelegate(LibraryDelegate(self))
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(22)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 52), (2, 64), (3, 44)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(column, width)
        header.setFixedHeight(22)
        header.sectionClicked.connect(self._sort)
        self.file_hover = False
        self.doubleClicked.connect(lambda index: self.addRequested.emit(model.data(index, LibraryModel.TrackRole)))
        self.setAccessibleName("Browser")

    def _sort(self, column):
        model = self.model()
        current, order = model.order
        order = (Qt.SortOrder.DescendingOrder if current == column and order == Qt.SortOrder.AscendingOrder
                 else Qt.SortOrder.AscendingOrder)
        identity = self.current_id()
        model.sort(column, order)
        self.horizontalHeader().setSortIndicator(column, order)
        if identity:
            self.select_id(identity)

    def current_id(self):
        index = self.currentIndex()
        return self.model().data(index, LibraryModel.TrackRole) if index.isValid() else None

    def select_id(self, identity):
        model = self.model()
        if identity in model.ids:
            self.selectRow(model.ids.index(identity))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.current_id():
            self.addRequested.emit(self.current_id())
            return
        super().keyPressEvent(event)

    def startDrag(self, actions):
        index = self.currentIndex()
        if not index.isValid():
            return
        drag = QDrag(self)
        drag.setMimeData(self.model().mimeData([index]))
        row = self.visualRect(index.siblingAtColumn(0)).united(self.visualRect(index.siblingAtColumn(3)))
        drag.setPixmap(self.viewport().grab(row))
        drag.exec(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self.file_hover = True
            self.viewport().update()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.file_hover = False
        self.viewport().update()

    def dropEvent(self, event):
        self.file_hover = False
        self.viewport().update()
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        area = QRectF(self.viewport().rect()).adjusted(3, 3, -3, -3)
        if self.model().rowCount() == 0:
            searching = bool(self.model().query.strip())
            painter.setFont(T.font(12, QFont.Weight.DemiBold))
            painter.setPen(QColor(T.TEXT))
            painter.drawText(QRectF(area.left() + 10, area.top() + 30, area.width() - 20, 20), Qt.AlignmentFlag.AlignCenter,
                             "No matching songs" if searching else "Your library is empty")
            painter.setFont(T.font(11))
            painter.setPen(QColor(T.TEXT_2))
            painter.drawText(QRectF(area.left() + 14, area.top() + 54, area.width() - 28, 60),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                             "Try another part of the title." if searching else
                             "Drop audio files or a folder here, or use Add folder below.")
        if self.file_hover:
            pen = QPen(QColor(T.ACCENT), 1.5, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([4, 3])
            painter.setPen(pen)
            painter.setBrush(T.color(T.ACCENT, .05))
            painter.drawRect(area)
        painter.end()


class BrowserPreview(QWidget):
    """Waveform of the song selected in the browser. Drag it onto the arrangement to add the song."""

    def __init__(self, bands, parent=None):
        super().__init__(parent)
        self.bands = bands
        self.track = None
        self.origin = None
        self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag onto the arrangement to add this song")

    def set_track(self, track):
        self.track = track
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(T.FIELD))
        if not self.track:
            painter.setFont(T.font(11))
            painter.setPen(QColor(T.TEXT_3))
            painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, "Select a song to see its waveform")
            return
        painter.setFont(T.font(11, QFont.Weight.Medium))
        painter.setPen(QColor(T.TEXT_2))
        metrics = painter.fontMetrics()
        painter.drawText(QRectF(6, 2, self.width() - 60, 14), Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(self.track.name, Qt.TextElideMode.ElideRight, self.width() - 60))
        painter.setFont(T.font(11, tabular=True))
        painter.drawText(QRectF(self.width() - 50, 2, 44, 14), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                         clock(self.track.duration))
        wave = QRectF(0, 17, self.width(), self.height() - 19)
        base = T.clip_color(self.track.id)
        columns = band_columns(self.bands.get(self.track.id), 0, self.track.duration * BINS_PER_SECOND,
                               max(1, int(wave.width())))
        if columns is not None:
            paint_bands(painter, wave, columns, T.clip_bands(base))
        else:
            paint_envelope(painter, wave, peak_columns(self.track.waveform, 0, 1, max(1, int(wave.width() / 2))), T.TEXT_3, .6)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position()

    def mouseMoveEvent(self, event):
        if self.track and self.origin and (event.position() - self.origin).manhattanLength() > 6:
            data = QMimeData()
            data.setData(TRACK_MIME, self.track.id.encode())
            drag = QDrag(self)
            drag.setMimeData(data)
            drag.setPixmap(self.grab())
            self.origin = None
            drag.exec(Qt.DropAction.CopyAction)
