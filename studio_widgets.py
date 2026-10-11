"""Custom-painted widgets for DJ Studio: timeline, overview, cue editor, library and controls."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import (QAbstractListModel, QEasingCurve, QEvent, QMimeData, QModelIndex, QPoint, QPointF,
                            QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QValidator
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractScrollArea, QAbstractSpinBox,
                               QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLayout, QListView, QPushButton,
                               QSizePolicy, QSpinBox, QStyle, QStyledItemDelegate, QToolButton, QWidget)

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


def band_columns(bands, first_bin: float, last_bin: float, columns: int):
    """Max-pool band envelopes into pixel columns, returning (columns, 3) or None."""
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


def paint_bands(painter: QPainter, rect: QRectF, columns, alpha: float = 1.0):
    """Mirrored three-band waveform, low band behind, high band in front."""
    if columns is None or not len(columns):
        return
    count = len(columns)
    xs = rect.left() + (np.arange(count) + 0.5) * rect.width() / count
    xs = np.concatenate(([rect.left()], xs, [rect.right()]))
    middle, half = rect.center().y(), rect.height() / 2
    painter.setPen(Qt.PenStyle.NoPen)
    for band in range(3):
        amplitude = np.concatenate(([0.0], np.maximum(columns[:, band] * BAND_WEIGHTS[band], 0.012), [0.0])) * half
        top = [QPointF(x, middle - a) for x, a in zip(xs, amplitude)]
        bottom = [QPointF(x, middle + a) for x, a in zip(xs[::-1], amplitude[::-1])]
        painter.setBrush(T.color(T.BANDS[band], alpha))
        painter.drawPolygon(QPolygonF(top + bottom))


def paint_peaks(painter: QPainter, rect: QRectF, waveform, start: float, end: float, alpha=.5):
    """Placeholder from the coarse library peaks while band data loads."""
    if not waveform or rect.width() < 2:
        return
    count = max(1, int(rect.width() / 2))
    values = np.asarray(waveform, dtype=float)
    positions = np.clip((start + (np.arange(count) + .5) / count * (end - start)) * len(values), 0, len(values) - 1)
    columns = np.zeros((count, 3))
    columns[:, 0] = values[positions.astype(int)] * .8
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(T.color(T.TEXT_3, alpha))
    middle, half = rect.center().y(), rect.height() / 2
    xs = rect.left() + (np.arange(count) + .5) * rect.width() / count
    top = [QPointF(x, middle - max(.6, a * half)) for x, a in zip(xs, columns[:, 0])]
    bottom = [QPointF(x, middle + max(.6, a * half)) for x, a in zip(xs[::-1], columns[::-1, 0])]
    painter.drawPolygon(QPolygonF(top + bottom))
    painter.restore()


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

class SpinBox(QSpinBox):
    """Ignores the scroll wheel unless focused, so scrolling a panel never edits values."""

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class DoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ComboBox(QComboBox):
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class SignedSpinBox(SpinBox):
    """Semitone field: shows +2 and -3, and plain 0."""

    def textFromValue(self, value):
        return f"{value:+d}" if value else "0"

    def valueFromText(self, text):
        try:
            return int(text.replace("+", "").strip() or 0)
        except ValueError:
            return self.value()

    def validate(self, text, position):
        stripped = text.replace("+", "").strip()
        if stripped in ("", "-"):
            return QValidator.State.Intermediate, text, position
        try:
            value = int(stripped)
        except ValueError:
            return QValidator.State.Invalid, text, position
        state = QValidator.State.Acceptable if self.minimum() <= value <= self.maximum() else QValidator.State.Intermediate
        return state, text, position


class TimeSpinBox(DoubleSpinBox):
    """Seconds shown as m:ss.mmm; plain seconds are accepted when typing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDecimals(3)
        self.setRange(0, 3600)

    def textFromValue(self, value):
        total = int(round(value * 1000))
        return f"{total // 60000}:{total % 60000 / 1000:06.3f}"

    @staticmethod
    def parse(text):
        text = text.strip()
        if ":" in text:
            minutes, seconds = text.split(":", 1)
            return int(minutes or 0) * 60 + float(seconds or 0)
        return float(text)

    def valueFromText(self, text):
        try:
            return self.parse(text)
        except ValueError:
            return self.value()

    def validate(self, text, position):
        try:
            value = self.parse(text)
            state = QValidator.State.Acceptable if self.minimum() <= value <= self.maximum() else QValidator.State.Intermediate
        except ValueError:
            state = QValidator.State.Intermediate
        return state, text, position


def tool_button(icon_name, tooltip, callback=None, size=16):
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


class Stepper(QWidget):
    """A field flanked by minus and plus buttons. The step may depend on context (one bar)."""

    def __init__(self, spin: QAbstractSpinBox, step=None, label="value", parent=None):
        super().__init__(parent)
        self.spin, self.step = spin, step
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setKeyboardTracking(False)
        spin.setFont(T.font(T.SIZE_BODY, tabular=True))
        spin.setMinimumWidth(56)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.minus = tool_button("minus", f"Decrease {label}", lambda: self.nudge(-1), 14)
        self.plus = tool_button("plus", f"Increase {label}", lambda: self.nudge(1), 14)
        for widget in (self.minus, self.plus):
            widget.setAutoRepeat(True)
            widget.setAutoRepeatDelay(350)
            widget.setAutoRepeatInterval(110)
        row.addWidget(self.minus)
        row.addWidget(spin, 1)
        row.addWidget(self.plus)

    def nudge(self, direction):
        amount = self.step() if callable(self.step) else self.spin.singleStep()
        self.spin.setValue(self.spin.value() + direction * amount)


class FlowLayout(QLayout):
    """Lays items out in rows and wraps them when the width runs out. Rows are vertically centered."""

    def __init__(self, parent=None, spacing=12, row_spacing=8):
        super().__init__(parent)
        self._items = []
        self._spacing, self._row_spacing = spacing, row_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        width = sum(item.sizeHint().width() for item in self._items if not item.isEmpty())
        width += self._spacing * max(0, len(self._items) - 1)
        height = max((item.sizeHint().height() for item in self._items if not item.isEmpty()), default=0)
        return QSize(width, height)

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect, apply):
        rows, row, used = [], [], 0
        for item in self._items:
            if item.isEmpty():
                continue
            width = min(item.sizeHint().width(), rect.width())
            if row and used + self._spacing + width > rect.width():
                rows.append(row)
                row, used = [], 0
            used += (self._spacing if row else 0) + width
            row.append((item, width))
        if row:
            rows.append(row)
        y = rect.y()
        for index, row in enumerate(rows):
            height = max(item.sizeHint().height() for item, _ in row)
            x = rect.x()
            for item, width in row:
                if apply:
                    item_height = item.sizeHint().height()
                    item.setGeometry(QRect(QPoint(x, y + (height - item_height) // 2), QSize(width, item_height)))
                x += width + self._spacing
            y += height + (self._row_spacing if index < len(rows) - 1 else 0)
        return y - rect.y()


def group(*widgets, spacing=8):
    """Keep related widgets together inside a wrapping row."""
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    for widget in widgets:
        row.addWidget(widget)
    return holder


class Switch(QAbstractButton):
    """Label on the left, switch on the right. The knob slides in 160 ms unless motion is reduced."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._position = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(0 if T.reduced_motion() else 160)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._moved)
        self.toggled.connect(self._animate)

    def _moved(self, value):
        self._position = float(value)
        self.update()

    def _animate(self, checked):
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def set_state(self, checked):
        """Programmatic update: no signal, no animation."""
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self._animation.stop()
        self._position = 1.0 if checked else 0.0
        self.update()

    def sizeHint(self):
        metrics = QFontMetricsF(self.font())
        return QSize(int(metrics.horizontalAdvance(self.text())) + 46, 28)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, position):
        return self.rect().contains(position)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        track = QRectF(self.width() - 32, (self.height() - 18) / 2, 30, 18)
        painter.setFont(self.font())
        painter.setPen(QColor(T.TEXT if enabled else T.TEXT_3))
        text = painter.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, max(0, int(track.left() - 10)))
        painter.drawText(QRectF(0, 0, track.left() - 10, self.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        off = QColor(T.EDGE_HOVER if self.underMouse() and enabled else T.LINE_STRONG)
        on = QColor(T.ACCENT_HOVER if self.underMouse() and enabled else T.ACCENT)
        t = self._position
        fill = QColor(round(off.red() + (on.red() - off.red()) * t), round(off.green() + (on.green() - off.green()) * t),
                      round(off.blue() + (on.blue() - off.blue()) * t))
        if not enabled:
            fill.setAlphaF(.4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(track, 9, 9)
        knob = QRectF(track.left() + 2 + t * (track.width() - 18), track.top() + 2, 14, 14)
        painter.setBrush(T.color(T.ON_ACCENT if t > .5 else T.TEXT, 1 if enabled else .5))
        painter.drawEllipse(knob)
        if self.hasFocus():
            painter.setPen(QPen(QColor(T.ACCENT), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(-3, -3, 3, 3), 12, 12)


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
    """Stereo peak meter, 2 dB segments from -44 dBFS, with a 1.2 s peak hold."""

    SEGMENTS, FLOOR = 22, -44.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(104, 20)
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

    @staticmethod
    def tint(threshold):
        if threshold >= -1:
            return T.BAD
        if threshold >= -6:
            return T.ACCENT
        if threshold >= -18:
            return T.MID
        return T.LOW

    def paintEvent(self, event):
        painter = QPainter(self)
        gap = 2
        width = (self.width() - gap * (self.SEGMENTS - 1)) / self.SEGMENTS
        for channel, top in enumerate((4, 12)):
            for segment in range(self.SEGMENTS):
                threshold = self.FLOOR + (segment + 1) * (-self.FLOOR / self.SEGMENTS)
                lit = self.levels[channel] >= threshold - 1e-9
                peak = abs(self.peaks[channel] - threshold) < -self.FLOOR / self.SEGMENTS / 2 and self.peaks[channel] > self.FLOOR + 2
                rect = QRectF(segment * (width + gap), top, width, 4)
                painter.fillRect(rect, QColor(self.tint(threshold)) if lit or peak else T.color(T.LINE_STRONG, .7))


class Notice(QFrame):
    """Inline message with optional actions. Info notices dismiss themselves after a few seconds."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notice")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 8, 8)
        row.setSpacing(10)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.message, 1)
        self.actions_row = QHBoxLayout()
        self.actions_row.setSpacing(6)
        row.addLayout(self.actions_row)
        self.close_button = tool_button("close", "Dismiss", self.hide, 14)
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
    """The arrangement: clips on two alternating lanes, overlaps drawn with their gain curves."""

    trackDropped = Signal(str, int)
    clipMoved = Signal(str, int)
    selected = Signal(str)
    seekRequested = Signal(float)
    transitionSelected = Signal(int)
    viewChanged = Signal()

    PAD, RULER, GAP, BOTTOM, HEADER = 18, 30, 30, 16, 26

    def __init__(self, session, bands, parent=None):
        super().__init__(parent)
        self.session, self.bands = session, bands
        self.selection, self.transition = "", 0
        self.harmony = {}
        self.scale = 2.0
        self.playhead = None
        self.rendered = None
        self.drop_index = None
        self.drag_origin = self.drag_clip = None
        self.positions = []
        self._labels = []
        self._static = None
        self.setAcceptDrops(True)
        self.setMinimumHeight(250)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.horizontalScrollBar().valueChanged.connect(self._scrolled)
        self.viewport().setMouseTracking(True)
        self.setAccessibleName("Arrangement timeline")
        self.setAccessibleDescription("Drag songs here from the library. Drag clips to reorder them.")

    # Geometry
    def duration(self):
        return self.positions[-1]["end"] / djmix.SR if self.positions else 0.0

    def x(self, seconds):
        return self.PAD + seconds * self.scale - self.horizontalScrollBar().value()

    def time_at(self, x):
        return max(0.0, (x - self.PAD + self.horizontalScrollBar().value()) / self.scale)

    def lanes(self):
        top = self.RULER + 14
        height = max(56.0, (self.viewport().height() - top - self.BOTTOM - self.GAP) / 2)
        return top, height

    def clip_rect(self, position, index):
        top, height = self.lanes()
        return QRectF(self.x(position["start"] / djmix.SR), top + (index % 2) * (height + self.GAP),
                      position["length"] / djmix.SR * self.scale, height)

    def visible_range(self):
        return self.time_at(0), self.time_at(self.viewport().width())

    # State
    def refresh(self):
        try:
            self.positions = layout(self.session)
        except djmix.MixError:
            self.positions = []
        span = max(self.duration(), 60)
        bar = self.horizontalScrollBar()
        bar.setRange(0, max(0, round(span * self.scale + 2 * self.PAD - self.viewport().width())))
        bar.setPageStep(self.viewport().width())
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
        self.scale = max(.05, (self.viewport().width() - 2 * self.PAD) / max(30.0, self.duration()))
        self.refresh()
        self.horizontalScrollBar().setValue(0)

    def zoom(self, factor, anchor=None):
        anchor = self.viewport().width() / 2 if anchor is None else anchor
        moment = self.time_at(anchor)
        self.scale = min(400.0, max(.05, self.scale * factor))
        self.refresh()
        self.horizontalScrollBar().setValue(round(self.PAD + moment * self.scale - anchor))

    def center_on(self, seconds):
        self.horizontalScrollBar().setValue(round(self.PAD + seconds * self.scale - self.viewport().width() / 2))

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
        height = self.viewport().height()
        if self.drop_index is not None:
            if self.positions:
                index = self.drop_index
                seconds = self.positions[index]["start"] / djmix.SR if index < len(self.positions) else self.duration()
                x = self.x(seconds)
            else:
                x = self.PAD
            painter.setPen(QPen(QColor(T.ACCENT), 2))
            painter.drawLine(QPointF(x, self.RULER), QPointF(x, height - 4))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(T.ACCENT))
            painter.drawPolygon(QPolygonF([QPointF(x - 5, self.RULER - 7), QPointF(x + 5, self.RULER - 7), QPointF(x, self.RULER - 1)]))
        if self.playhead is not None and self.positions:
            x = self.x(self.playhead)
            if -4 <= x <= self.viewport().width() + 4:
                painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
                painter.drawLine(QPointF(x, self.RULER - 2), QPointF(x, height - 4))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#FFFFFF"))
                painter.drawPolygon(QPolygonF([QPointF(x - 5, self.RULER - 10), QPointF(x + 5, self.RULER - 10), QPointF(x, self.RULER - 3)]))
        painter.end()

    def _render_static(self):
        ratio = self.viewport().devicePixelRatioF()
        size = self.viewport().size()
        pixmap = QPixmap(max(1, int(size.width() * ratio)), max(1, int(size.height() * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QColor(T.INK))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_ruler(painter, size.width(), size.height())
        self._labels = []
        if self.positions:
            for index, (clip, position) in enumerate(zip(self.session.clips, self.positions)):
                self._paint_clip(painter, index, clip, position, size.width())
            for index in range(1, len(self.positions)):
                self._paint_overlap(painter, index, size.width())
        else:
            self._paint_empty(painter, size.width(), size.height())
        painter.end()
        return pixmap

    def _paint_ruler(self, painter, width, height):
        painter.fillRect(QRectF(0, 0, width, self.RULER), QColor("#0D0F1D"))
        painter.fillRect(QRectF(0, self.RULER - 1, width, 1), QColor(T.LINE))
        bar = 240 / self.session.target_bpm
        major_bars = 1
        while major_bars * bar * self.scale < 72:
            major_bars *= 2
        minor_bars = major_bars / 4 if major_bars >= 4 else 1
        while minor_bars * bar * self.scale < 7:
            minor_bars *= 2
        first, last = self.time_at(0), self.time_at(width)
        top, lane = self.lanes()
        bottom = top + 2 * lane + self.GAP
        painter.setFont(T.font(T.SIZE_SMALL, tabular=True))
        step = minor_bars * bar
        tick = math.floor(first / step) * step
        while tick <= last + step:
            x = self.x(tick)
            bars_from_start = round(tick / bar)
            if bars_from_start % major_bars == 0:
                painter.fillRect(QRectF(x, self.RULER - 9, 1, 8), QColor(T.LINE_STRONG))
                painter.fillRect(QRectF(x, top - 6, 1, bottom - top + 6), T.color("#FFFFFF", .028))
                painter.setPen(QColor(T.TEXT_3))
                painter.drawText(QRectF(x + 5, 4, 80, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, clock(tick))
            else:
                painter.fillRect(QRectF(x, self.RULER - 5, 1, 4), QColor(T.LINE_STRONG))
            tick += step
        if self.rendered:
            left, right = self.x(self.rendered[0]), self.x(self.rendered[1])
            painter.fillRect(QRectF(left, self.RULER - 3, max(1, right - left), 2), T.color(T.ACCENT, .85))

    def _paint_clip(self, painter, index, clip, position, width):
        rect = self.clip_rect(position, index)
        if rect.right() < -2 or rect.left() > width + 2:
            return
        track = self.session.tracks[clip.track_id]
        selected = clip.id == self.selection
        painter.save()
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        painter.fillPath(path, QColor("#161A31" if selected else "#11142A"))
        painter.setClipPath(path)
        wave = QRectF(rect.left(), rect.top() + self.HEADER, rect.width(), rect.height() - self.HEADER - 4)
        visible_left, visible_right = max(wave.left(), -4), min(wave.right(), width + 4)
        if visible_right > visible_left:
            span = clip.cue_out - clip.cue_in
            start = clip.cue_in + (visible_left - wave.left()) / wave.width() * span
            end = clip.cue_in + (visible_right - wave.left()) / wave.width() * span
            target = QRectF(visible_left, wave.top(), visible_right - visible_left, wave.height())
            bands = self.bands.get(track.id)
            columns = band_columns(bands, start * BINS_PER_SECOND, end * BINS_PER_SECOND,
                                   max(1, min(4000, int(target.width()))))
            if columns is not None:
                paint_bands(painter, target, columns, .95 if selected or not self.selection else .82)
            else:
                paint_peaks(painter, target, track.waveform, start / track.duration, end / track.duration)
        self._paint_curves(painter, index, clip, position, wave)
        # Header: name on the left, key and tempo on the right.
        painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), self.HEADER), T.color("#0B0D1C", .55))
        painter.restore()
        painter.save()
        # Names stay readable when a clip is scrolled partly out of view.
        frame = rect
        rect = QRectF(max(rect.left(), 0.0), rect.top(), min(rect.right(), float(width)) - max(rect.left(), 0.0), rect.height())
        painter.setClipRect(rect.adjusted(8, 0, -8, 0))
        metrics_font = T.font(12, tabular=True)
        painter.setFont(metrics_font)
        runs = []
        processed = djmix.transpose(parsed_key(track.key), clip.shift)
        if processed:
            runs.append((display_key(processed), T.TEXT_2))
        if clip.shift:
            runs.append((f"{clip.shift:+d} st", T.ACCENT))
        stretch = (self.session.target_bpm / track.bpm - 1) * 100
        runs.append((f"{track.bpm:.1f} BPM", T.TEXT_2))
        if abs(stretch) >= .05:
            runs.append((f"{stretch:+.1f}%", T.BAD if abs(stretch) > 8 else T.TEXT_3))
        if clip.stabilize:
            runs.append(("Stabilized", T.TEXT_2))
        metrics = QFontMetricsF(metrics_font)
        run_width = sum(metrics.horizontalAdvance(text) for text, _ in runs) + 12 * (len(runs) - 1)
        name_font = T.font(T.SIZE_BODY, QFont.Weight.DemiBold)
        name_metrics = QFontMetricsF(name_font)
        room = rect.width() - 20
        show_runs = room - run_width > min(140, name_metrics.horizontalAdvance(track.name) + 16)
        name_room = room - (run_width + 16 if show_runs else 0)
        painter.setFont(name_font)
        painter.setPen(QColor(T.TEXT))
        name = name_metrics.elidedText(track.name, Qt.TextElideMode.ElideRight, max(0, name_room))
        painter.drawText(QRectF(rect.left() + 10, rect.top(), max(0, name_room), self.HEADER),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
        if show_runs:
            painter.setFont(metrics_font)
            x = rect.right() - 10 - run_width
            for text, ink in runs:
                advance = metrics.horizontalAdvance(text)
                painter.setPen(QColor(ink))
                painter.drawText(QRectF(x, rect.top(), advance + 2, self.HEADER),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
                x += advance + 12
        painter.restore()
        painter.setPen(QPen(QColor(T.ACCENT), 1.5) if selected else QPen(QColor("#272C4A"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(frame.adjusted(.5, .5, -.5, -.5), 6, 6)

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
            curves = [(high, QPen(T.color(T.TEXT, .78), 1.4))]
            if owner.bass_swap:
                curves.append((low, QPen(T.color(T.TEXT, .5), 1.2, Qt.PenStyle.DashLine)))
            for gains, pen in curves:
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                points = [QPointF(x, wave.bottom() - 3 - g * (wave.height() - 6)) for x, g in zip(xs, gains)]
                painter.drawPolyline(QPolygonF(points))

    def _paint_overlap(self, painter, index, width):
        position = self.positions[index]
        left = self.x(position["start"] / djmix.SR)
        right = self.x((position["start"] + position["incoming"]) / djmix.SR)
        if right < -40 or left > width + 40:
            return
        top, lane = self.lanes()
        bottom = top + 2 * lane + self.GAP
        current = index == self.transition
        painter.fillRect(QRectF(left, top, right - left, bottom - top), T.color("#FFFFFF", .05 if current else .025))
        edge = T.color(T.ACCENT, .55) if current else T.color("#FFFFFF", .12)
        painter.fillRect(QRectF(left, top - 4, 1, bottom - top + 8), edge)
        painter.fillRect(QRectF(right - 1, top - 4, 1, bottom - top + 8), edge)
        clip = self.session.clips[index]
        text = f"{clip.blend_bars} bars"
        font = T.font(12, tabular=True)
        metrics = QFontMetricsF(font)
        label_width = metrics.horizontalAdvance(text) + 26
        center = (left + right) / 2
        label = QRectF(center - label_width / 2, top + lane + (self.GAP - 22) / 2, label_width, 22)
        painter.setPen(QPen(T.color(T.ACCENT, .7) if current else QColor(T.LINE_STRONG), 1))
        painter.setBrush(QColor(T.INK))
        painter.drawRoundedRect(label, 5, 5)
        state = self.harmony.get(index, "unknown")
        dot = {"good": T.GOOD, "bad": T.BAD}.get(state, T.TEXT_3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(dot))
        painter.drawEllipse(QPointF(label.left() + 11, label.center().y()), 3, 3)
        painter.setFont(font)
        painter.setPen(QColor(T.TEXT if current else T.TEXT_2))
        painter.drawText(label.adjusted(18, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        self._labels.append((label.adjusted(-4, -4, 4, 4), index))

    def _paint_empty(self, painter, width, height):
        top, lane = self.lanes()
        area = QRectF(self.PAD, top, width - 2 * self.PAD, 2 * lane + self.GAP)
        pen = QPen(QColor(T.LINE_STRONG), 1.2, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([4, 4])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(area, 10, 10)
        painter.setPen(QColor(T.TEXT))
        painter.setFont(T.font(T.SIZE_HEADING, QFont.Weight.DemiBold))
        painter.drawText(QRectF(area.left(), area.center().y() - 30, area.width(), 26), Qt.AlignmentFlag.AlignCenter,
                         "Drag a song here to start your mix")
        painter.setFont(T.font(T.SIZE_BODY))
        painter.setPen(QColor(T.TEXT_2))
        painter.drawText(QRectF(area.left(), area.center().y(), area.width(), 40), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         "Or double-click a song in your library. Each new song overlaps the one before it.")

    # Interaction
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        for rect, index in self._labels:
            if rect.contains(point):
                self.transitionSelected.emit(index)
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
        if not event.buttons():
            over_label = any(rect.contains(point) for rect, _ in self._labels)
            over_clip = any(self.clip_rect(p, i).contains(point) for i, p in enumerate(self.positions))
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if over_label else
                                      Qt.CursorShape.OpenHandCursor if over_clip else Qt.CursorShape.ArrowCursor)
            return
        if self.drag_clip and self.drag_origin and (point - self.drag_origin).manhattanLength() > 8:
            index = next(i for i, p in enumerate(self.positions) if p["clip_id"] == self.drag_clip)
            rect = self.clip_rect(self.positions[index], index).intersected(QRectF(self.viewport().rect()))
            data = QMimeData()
            data.setData(CLIP_MIME, self.drag_clip.encode())
            drag = QDrag(self)
            drag.setMimeData(data)
            if self._static is not None and rect.width() > 4:
                ratio = self._static.devicePixelRatio()
                image = self._static.copy(rect.left() * ratio, rect.top() * ratio, rect.width() * ratio, rect.height() * ratio)
                image.setDevicePixelRatio(ratio)
                if image.width() > 360 * ratio:
                    image = image.copy(0, 0, int(360 * ratio), image.height())
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
        self.drop_index = self.insertion(event.position().x())
        self.viewport().update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.drop_index = None
        self.viewport().update()

    def dropEvent(self, event):
        index = self.insertion(event.position().x())
        mime = event.mimeData()
        self.drop_index = None
        self.viewport().update()
        if mime.hasFormat(TRACK_MIME):
            self.trackDropped.emit(bytes(mime.data(TRACK_MIME)).decode(), index)
        elif mime.hasFormat(CLIP_MIME):
            self.clipMoved.emit(bytes(mime.data(CLIP_MIME)).decode(), index)
        event.acceptProposedAction()


class Overview(QWidget):
    """The whole mix in one strip. Click or drag to move the timeline view there."""

    navigate = Signal(float)

    def __init__(self, timeline: Timeline, parent=None):
        super().__init__(parent)
        self.timeline = timeline
        self.playhead = None
        self._static = None
        self.setFixedHeight(42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Overview of the whole mix. Click to jump there.")
        self.setAccessibleName("Mix overview")
        timeline.viewChanged.connect(self.update)

    def invalidate(self):
        self._static = None
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._static = None

    def _x(self, seconds, duration):
        return 1 + seconds / duration * (self.width() - 2)

    def _render(self):
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QPainterPath()
        frame.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 6, 6)
        painter.fillPath(frame, QColor(T.INK))
        painter.setClipPath(frame)
        timeline = self.timeline
        duration = timeline.duration()
        if duration > 0:
            for index, (clip, position) in enumerate(zip(timeline.session.clips, timeline.positions)):
                track = timeline.session.tracks[clip.track_id]
                left = self._x(position["start"] / djmix.SR, duration)
                right = self._x(position["end"] / djmix.SR, duration)
                rect = QRectF(left, 5, max(1, right - left), self.height() - 10)
                columns = band_columns(timeline.bands.get(track.id), clip.cue_in * BINS_PER_SECOND,
                                       clip.cue_out * BINS_PER_SECOND, max(1, int(rect.width())))
                if columns is not None:
                    paint_bands(painter, rect, columns, .85)
                else:
                    paint_peaks(painter, rect, track.waveform, clip.cue_in / track.duration, clip.cue_out / track.duration)
                if index:
                    painter.fillRect(QRectF(left, 0, 1, self.height()), T.color(T.INK, .9))
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
            if right - left < self.width() - 4:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(T.color(T.TEXT, .55), 1))
                painter.setBrush(T.color("#FFFFFF", .06))
                painter.drawRoundedRect(QRectF(left, .5, max(4, right - left), self.height() - 1), 4, 4)
            if self.timeline.rendered:
                start, end = self.timeline.rendered
                painter.fillRect(QRectF(self._x(start, duration), self.height() - 2, max(1, self._x(end, duration) - self._x(start, duration)), 2),
                                 T.color(T.ACCENT, .85))
            if self.playhead is not None:
                painter.fillRect(QRectF(self._x(self.playhead, duration) - .75, 0, 1.5, self.height()), QColor("#FFFFFF"))
        else:
            painter.setPen(QColor(T.TEXT_3))
            painter.setFont(T.font(12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "The mix overview appears once songs are arranged")
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(T.LINE), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(.5, .5, self.width() - 1, self.height() - 1), 6, 6)

    def _go(self, x):
        duration = self.timeline.duration()
        if duration > 0:
            self.navigate.emit(min(duration, max(0.0, (x - 1) / max(1, self.width() - 2) * duration)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._go(event.position().x())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._go(event.position().x())


class CueEditor(QWidget):
    """The whole song with draggable in and out points. Handles snap to bars from the analyzed grid."""

    cuesChanged = Signal(float, float)

    TOP, BOTTOM = 16, 14

    def __init__(self, bands, parent=None):
        super().__init__(parent)
        self.bands = bands
        self.track = self.clip = None
        self.blend_in = self.blend_out = 0
        self.dragging = None
        self.preview = None
        self.setMinimumHeight(104)
        self.setMouseTracking(True)
        self.setAccessibleName("Cue points")
        self.setToolTip("")

    def set_clip(self, track, clip, blend_in_bars, blend_out_bars):
        self.track, self.clip = track, clip
        self.blend_in, self.blend_out = blend_in_bars, blend_out_bars
        self.preview = None
        self.update()

    def cues(self):
        return self.preview if self.preview else (self.clip.cue_in, self.clip.cue_out)

    def wave_rect(self):
        return QRectF(0, self.TOP, self.width(), self.height() - self.TOP - self.BOTTOM)

    def x_of(self, seconds):
        wave = self.wave_rect()
        return wave.left() + seconds / self.track.duration * wave.width()

    def time_at(self, x):
        wave = self.wave_rect()
        return min(self.track.duration, max(0.0, (x - wave.left()) / wave.width() * self.track.duration))

    def snap(self, seconds, modifiers):
        if modifiers & Qt.KeyboardModifier.AltModifier or not self.track.bpm:
            return seconds
        step = 60 / self.track.bpm if modifiers & Qt.KeyboardModifier.ShiftModifier else 240 / self.track.bpm
        origin = self.track.cue_in
        snapped = origin + round((seconds - origin) / step) * step
        return min(self.track.duration, max(0.0, snapped))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        wave = self.wave_rect()
        frame = QPainterPath()
        frame.addRoundedRect(wave, 6, 6)
        painter.fillPath(frame, QColor(T.INK))
        if not self.track:
            return
        painter.save()
        painter.setClipPath(frame)
        columns = band_columns(self.bands.get(self.track.id), 0, self.track.duration * BINS_PER_SECOND,
                               max(1, int(wave.width())))
        if columns is not None:
            paint_bands(painter, wave.adjusted(0, 6, 0, -6), columns)
        else:
            paint_peaks(painter, wave.adjusted(0, 6, 0, -6), self.track.waveform, 0, 1)
        cue_in, cue_out = self.cues()
        left, right = self.x_of(cue_in), self.x_of(cue_out)
        shade = T.color(T.BASE, .78)
        painter.fillRect(QRectF(wave.left(), wave.top(), left - wave.left(), wave.height()), shade)
        painter.fillRect(QRectF(right, wave.top(), wave.right() - right, wave.height()), shade)
        bar = 240 / self.track.bpm if self.track.bpm else 0
        for bars, at_start in ((self.blend_in, True), (self.blend_out, False)):
            if bars and bar:
                extent = bars * bar / self.track.duration * wave.width()
                zone = QRectF(left, wave.top(), extent, wave.height()) if at_start else QRectF(right - extent, wave.top(), extent, wave.height())
                painter.fillRect(zone, T.color("#FFFFFF", .07))
        painter.restore()
        if bar:
            pixels = bar / self.track.duration * wave.width()
            every = 1
            while every * pixels < 5:
                every *= 2
            painter.setPen(Qt.PenStyle.NoPen)
            index = math.ceil(-self.track.cue_in / bar / every) * every
            while True:
                seconds = self.track.cue_in + index * bar
                if seconds > self.track.duration:
                    break
                x = self.x_of(seconds)
                major = index % (every * 4) == 0
                painter.setBrush(T.color(T.TEXT_3, .7 if major else .35))
                painter.drawRect(QRectF(x, wave.bottom() + 3, 1, 6 if major else 3))
                index += every
        for name, x in (("In", left), ("Out", right)):
            active = self.dragging == name
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(T.ACCENT_HOVER if active else T.ACCENT))
            painter.drawRect(QRectF(x - 1, wave.top(), 2, wave.height()))
            tab = QRectF(x - 1, 0, 28, 14) if name == "In" else QRectF(x - 27, 0, 28, 14)
            painter.drawRoundedRect(tab, 3, 3)
            painter.setPen(QColor(T.ON_ACCENT))
            painter.setFont(T.font(10, QFont.Weight.DemiBold))
            painter.drawText(tab, Qt.AlignmentFlag.AlignCenter, name)
        painter.setPen(QPen(QColor(T.LINE), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(wave.adjusted(.5, .5, -.5, -.5), 6, 6)

    def _handle_at(self, x):
        if not self.track:
            return None
        cue_in, cue_out = self.cues()
        distances = {"In": abs(x - self.x_of(cue_in)), "Out": abs(x - self.x_of(cue_out))}
        name = min(distances, key=distances.get)
        return name if distances[name] <= 9 else None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
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
        if self.dragging == "In":
            cue_in = min(seconds, cue_out - minimum)
        else:
            cue_out = max(seconds, cue_in + minimum)
        self.preview = (max(0.0, cue_in), min(self.track.duration, cue_out))
        self.setToolTip(f"{self.dragging} {clock(seconds, True)}")
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


# Library ----------------------------------------------------------------------------------

class LibraryModel(QAbstractListModel):
    TrackRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.ids = []
        self.query = ""
        self.order = "added"
        self.context = None
        self.context_track = None
        self.arranged = {}

    def rebuild(self):
        self.beginResetModel()
        tracks = list(self.session.tracks.values()) if self.session else []
        needle = self.query.casefold().strip()
        if needle:
            tracks = [t for t in tracks if needle in t.name.casefold()]
        if self.order == "name":
            tracks.sort(key=lambda t: t.name.casefold())
        elif self.order == "bpm":
            tracks.sort(key=lambda t: (t.bpm is None, t.bpm or 0))
        elif self.order == "key":
            def key_order(track):
                code = camelot(track.key)
                return (not code, int(code[:-1]) if code else 0, code[-1:] if code else "")
            tracks.sort(key=key_order)
        self.ids = [t.id for t in tracks]
        self.arranged = {}
        if self.session:
            for index, clip in enumerate(self.session.clips):
                self.arranged.setdefault(clip.track_id, []).append(index + 1)
        self.endResetModel()

    def track(self, row):
        return self.session.tracks.get(self.ids[row]) if 0 <= row < len(self.ids) else None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.ids)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        track = self.track(index.row())
        if track is None:
            return None
        if role == self.TrackRole:
            return track.id
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            details = [track.name]
            if track.bpm:
                details.append(f"{track.bpm:.1f} BPM")
            if track.key:
                details.append(display_key(track.key))
            return ", ".join(details)
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [track.name, track.status]
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
    HEIGHT = 50

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.HEIGHT)

    def paint(self, painter, option, index):
        model = index.model()
        track = model.track(index.row())
        if track is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(4, 1, -4, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(T.SELECTION if selected else "#161A2E"))
            painter.drawRoundedRect(rect, 6, 6)
        right_width = 78
        text_left = rect.left() + 10
        text_width = rect.width() - right_width - 22
        painter.setFont(T.font(T.SIZE_BODY, QFont.Weight.Medium))
        painter.setPen(QColor(T.TEXT))
        name = painter.fontMetrics().elidedText(track.name, Qt.TextElideMode.ElideRight, int(text_width))
        painter.drawText(QRectF(text_left, rect.top() + 7, text_width, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        small = T.font(12, tabular=True)
        painter.setFont(small)
        metrics = QFontMetricsF(small)
        x = text_left
        line = QRectF(0, rect.top() + 26, 0, 16)
        runs = [(clock(track.duration), T.TEXT_3)]
        pending = track.bpm is None and track.status == "Pending analysis"
        if track.bpm is None and not pending:
            runs.append(("Needs BPM", T.BAD))
        positions = model.arranged.get(track.id)
        if positions:
            runs.append(("In mix as " + ", ".join(map(str, positions)), T.ACCENT))
        for text, ink in runs:
            advance = metrics.horizontalAdvance(text)
            if x + advance > text_left + text_width:
                break
            painter.setPen(QColor(ink))
            painter.drawText(QRectF(x, line.top(), advance + 2, line.height()), Qt.AlignmentFlag.AlignVCenter, text)
            x += advance + 12
        right = QRectF(rect.right() - right_width - 8, rect.top(), right_width, rect.height())
        if pending:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(T.LINE_STRONG))
            painter.drawRoundedRect(QRectF(right.right() - 40, rect.top() + 11, 40, 10), 3, 3)
            painter.drawRoundedRect(QRectF(right.right() - 56, rect.top() + 29, 56, 9), 3, 3)
        else:
            bpm_font = T.font(T.SIZE_BODY, tabular=True)
            painter.setFont(bpm_font)
            painter.setPen(QColor(T.TEXT if track.bpm else T.TEXT_3))
            painter.drawText(QRectF(right.left(), rect.top() + 7, right.width(), 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{track.bpm:.1f}" if track.bpm else "BPM?")
            painter.setFont(small)
            key = f"{display_key(track.key, True)}  {camelot(track.key)}" if track.key else "Key?"
            painter.setPen(QColor(T.TEXT_2 if track.key else T.TEXT_3))
            key_width = metrics.horizontalAdvance(key)
            painter.drawText(QRectF(right.left(), line.top(), right.width(), line.height()),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, key)
            if model.relation(track):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(T.GOOD))
                painter.drawEllipse(QPointF(right.right() - key_width - 9, line.center().y()), 3, 3)
        painter.restore()


class LibraryView(QListView):
    addRequested = Signal(str)
    filesDropped = Signal(list)

    def __init__(self, model: LibraryModel, parent=None):
        super().__init__(parent)
        self.setModel(model)
        self.setItemDelegate(LibraryDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setSpacing(0)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.file_hover = False
        self.doubleClicked.connect(lambda index: self.addRequested.emit(model.data(index, LibraryModel.TrackRole)))
        self.setAccessibleName("Library")

    def current_id(self):
        index = self.currentIndex()
        return self.model().data(index, LibraryModel.TrackRole) if index.isValid() else None

    def select_id(self, identity):
        model = self.model()
        if identity in model.ids:
            self.setCurrentIndex(model.index(model.ids.index(identity)))

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
        rect = self.visualRect(index)
        image = self.viewport().grab(rect)
        drag.setPixmap(image)
        drag.setHotSpot(self.viewport().mapFromGlobal(self.cursor().pos()) - rect.topLeft())
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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.viewport().rect()).adjusted(4, 4, -4, -4)
        if self.model().rowCount() == 0:
            searching = bool(self.model().query.strip())
            painter.setFont(T.font(T.SIZE_BODY, QFont.Weight.DemiBold))
            painter.setPen(QColor(T.TEXT))
            painter.drawText(QRectF(area.left() + 12, area.top() + 40, area.width() - 24, 22), Qt.AlignmentFlag.AlignCenter,
                             "No matching songs" if searching else "Your library is empty")
            painter.setFont(T.font(12))
            painter.setPen(QColor(T.TEXT_2))
            painter.drawText(QRectF(area.left() + 16, area.top() + 66, area.width() - 32, 60),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                             "Try another part of the title." if searching else
                             "Drop audio files or a folder here, or use Import folder below.")
        if self.file_hover:
            pen = QPen(QColor(T.ACCENT), 1.5, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([4, 3])
            painter.setPen(pen)
            painter.setBrush(T.color(T.ACCENT, .05))
            painter.drawRoundedRect(area, 8, 8)
        painter.end()
