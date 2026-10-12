"""Dedicated two-song blend editor view for DJ Studio.

Shows the outgoing and incoming songs as two aligned waveform lanes over one
preview interval, with a shared cue-based beat grid anchored on the incoming
start. Waveforms come only from real cached band envelopes (or the track's
real library peaks as a fallback); this module never synthesizes any.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

import djmix
import studio_theme as T
from studio_engine import layout
from studio_timing import corrected_time, mapping
from studio_waveform import BINS_PER_SECOND
from studio_widgets import band_columns, clock, paint_bands, paint_envelope


def _linear_to_corrected(track, clip, target_bpm, clip_start, project_seconds):
    base = corrected_time(track, clip, clip.cue_in)
    return base + (float(project_seconds) - float(clip_start)) * float(target_bpm) / float(track.bpm)


def _invert_map(points, corrected):
    return float(np.interp(corrected, points[:, 1], points[:, 0]))


def project_to_source(*args):
    """Map a project moment back to source seconds for one clip (pure).

    Accepts either ``(track, clip, target_bpm, clip_start, project_seconds)``
    or ``(session, clip_index, project_seconds, positions=None)``. Uses the
    source/target tempo ratio and, when the clip is stabilized, the inverse
    timing map. Never mutates anything. Callers clip to the song's timeline
    extent before mapping; interpolation clamps at timing-map endpoints.
    """
    if len(args) == 5:
        track, clip, target_bpm, clip_start, project_seconds = args
    elif len(args) in (3, 4):
        session, clip_index = args[0], args[1]
        project_seconds = args[2]
        positions = args[3] if len(args) == 4 else None
        if positions is None:
            positions = layout(session)
        clip = session.clips[clip_index]
        track = session.tracks[clip.track_id]
        target_bpm = session.target_bpm
        clip_start = positions[clip_index]["start"] / djmix.SR
    else:
        raise TypeError("project_to_source takes (track, clip, target, start, project) "
                        "or (session, index, project[, positions])")
    if track.bpm is None or not math.isfinite(track.bpm) or track.bpm <= 0:
        raise ValueError("Track BPM is required for source mapping.")
    corrected = _linear_to_corrected(track, clip, target_bpm, clip_start, project_seconds)
    points = mapping(track, clip)
    if points is None:
        return corrected
    return _invert_map(np.asarray(points, dtype=float), corrected)


def _project_array_to_source(track, clip, target_bpm, clip_start, base_corrected, points, projects):
    corrected = base_corrected + (projects - clip_start) * target_bpm / track.bpm
    if points is None:
        return corrected
    return np.interp(corrected, points[:, 1], points[:, 0])


class TransitionView(QWidget):
    """Two aligned waveform lanes for one transition. No session mutation."""

    seekRequested = Signal(float)
    overlapChanged = Signal(int)

    _PAD = 10
    _RULER = 40
    _LABEL = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Transition blend editor")
        self.setAccessibleDescription("Two-song blend editor. No transition loaded.")
        self.setMouseTracking(False)
        self._target = None
        self._index = None
        self._start = 0.0
        self._end = 0.0
        self._playhead = None
        self._lanes = []
        self._bands = {}
        self._incoming_start = 0.0
        self._overlap_end = 0.0
        self._blend_bars = 0
        self._bass_swap = False
        self.setMinimumSize(600, 300)

    # -- data -----------------------------------------------------------------
    def set_data(self, session, index, bands, start, end):
        """Load one transition. Copies scalars only; never mutates the session."""
        positions = layout(session)  # read-only validation of the arrangement
        if not 0 < index < len(session.clips):
            raise ValueError("index must be an incoming clip index.")
        start, end = float(start), float(end)
        if not end > start:
            raise ValueError("Preview interval must have positive length.")
        target = float(session.target_bpm)
        lanes = []
        for role, clip_index in (("Out", index - 1), ("In", index)):
            clip = session.clips[clip_index]
            track = session.tracks[clip.track_id]
            try:
                points = mapping(track, clip)
            except Exception:
                points = None
            lanes.append({
                "role": role, "clip_index": clip_index, "track_id": track.id,
                "name": track.name, "bpm": float(track.bpm),
                "cue_in": float(clip.cue_in), "cue_out": float(clip.cue_out),
                "duration": float(track.duration),
                "waveform": list(track.waveform),
                "clip_start": positions[clip_index]["start"] / djmix.SR,
                "clip_end": positions[clip_index]["end"] / djmix.SR,
                "colors": T.clip_bands(T.clip_color(track.id)),
                "points": None if points is None else np.asarray(points, dtype=float),
                "base": None,  # corrected(cue_in), filled below
            })
            try:
                lanes[-1]["base"] = float(corrected_time(track, clip, clip.cue_in))
            except Exception:
                lanes[-1]["base"] = float(clip.cue_in)
        incoming = session.clips[index]
        self._target = target
        self._index = index
        self._start, self._end = start, end
        self._playhead = None
        self._lanes = lanes
        # Keep references to real envelopes only; painting never writes to them.
        self._bands = bands if bands is not None else {}
        self._incoming_start = positions[index]["start"] / djmix.SR
        self._overlap_end = self._incoming_start + positions[index]["incoming"] / djmix.SR
        self._blend_bars = int(incoming.blend_bars)
        self._bass_swap = bool(incoming.bass_swap)
        out, inc = lanes
        self.setAccessibleDescription(
            f"Blend from {out['name']} into {inc['name']}, {self._blend_bars} bars at "
            f"{target:g} BPM. Cue-based grid anchored on incoming start. Arrow keys seek "
            f"by beat, Shift by bar.")
        self.update()

    def set_playhead(self, local_seconds):
        self._playhead = float(local_seconds)
        self.update()

    # -- geometry ---------------------------------------------------------------
    def sizeHint(self):
        return QSize(860, 340)

    def minimumSizeHint(self):
        return QSize(600, 300)

    def _x(self, project):
        span = self._end - self._start
        width = self.width() - 2 * self._PAD
        if span <= 0 or width <= 0:
            return float(self._PAD)
        return self._PAD + (float(project) - self._start) / span * width

    def _project(self, x):
        width = self.width() - 2 * self._PAD
        if width <= 0:
            return self._start
        return self._start + (float(x) - self._PAD) / width * (self._end - self._start)

    def _lane_rects(self):
        top = self._RULER + 4
        height = max(0, self.height() - top - 6)
        each = height / 2 if height else 0
        rects = []
        for i in range(2):
            y = top + i * each
            rects.append((QRectF(self._PAD, y, self.width() - 2 * self._PAD, self._LABEL),
                          QRectF(self._PAD, y + self._LABEL, self.width() - 2 * self._PAD,
                                 max(0, each - self._LABEL - 3))))
        return rects

    def _source_span(self, lane):
        visible = (max(self._start, lane["clip_start"]), min(self._end, lane["clip_end"]))
        if visible[1] <= visible[0]:
            return None
        projects = np.array(visible, dtype=float)
        sources = _project_array_to_source(_Bpm(lane["bpm"]), None, self._target,
                                           lane["clip_start"], lane["base"],
                                           lane["points"], projects)
        return float(sources[0]), float(sources[1])

    # -- painting ---------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(T.BASE))
        if not self._lanes or not self._end > self._start:
            painter.setFont(T.font(T.SIZE_BODY))
            painter.setPen(QColor(T.TEXT_3))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No transition loaded")
            painter.end()
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        lanes_top = self._RULER + 4
        lanes_bottom = self.height() - 6
        for lane, (_, wave) in zip(self._lanes, self._lane_rects()):
            self._paint_wave(painter, lane, wave)
        self._paint_overlap(painter, lanes_top, lanes_bottom)
        self._paint_grid(painter, lanes_top, lanes_bottom)
        for lane, (label, _) in zip(self._lanes, self._lane_rects()):
            self._paint_label(painter, lane, label)
        self._paint_bass(painter, lanes_top, lanes_bottom)
        self._paint_playhead(painter, lanes_top, lanes_bottom)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(T.ACCENT), 2))
            painter.drawRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        painter.end()

    def _paint_grid(self, painter, top, bottom):
        beat = 60.0 / self._target
        first = math.ceil((self._start - self._incoming_start) / beat)
        last = math.floor((self._end - self._incoming_start) / beat)
        painter.setFont(T.font(T.SIZE_SMALL, tabular=True))
        for k in range(first, last + 1):
            x = self._x(self._incoming_start + k * beat)
            bar = (k % 4 == 0)
            guide = (k % 32 == 0)
            if guide:
                pen = QPen(T.color(T.ACCENT, .55), 1.2)
            elif bar:
                pen = QPen(QColor(T.LINE_STRONG), 1)
            else:
                pen = QPen(QColor(T.LINE), 1)
            painter.setPen(pen)
            for _, wave in self._lane_rects():
                painter.drawLine(QPointF(x, wave.top()), QPointF(x, wave.bottom()))
            if bar and x + 16 < self.width():
                painter.setPen(QColor(T.TEXT_3))
                painter.drawText(QRectF(x + 3, 20, 40, 18),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 f"{math.floor(k / 4) + 1}")
        painter.setPen(QColor(T.TEXT_3))
        painter.drawText(QRectF(self._PAD, 2, self.width() - 2 * self._PAD, 18),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         "cue-based grid · bars from incoming cue")

    def _paint_overlap(self, painter, top, bottom):
        lo, hi = max(self._start, self._incoming_start), min(self._end, self._overlap_end)
        if hi <= lo:
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(T.color(T.ACCENT, .10))
        for _, wave in self._lane_rects():
            painter.drawRect(QRectF(self._x(lo), wave.top(), self._x(hi) - self._x(lo), wave.height()))

    def _paint_label(self, painter, lane, rect):
        painter.setFont(T.font(T.SIZE_BODY, tabular=True))
        metrics = painter.fontMetrics()
        badge = "Out" if lane["role"] == "Out" else "In"
        painter.setPen(QColor(T.ACCENT if lane["role"] == "In" else T.TEXT_2))
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, badge)
        badge_w = painter.fontMetrics().horizontalAdvance(badge + "  ")
        span = self._source_span(lane)
        right = f"{clock(span[0], True)} – {clock(span[1], True)}" if span else "—"
        painter.setPen(QColor(T.TEXT_2))
        painter.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, right)
        right_w = painter.fontMetrics().horizontalAdvance(right + "  ")
        painter.setPen(QColor(T.TEXT))
        name_rect = rect.adjusted(badge_w, 0, -right_w, 0)
        elided = metrics.elidedText(lane["name"], Qt.TextElideMode.ElideRight, int(max(0, name_rect.width())))
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)

    def _paint_wave(self, painter, lane, rect):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(T.FIELD))
        painter.drawRoundedRect(rect, 3, 3)
        lo, hi = max(self._start, lane["clip_start"]), min(self._end, lane["clip_end"])
        if hi <= lo or rect.width() < 2 or rect.height() < 4:
            return
        inner = QRectF(self._x(lo), rect.top() + 2, self._x(hi) - self._x(lo), rect.height() - 4)
        if inner.width() < 1:
            return
        columns = max(1, min(1600, int(inner.width())))
        edges = np.linspace(lo, hi, columns + 1)
        sources = _project_array_to_source(_Bpm(lane["bpm"]), None, self._target,
                                           lane["clip_start"], lane["base"],
                                           lane["points"], edges)
        envelopes = self._bands.get(lane["track_id"])
        if envelopes is not None and len(envelopes) >= 2:
            pooled = _warped_band_columns(np.asarray(envelopes, dtype=float),
                                          sources * BINS_PER_SECOND, columns)
            paint_bands(painter, inner, pooled, colors=list(lane["colors"]))
        elif lane["waveform"]:
            # Sample every mapped column, including nonlinear timing correction.
            peaks = np.asarray(lane['waveform'], dtype=float)
            centers = (sources[:-1] + sources[1:]) / 2
            indices = np.clip(centers / lane['duration'] * len(peaks), 0, len(peaks) - 1).astype(int)
            values = peaks[indices] * .8
            paint_envelope(painter, inner, values, lane["colors"][1])

    def _paint_bass(self, painter, top, bottom):
        if not self._bass_swap or self._overlap_end <= self._incoming_start:
            return
        center = (self._incoming_start + self._overlap_end) / 2
        if not self._start <= center <= self._end:
            return
        x = self._x(center)
        painter.setPen(QPen(T.color(T.ACCENT, .8), 1.2, Qt.PenStyle.DashLine))
        for _, wave in self._lane_rects():
            painter.drawLine(QPointF(x, wave.top()), QPointF(x, wave.bottom()))
        painter.setFont(T.font(T.SIZE_SMALL))
        first_wave = self._lane_rects()[0][1]
        caption = QRectF(x + 3, first_wave.bottom() - 19, 82, 16)
        painter.fillRect(caption, T.color(T.FIELD, .85))
        painter.setPen(QColor(T.ACCENT))
        painter.drawText(caption, Qt.AlignmentFlag.AlignCenter, "bass handoff")

    def _paint_playhead(self, painter, top, bottom):
        if self._playhead is None:
            return
        project = self._start + self._playhead
        if not self._start <= project <= self._end:
            return
        x = self._x(project)
        painter.setPen(QPen(QColor("#FFFFFF"), 1.4))
        painter.drawLine(QPointF(x, top), QPointF(x, bottom))

    # -- interaction ------------------------------------------------------------
    def _clamped_local(self, local):
        return min(max(float(local), 0.0), max(0.0, self._end - self._start))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._end > self._start:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.seekRequested.emit(self._clamped_local(self._project(event.position().x()) - self._start))
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if not self._end > self._start or self._target is None:
            super().keyPressEvent(event)
            return
        beat = 60.0 / self._target
        step = 4 * beat if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else beat
        current = 0.0 if self._playhead is None else self._playhead
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            delta = -step if key == Qt.Key.Key_Left else step
            self.seekRequested.emit(self._clamped_local(current + delta))
            event.accept()
        elif key == Qt.Key.Key_Home:
            self.seekRequested.emit(0.0)
            event.accept()
        elif key == Qt.Key.Key_End:
            self.seekRequested.emit(self._clamped_local(self._end - self._start))
            event.accept()
        else:
            super().keyPressEvent(event)


class _Bpm:
    """Minimal track stand-in carrying only the tempo for vectorized mapping."""

    def __init__(self, bpm):
        self.bpm = bpm


def _warped_band_columns(bands, edge_bins, columns):
    """Pool real band envelopes through warped (possibly nonlinear) bin edges."""
    if bands is None or len(bands) < 2 or columns < 1:
        return None
    deepest = np.clip(np.floor(edge_bins).astype(int), 0, len(bands))
    # Fast path for the common near-linear case: reuse the shared pooling.
    if float(np.ptp(np.diff(edge_bins))) < 1e-9:
        return band_columns(bands, float(edge_bins[0]), float(edge_bins[-1]), columns)
    pooled = np.zeros((columns, bands.shape[1]), dtype=float)
    for i in range(columns):
        lo, hi = deepest[i], max(deepest[i + 1], deepest[i] + 1)
        segment = bands[lo:hi]
        if not len(segment):
            continue
        pooled[i] = .4 * segment.max(axis=0) + .6 * segment.mean(axis=0)
    return pooled
