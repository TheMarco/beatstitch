"""Standalone native Qt workbench for detector-pilot human references.

Loads a frozen ``pilot.json`` plus already-prepared decoded audio and cached
waveform peaks. It never runs detector inference: suggestions are read with
:func:`detector_pilot.load_prediction` only after the reviewer has saved a
reference and explicitly reveals them. Exposure is recorded through
:func:`detector_pilot.mark_predictions_exposed` *before* any model output is
shown, and is a record of exposure in this tool only — not proof of fully
blind ground truth.
"""
from __future__ import annotations

import argparse
import copy
import math
from pathlib import Path
import sys

import numpy as np
from PySide6.QtCore import QLockFile, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSlider, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

import detector_pilot as dp
import studio_theme as T
from studio_widgets import clock

PEAK_RATE = dp.PEAK_RATE

KEY_DISPLAY = {
    "not_reviewed": "Not reviewed",
    "unclear": "Unclear — cannot tell",
    "no_key": "No key (no pitched key centre)",
    "not_qualified": "Not qualified to judge key",
}
CHOICE_DISPLAY = {
    "not_reviewed": "Not reviewed", "yes": "Yes",
    "no": "No", "unclear": "Unclear",
}
TIMING_DISPLAY = {
    "not_reviewed": "Not reviewed", "annotated": "Marked beats (annotated)",
    "beatless": "No audible beat", "unclear": "Unclear",
}
EXPOSURE_NOTE = (
    "Recorded exposure is only tool exposure in this reviewer, not proof of "
    "fully blind ground truth."
)


def sorted_beats(beats):
    """Return validated-shaped copies sorted by absolute source time."""
    result = [{"time": float(b["time"]), "downbeat": bool(b["downbeat"])} for b in beats]
    result.sort(key=lambda b: b["time"])
    return result


def predicted_events(prediction, task):
    """Predicted (beats, downbeats) inside ``task``, from a loaded prediction."""
    if not prediction:
        return [], []
    stage = prediction.get("stages", {}).get("beats", {})
    if stage.get("status") != "completed":
        return [], []
    result = stage.get("result") or {}
    raw = result.get("raw_events") or {}
    lo, hi = float(task["start"]), float(task["end"])

    def inside(values):
        out = []
        for value in values or []:
            try:
                t = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(t) and lo <= t < hi:
                out.append(t)
        return sorted(out)

    return inside(raw.get("beats")), inside(raw.get("downbeats"))


def build_suggestion_lines(prediction, task):
    """Human-readable revealed suggestions. Raw scores are never percentages."""
    lines = []
    if not prediction:
        return ["No predictions file for this song yet. Nothing was inferred just now."]
    stages = prediction.get("stages", {})

    beats_stage = stages.get("beats", {})
    if beats_stage.get("status") == "completed":
        result = beats_stage.get("result") or {}
        tempo = result.get("tempo") or {}
        bpm = tempo.get("bpm")
        if bpm is None:
            lines.append("Tempo: uncertain — no reliable BPM estimate.")
        else:
            lines.append(f"Tempo estimate: {float(bpm):.1f} BPM (detector estimate, not accuracy).")
        beats, downs = predicted_events(prediction, task)
        lines.append(
            f"Predicted beats in this passage: {len(beats)} beat(s), {len(downs)} downbeat(s) "
            "(blue/purple on the waveform; orange/white are your reference markers)."
        )
    else:
        detail = beats_stage.get("error") or "stage not completed"
        lines.append(f"Tempo/beats unavailable ({detail}).")

    if task["kind"] == "source_key":
        key_stage = stages.get("source_key", {})
        if key_stage.get("status") == "completed":
            result = key_stage.get("result") or {}
            candidates = result.get("candidates") or []
            top = candidates[:3]
            if top:
                rendered = ", ".join(
                    f"{c.get('key')} (raw score {float(c.get('score', 0)):.3f})" for c in top
                )
                lines.append(f"First-minute key — top 3: {rendered}.")
            else:
                lines.append(f"First-minute key: {result.get('state', 'uncertain')} — no ranked candidates.")
            if result.get("reason"):
                lines.append(f"Key note: {result['reason']}")
        else:
            detail = key_stage.get("error") or "stage not completed"
            lines.append(f"First-minute key unavailable ({detail}).")
    else:
        local_stage = stages.get("local_keys", {})
        if local_stage.get("status") == "completed":
            result = local_stage.get("result") or {}
            entry = result.get(task["id"]) or {}
            if entry:
                keys = ", ".join(entry.get("keys") or []) or "none"
                lines.append(
                    f"Local key evidence for this passage: state={entry.get('state', 'uncertain')}; "
                    f"keys: {keys}."
                )
                if entry.get("reason"):
                    lines.append(f"Local key note: {entry['reason']}")
            else:
                lines.append("Local key evidence unavailable for this passage.")
        else:
            detail = local_stage.get("error") or "stage not completed"
            lines.append(f"Local key evidence unavailable ({detail}).")
        # Also offer first-minute context when present.
        key_stage = stages.get("source_key", {})
        if key_stage.get("status") == "completed":
            result = key_stage.get("result") or {}
            top = (result.get("candidates") or [])[:3]
            if top:
                rendered = ", ".join(
                    f"{c.get('key')} (raw score {float(c.get('score', 0)):.3f})" for c in top
                )
                lines.append(f"First-minute key (context) — top 3: {rendered}.")

    content_stage = stages.get("content", {})
    if content_stage.get("status") == "completed":
        result = content_stage.get("result") or {}
        windows = [
            w for w in (result.get("windows") or [])
            if float(w.get("end", 0)) > float(task["start"]) and float(w.get("start", 0)) < float(task["end"])
        ]
        if windows:
            lines.append("Coarse content observations (raw scores, not accuracy percentages):")
            for w in windows[:4]:
                scores = w.get("scores") or {}
                rendered = ", ".join(f"{k} raw {float(v):.3f}" for k, v in sorted(scores.items()))
                lines.append(
                    f"  {float(w['start']):.1f}–{float(w['end']):.1f}s state={w.get('state', '?')}: {rendered}."
                )
            if len(windows) > 4:
                lines.append(f"  …and {len(windows) - 4} more overlapping window(s).")
        else:
            lines.append("Content: no overlapping observation windows for this passage.")
    else:
        detail = content_stage.get("error") or "stage not completed"
        lines.append(f"Content observations unavailable ({detail}).")
    lines.append("Raw scores are detector outputs, not accuracy percentages.")
    return lines


class PilotWaveform(QWidget):
    """Real cached peaks for one task, with reference/predicted markers."""

    seekRequested = Signal(float)
    beatsChanged = Signal()
    selectionChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.peaks: np.ndarray | None = None
        self.task_start = 0.0
        self.task_end = 0.0
        self.reference_beats: list[dict] = []
        self.predicted_beats: list[float] = []
        self.predicted_downbeats: list[float] = []
        self.show_predictions = False
        self.position = 0.0
        self.selected_index = -1
        self.add_mode = False
        self.available = False
        self._drag_index = -1
        self._drag_moved = False
        self.setMinimumHeight(190)
        self.setAccessibleName("Source waveform for the selected listening task")
        self.setToolTip(
            "Real cached source waveform for this task only, in source seconds. "
            "When 'Add beats' is off, click to listen from there (paused stays paused). "
            "When it is on, click to add a beat; drag a selected marker to move it."
        )
        self.setAccessibleDescription(self.toolTip())

    # -- data ---------------------------------------------------------
    def set_task(self, peaks, start, end):
        self.peaks = None if peaks is None else np.asarray(peaks, dtype=float)
        self.task_start, self.task_end = float(start), float(end)
        self.available = self.peaks is not None and len(self.peaks) > 0
        self.position = self.task_start
        self.selected_index = -1
        self.update()

    def set_reference_beats(self, beats):
        self.reference_beats = sorted_beats(beats)
        if self.selected_index >= len(self.reference_beats):
            self.selected_index = -1
        self.update()

    def set_predictions(self, beats, downbeats, visible):
        self.predicted_beats = list(beats)
        self.predicted_downbeats = list(downbeats)
        self.show_predictions = bool(visible)
        self.update()

    def set_position(self, seconds):
        self.position = float(seconds)
        self.update()

    def set_selected(self, index):
        self.selected_index = index if 0 <= index < len(self.reference_beats) else -1
        self.selectionChanged.emit(self.selected_index)
        self.update()

    # -- geometry -----------------------------------------------------
    def _duration(self):
        return max(1e-9, self.task_end - self.task_start)

    def time_at_x(self, x):
        frac = min(1.0, max(0.0, x / max(1, self.width())))
        return self.task_start + frac * self._duration()

    def x_at_time(self, t):
        return (float(t) - self.task_start) / self._duration() * max(1, self.width())

    def _nearest_index(self, x, tolerance=9):
        best, best_dist = -1, tolerance + 1
        for i, beat in enumerate(self.reference_beats):
            dist = abs(self.x_at_time(beat["time"]) - x)
            if dist < best_dist:
                best, best_dist = i, dist
        return best

    def _pooled(self, columns):
        if not self.available or columns < 1:
            return None
        peaks = np.clip(np.nan_to_num(self.peaks, nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)
        peak_max = float(np.max(peaks)) if len(peaks) else 0.0
        if peak_max > 0:
            peaks = peaks / peak_max
        if len(peaks) <= columns:
            # Sparse samples still cover the entire time range. Padding them
            # on the left would move visible transients away from their times.
            return peaks[np.minimum((np.arange(columns) * len(peaks) / columns).astype(int), len(peaks)-1)]
        edges = np.linspace(0, len(peaks), columns + 1).astype(int)
        return np.array([np.max(peaks[edges[i]: max(edges[i] + 1, edges[i + 1])]) for i in range(columns)])

    # -- painting -----------------------------------------------------
    def paintEvent(self, event):  # noqa: N802 (Qt name)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(T.FIELD))
        width, height = self.width(), self.height()
        plot_h = height - 44
        if not self.available:
            painter.setPen(QColor(T.TEXT_2))
            painter.setFont(T.font(12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Waveform unavailable for this task")
            return
        columns = self._pooled(max(1, width))
        if columns is not None:
            middle, half = 8 + plot_h / 2, plot_h / 2 - 4
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(T.color(T.MID, 0.9))
            for i, value in enumerate(columns):
                amp = max(0.6, float(value) * half)
                x = i + 0.5
                painter.drawLine(int(x), int(middle - amp), int(x), int(middle + amp))
            # Redraw as 1px bars via rects for visibility at small heights.
            painter.setBrush(T.color(T.HIGH, 0.35))
            for i, value in enumerate(columns):
                amp = max(0.6, float(value) * half)
                painter.fillRect(i, int(middle - amp), 1, max(1, int(2 * amp)), T.color(T.MID, 0.85))
        top, bottom = 8, 8 + plot_h
        # Predicted markers first so reference markers stay on top.
        if self.show_predictions:
            for t in self.predicted_beats:
                x = round(self.x_at_time(t))
                painter.setPen(QPen(QColor("#5CCBEA"), 1))
                painter.drawLine(x, top + 6, x, bottom - 6)
            for t in self.predicted_downbeats:
                x = round(self.x_at_time(t))
                painter.setPen(QPen(QColor("#CF8FFF"), 2))
                painter.drawLine(x, top, x, bottom)
        for i, beat in enumerate(self.reference_beats):
            x = round(self.x_at_time(beat["time"]))
            selected = i == self.selected_index
            if beat["downbeat"]:
                painter.setPen(QPen(QColor("#FFFFFF"), 3 if selected else 2))
                painter.drawLine(x, top - 2, x, bottom + 2)
                painter.setBrush(QColor("#FFFFFF"))
                painter.drawEllipse(x - 3, top - 2, 6, 6)
            else:
                painter.setPen(QPen(QColor(T.ACCENT), 3 if selected else 2))
                painter.drawLine(x, top + 4, x, bottom - 4)
            if selected:
                painter.setPen(QPen(QColor(T.PLAYING), 1))
                painter.drawRect(x - 5, top, 10, plot_h)
        # Playhead.
        px = round(self.x_at_time(self.position))
        painter.setPen(QPen(QColor(T.TEXT), 2))
        painter.drawLine(px, top, px, bottom)
        # Time ticks in source seconds.
        painter.setPen(QColor(T.TEXT_3))
        painter.setFont(T.font(11))
        span = self._duration()
        step = 1.0 if span <= 12 else 2.0 if span <= 25 else 5.0
        t = math.ceil(self.task_start / step) * step
        while t < self.task_end:
            x = self.x_at_time(t)
            if 0 <= x < width - 44:
                painter.drawText(int(x) + 2, height - 24, 48, 16,
                                 Qt.AlignmentFlag.AlignLeft, clock(t, True))
            t += step
        painter.setPen(QColor(T.TEXT_2))
        painter.drawText(2, height - 6, width - 4, 14, Qt.AlignmentFlag.AlignLeft,
                         f"Source {self.task_start:.1f}s – {self.task_end:.1f}s")

    # -- interaction --------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self.available:
            return
        x = event.position().x()
        near = self._nearest_index(x)
        self._drag_moved = False
        if near >= 0:
            self._drag_index = near
            self.set_selected(near)
            return
        self._drag_index = -1
        when = round(self.time_at_x(x), 3)
        when = min(max(when, self.task_start), math.nextafter(self.task_end, self.task_start))
        if self.add_mode:
            beats = sorted_beats(self.reference_beats + [{"time": when, "downbeat": False}])
            # New beats are never downbeats until explicitly marked.
            self.reference_beats = beats
            self.selected_index = next(i for i, b in enumerate(beats) if b["time"] == when)
            self.selectionChanged.emit(self.selected_index)
            self.beatsChanged.emit()
            self.update()
        else:
            self.set_selected(-1)
            self.seekRequested.emit(when)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_index < 0 or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        beats = self.reference_beats
        i = self._drag_index
        lo = beats[i - 1]["time"] + 0.01 if i > 0 else self.task_start
        hi = beats[i + 1]["time"] - 0.01 if i + 1 < len(beats) else math.nextafter(self.task_end, self.task_start)
        when = min(max(round(self.time_at_x(event.position().x()), 3), lo), hi)
        if when != beats[i]["time"]:
            beats[i] = {**beats[i], "time": when}
            self._drag_moved = True
            self.beatsChanged.emit()
            self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag_index = -1


class PilotReviewWindow(QMainWindow):
    """Review workbench: navigation, waveform, transport, references."""

    def __init__(self, pilot_file, parent=None):
        super().__init__(parent)
        self.pilot_file = Path(pilot_file).resolve()
        self.pilot = dp.load_pilot(self.pilot_file)
        self._review_lock = QLockFile(str(self.pilot_file.parent / '.review.lock'))
        if not self._review_lock.tryLock(0):
            raise ValueError('This pilot is already open in another review window. Close that window first.')
        self.settings = QSettings(str(self.pilot_file.parent / 'review-ui.ini'), QSettings.Format.IniFormat)
        self.reviewer = str(self.settings.value("reviewer", "") or "")
        self.current_task_id: str | None = None
        self._baseline: dict = dp.empty_reference()
        self._revealed = False
        self._prediction: dict = {}
        self._available = False
        self._audio_path: Path | None = None
        self._record: dict | None = None
        self._task: dict | None = None
        self._loading = False
        self._pending_seek = None
        self._pending_play = False
        self._help_dialog = None
        self._devices = QMediaDevices(self)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.7)

        self.setWindowTitle("DJ Studio · Detector pilot review")
        self.setMinimumSize(1000, 750)
        self.resize(1120, 820)
        self._build_ui()
        self._connect_player()
        initial = self._initial_task_id()
        self._select_task(initial, force=True)

    # -- construction -------------------------------------------------
    def _build_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(splitter)

        left = QFrame(); left.setObjectName("panel"); left.setMinimumWidth(280); left.setMaximumWidth(360)
        lv = QVBoxLayout(left); lv.setContentsMargins(12, 12, 12, 12); lv.setSpacing(8)
        title = QLabel("Detector pilot review"); title.setProperty("role", "title"); lv.addWidget(title)
        intro = QLabel("Judge each passage from listening only. Suggestions stay hidden until you save and reveal them.")
        intro.setWordWrap(True); intro.setProperty("role", "small"); lv.addWidget(intro)
        self.help_button = QPushButton('How to review…')
        self.help_button.setToolTip('Open the offline instructions for voices, keys, beat markers and independent review. F1 also opens this guide.')
        self.help_button.clicked.connect(self._show_help); lv.addWidget(self.help_button)
        self.help_shortcut = QShortcut('F1', self); self.help_shortcut.activated.connect(self._show_help)
        lv.addWidget(QLabel("Reviewer name or alias"))
        self.reviewer_edit = QLineEdit(self.reviewer)
        self.reviewer_edit.setPlaceholderText("e.g. Alex")
        self.reviewer_edit.setAccessibleName("Reviewer name or alias")
        self.reviewer_edit.setToolTip("Your answers are remembered per reviewer in references.json beside pilot.json. Changing reviewer loads that reviewer's saved answers and hides suggestions.")
        self.reviewer_edit.setAccessibleDescription(self.reviewer_edit.toolTip())
        lv.addWidget(self.reviewer_edit)
        self.progress_label = QLabel(""); self.progress_label.setWordWrap(True)
        self.progress_label.setProperty("role", "small"); lv.addWidget(self.progress_label)
        lv.addWidget(QLabel("Songs & listening tasks"))
        self.task_list = QListWidget()
        self.task_list.setAccessibleName("Songs and listening tasks")
        self.task_list.setToolTip("All listening tasks. No tempo, key or confidence is shown here. Select a task to load its real waveform and audio; nothing plays automatically.")
        self.task_list.setAccessibleDescription(self.task_list.toolTip())
        for task in self.pilot["tasks"]:
            record = dp.record_for_id(self.pilot, task["record_id"])
            item = QListWidgetItem(self._task_item_text(task, record, reviewed=False))
            item.setData(Qt.ItemDataRole.UserRole, task["id"])
            item.setToolTip(f"{record['name']} · {task['name']} · source {task['start']:.1f}–{task['end']:.1f}s. Select to review; no playback starts automatically.")
            self.task_list.addItem(item)
        lv.addWidget(self.task_list, 1)
        self.exposure_warning = QLabel(""); self.exposure_warning.setWordWrap(True)
        self.exposure_warning.setProperty("role", "small"); lv.addWidget(self.exposure_warning)
        exposure_note = QLabel(EXPOSURE_NOTE); exposure_note.setWordWrap(True)
        exposure_note.setProperty('role', 'small'); lv.addWidget(exposure_note)
        for w in (self.exposure_warning,):
            w.setProperty("role", "small")
        splitter.addWidget(left)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget(); rv = QVBoxLayout(content); rv.setContentsMargins(14, 14, 14, 14); rv.setSpacing(10)
        scroll.setWidget(content); splitter.addWidget(scroll)
        splitter.setStretchFactor(1, 1)

        self.task_title = QLabel(""); self.task_title.setProperty("role", "title"); self.task_title.setWordWrap(True); rv.addWidget(self.task_title)
        self.task_scope = QLabel(""); self.task_scope.setWordWrap(True); self.task_scope.setProperty("role", "secondary"); rv.addWidget(self.task_scope)
        self.unavailable_label = QLabel(""); self.unavailable_label.setWordWrap(True); self.unavailable_label.setVisible(False); rv.addWidget(self.unavailable_label)

        self.waveform = PilotWaveform(); rv.addWidget(self.waveform)
        self.wave_legend = QLabel("Orange = your beat · white = your beat 1/downbeat · white line = playhead. Predicted beats appear only after reveal (blue/purple).")
        self.wave_legend.setWordWrap(True); self.wave_legend.setProperty("role", "small"); rv.addWidget(self.wave_legend)

        beat_row = QHBoxLayout()
        self.add_beats_check = QCheckBox("Add beats by clicking")
        self.add_beats_check.setToolTip("On: clicking the waveform adds a beat at that source second (never a downbeat until you mark it). Off: clicking seeks; paused audio stays paused. Drag a selected marker to move it.")
        self.add_beats_check.setAccessibleDescription(self.add_beats_check.toolTip())
        beat_row.addWidget(self.add_beats_check)
        self.selected_label = QLabel("No marker selected"); self.selected_label.setProperty("role", "small")
        self.downbeat_button = QPushButton("Mark selected as beat 1")
        self.downbeat_button.setToolTip("Explicitly mark the selected marker as a downbeat (beat 1). New markers are never downbeats automatically.")
        self.delete_beat_button = QPushButton("Delete selected")
        self.delete_beat_button.setToolTip("Delete the selected reference marker. This only edits your unsaved/saved reference, never predictions.")
        beat_row.addWidget(self.downbeat_button); beat_row.addWidget(self.delete_beat_button); beat_row.addStretch()
        rv.addLayout(beat_row)
        rv.addWidget(self.selected_label)

        transport = QFrame(); transport.setObjectName("controlBar")
        tv = QVBoxLayout(transport); tv.setContentsMargins(10, 8, 10, 8); tv.setSpacing(6)
        row = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.setToolTip("Play or pause the prepared decoded audio at original pitch and tempo. Seeking keeps paused audio paused.")
        self.replay_button = QPushButton("Replay passage")
        self.replay_button.setToolTip("Play again from the start of this listening task at original pitch and tempo.")
        self.loop_check = QCheckBox("Loop passage")
        self.loop_check.setToolTip("Loop only this task's source seconds while playing.")
        self.time_label = QLabel("0.0s"); self.time_label.setProperty("role", "small")
        row.addWidget(self.play_button); row.addWidget(self.replay_button); row.addWidget(self.loop_check); row.addWidget(self.time_label); row.addStretch()
        tv.addLayout(row)
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setSingleStep(100); self.seek_slider.setPageStep(1000)
        self.seek_slider.setAccessibleName("Position within this listening task")
        self.seek_slider.setToolTip("Seek within this task in source seconds. Left/Right moves 0.1 second; Page Up/Down moves one second. Seeking keeps paused audio paused.")
        self.seek_slider.setAccessibleDescription(self.seek_slider.toolTip())
        tv.addWidget(self.seek_slider)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Volume"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal); self.volume_slider.setRange(0, 100); self.volume_slider.setValue(70)
        self.volume_slider.setMaximumWidth(150); self.volume_slider.setAccessibleName("Playback volume")
        self.volume_slider.setToolTip("Playback volume only; it does not change any file or mix.")
        row2.addWidget(self.volume_slider)
        row2.addWidget(QLabel("Output"))
        self.device_combo = QComboBox(); self.device_combo.setAccessibleName("Audio output device")
        self.device_combo.setToolTip("Choose the system default or a specific output device for listening.")
        row2.addWidget(self.device_combo, 1)
        tv.addLayout(row2)
        rv.addWidget(transport)

        rv.addWidget(QLabel("Your reference (saved locally, per reviewer)"))
        key_row = QHBoxLayout(); key_row.addWidget(QLabel("Key"))
        self.key_combo = QComboBox(); self.key_combo.setAccessibleName("Reference key")
        self.key_combo.setToolTip("Judge the key you hear. 'Unclear', 'No key' and 'Not qualified' are explicit expert options — use them instead of guessing. For a source-key task this is the first-minute key; for a passage it is the key for that passage window as instructed by the task scope.")
        for choice in dp.KEY_CHOICES:
            self.key_combo.addItem(KEY_DISPLAY.get(choice, choice), choice)
            if choice == "not_qualified":
                self.key_combo.insertSeparator(self.key_combo.count())
        key_row.addWidget(self.key_combo, 1); rv.addLayout(key_row)

        self.passage_box = QWidget(); pv = QVBoxLayout(self.passage_box); pv.setContentsMargins(0, 0, 0, 0); pv.setSpacing(8)
        grid = QHBoxLayout()
        self.voice_combo = self._choice_combo("Audible voice (singing, speech or vocal samples) anywhere in this passage")
        self.pitched_combo = self._choice_combo("Audible pitched material (notes/chords with pitch) in this passage")
        self.drums_combo = self._choice_combo("Drums only: only drums/percussion audible, with no voice or pitched material")
        for label, combo in (("Voices present?", self.voice_combo), ("Musical notes present?", self.pitched_combo), ("Only unpitched drums?", self.drums_combo)):
            col = QVBoxLayout(); col.addWidget(QLabel(label)); col.addWidget(combo); grid.addLayout(col)
        pv.addLayout(grid)
        trow = QHBoxLayout(); trow.addWidget(QLabel("Timing"))
        self.timing_combo = QComboBox(); self.timing_combo.setAccessibleName("Timing status")
        self.timing_combo.setToolTip("Marked beats means you added beat markers. No audible beat means there are no beats to mark; drifting or irregular beats can still be marked. Timing completeness is a separate, explicit judgment.")
        for choice in dp.TIMING_CHOICES:
            self.timing_combo.addItem(TIMING_DISPLAY[choice], choice)
        trow.addWidget(self.timing_combo)
        self.timing_complete_check = QCheckBox("Timing fully reviewed")
        self.timing_complete_check.setToolTip("Check only after marking every audible beat, or confirming there are no audible beats anywhere in this passage. Partial or uncertain annotations stay incomplete.")
        trow.addWidget(self.timing_complete_check); trow.addStretch(); pv.addLayout(trow)
        rv.addWidget(self.passage_box)
        self.source_key_note = QLabel("Source-key task: judge only the key above. Voice, pitched, drums and timing stay Not reviewed and no beats are saved for this task.")
        self.source_key_note.setWordWrap(True); self.source_key_note.setProperty("role", "small"); rv.addWidget(self.source_key_note)
        rv.addWidget(QLabel("Notes"))
        self.notes_edit = QPlainTextEdit(); self.notes_edit.setAccessibleName("Reviewer notes")
        self.notes_edit.setToolTip("Optional notes about what you heard. Saved with your reference.")
        self.notes_edit.setMaximumHeight(90); rv.addWidget(self.notes_edit)

        reveal_row = QHBoxLayout()
        self.reveal_button = QPushButton("Reveal suggestions")
        self.reveal_button.setToolTip("Save your reference first. Revealing records exposure for this whole song for you, then shows detector suggestions. Hidden again when you change task or reviewer.")
        reveal_row.addWidget(self.reveal_button)
        self.reveal_state = QLabel("Suggestions hidden. Save your reference to enable Reveal.")
        self.reveal_state.setWordWrap(True); self.reveal_state.setProperty("role", "small")
        reveal_row.addWidget(self.reveal_state, 1); rv.addLayout(reveal_row)
        self.suggestions_label = QLabel(""); self.suggestions_label.setWordWrap(True)
        self.suggestions_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.suggestions_label.setVisible(False); rv.addWidget(self.suggestions_label)

        save_row = QHBoxLayout()
        self.save_button = QPushButton("Save reference"); self.save_button.setProperty("role", "primary")
        self.save_button.setToolTip("Save your reference for this task and reviewer to references.json beside pilot.json.")
        self.save_next_button = QPushButton("Save & Next")
        self.save_next_button.setToolTip("Save this reference, then move to the next task in the list.")
        self.dirty_label = QLabel(""); self.dirty_label.setProperty("role", "small")
        save_row.addWidget(self.save_button); save_row.addWidget(self.save_next_button); save_row.addWidget(self.dirty_label); save_row.addStretch()
        rv.addLayout(save_row)
        self.status_label = QLabel(""); self.status_label.setWordWrap(True); rv.addWidget(self.status_label)
        rv.addStretch()

        # connections
        self.task_list.currentItemChanged.connect(self._on_list_changed)
        self.reviewer_edit.editingFinished.connect(self._on_reviewer_finished)
        self.add_beats_check.toggled.connect(lambda on: setattr(self.waveform, "add_mode", on))
        self.waveform.seekRequested.connect(self._seek_to)
        self.waveform.beatsChanged.connect(self._on_edited)
        self.waveform.selectionChanged.connect(self._refresh_marker_controls)
        self.downbeat_button.clicked.connect(self._mark_downbeat)
        self.delete_beat_button.clicked.connect(self._delete_beat)
        self.play_button.clicked.connect(self._toggle_play)
        self.replay_button.clicked.connect(self._replay)
        self.seek_slider.sliderMoved.connect(lambda ms: self._seek_to(self._task["start"] + ms / 1000) if self._task else None)
        self.seek_slider.valueChanged.connect(lambda ms: self._seek_to(self._task['start'] + ms / 1000) if self._task and not self.seek_slider.isSliderDown() else None)
        self.volume_slider.valueChanged.connect(lambda v: self.audio_output.setVolume(v / 100))
        self.device_combo.currentIndexChanged.connect(self._apply_device)
        for combo in (self.key_combo, self.voice_combo, self.pitched_combo, self.drums_combo, self.timing_combo):
            combo.currentIndexChanged.connect(self._on_edited)
        self.timing_complete_check.toggled.connect(self._on_edited)
        self.notes_edit.textChanged.connect(self._on_edited)
        self.save_button.clicked.connect(lambda: self._save_current())
        self.save_next_button.clicked.connect(self._save_and_next)
        self.reveal_button.clicked.connect(self._reveal)
        self._refresh_devices()
        self._devices.audioOutputsChanged.connect(self._refresh_devices)
        for widget in self.findChildren(QWidget):
            if widget.toolTip() and not widget.accessibleDescription():
                widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton):
                widget.setAutoDefault(False)

    def _choice_combo(self, tooltip):
        combo = QComboBox(); combo.setToolTip(tooltip); combo.setAccessibleDescription(tooltip)
        for choice in dp.CHOICES:
            combo.addItem(CHOICE_DISPLAY[choice], choice)
        return combo

    def _connect_player(self):
        self.player.positionChanged.connect(self._on_position)
        self.player.playbackStateChanged.connect(self._refresh_play_button)
        self.player.mediaStatusChanged.connect(self._media_ready)
        self.player.errorOccurred.connect(
            lambda *_: self._set_status("Playback failed: " + self.player.errorString()))

    # -- helpers --------------------------------------------------------
    def _set_status(self, text):
        self.status_label.setText(text)

    def _show_help(self):
        if self._help_dialog is None:
            path = Path(__file__).with_name('DETECTOR_PILOT.md')
            if not path.is_file():
                self._set_status('Pilot instructions are missing: ' + str(path)); return
            dialog = QDialog(self); dialog.setWindowTitle('Detector pilot · How to review')
            dialog.resize(820, 720); layout = QVBoxLayout(dialog)
            text = QTextBrowser(); text.setOpenExternalLinks(False)
            text.setMarkdown(path.read_text(encoding='utf-8')); layout.addWidget(text)
            close = QPushButton('Close guide'); close.clicked.connect(dialog.hide); layout.addWidget(close)
            self._help_dialog = dialog
        self._help_dialog.show(); self._help_dialog.raise_()

    def _task_item_text(self, task, record, reviewed):
        mark = " ✓" if reviewed else ""
        return f"{record['name']} · {task['name']} ({task['start']:.0f}–{task['end']:.0f}s){mark}"

    def _initial_task_id(self):
        for task in self.pilot["tasks"]:
            if task["kind"] == "passage" and task["name"] == "Beginning":
                return task["id"]
        for task in self.pilot["tasks"]:
            if task["kind"] == "passage":
                return task["id"]
        return self.pilot["tasks"][0]["id"]

    def _reviewed_ids(self):
        try:
            reviews = dp.load_reviews(self.pilot_file)
        except (OSError, ValueError, KeyError):
            return set()
        latest = {}
        for entry in reviews["entries"]:
            if self.reviewer and entry["reviewer"] != self.reviewer:
                continue
            latest[entry["task_id"]] = entry
        return {
            tid for tid, e in latest.items()
            if any(e["reference"][f] != "not_reviewed" for f in ("voice", "pitched", "drums_only", "key", "timing_status"))
        }

    def _refresh_progress(self):
        reviewed = self._reviewed_ids()
        total = len(self.pilot["tasks"])
        who = f" for {self.reviewer}" if self.reviewer else ""
        self.progress_label.setText(
            f"{len(reviewed)} of {total} tasks have some reference{who} (saved locally). "
            "This counts any saved field, not every field verified."
        )
        for row in range(self.task_list.count()):
            item = self.task_list.item(row)
            task = dp.task_for_id(self.pilot, item.data(Qt.ItemDataRole.UserRole))
            record = dp.record_for_id(self.pilot, task["record_id"])
            item.setText(self._task_item_text(task, record, task["id"] in reviewed))

    def _refresh_exposure_warning(self):
        exposed = False
        if self.reviewer and self._record:
            try:
                reviews = dp.load_reviews(self.pilot_file)
                exposed = any(
                    e["record_id"] == self._record["id"] and e["reviewer"] == self.reviewer
                    for e in reviews["exposures"]
                )
            except (OSError, ValueError, KeyError):
                exposed = False
        if exposed:
            self.exposure_warning.setText(
                "This song was already revealed to you in this tool. Suggestions stay hidden until you reveal again. "
                + EXPOSURE_NOTE
            )
        else:
            self.exposure_warning.setText("")

    def _refresh_devices(self):
        current = self.device_combo.currentData()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem("System default", "system")
        for device in QMediaDevices.audioOutputs():
            if device.isNull():
                continue
            self.device_combo.addItem(device.description(), bytes(device.id()).hex())
        index = self.device_combo.findData(current) if current else 0
        if current not in (None, 'system') and index < 0:
            self.player.pause(); self._pending_play = False
            self._set_status('Selected output disconnected. Playback paused; choose an output before playing again.')
        self.device_combo.setCurrentIndex(max(0, index))
        self.device_combo.blockSignals(False)
        self._apply_device()

    def _apply_device(self):
        choice = self.device_combo.currentData()
        if choice in (None, "system"):
            device = QMediaDevices.defaultAudioOutput()
        else:
            device = next((d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == choice), None)
            if device is None:
                device = QMediaDevices.defaultAudioOutput()
        if device is not None and not device.isNull():
            self.audio_output.setDevice(device)

    # -- reference state ------------------------------------------------
    def _set_combo(self, combo, value):
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def _collect_reference(self):
        beats = []
        if self._task and self._task["kind"] == "passage":
            beats = sorted_beats(self.waveform.reference_beats)
        reference = {
            "voice": self.voice_combo.currentData(),
            "pitched": self.pitched_combo.currentData(),
            "drums_only": self.drums_combo.currentData(),
            "key": self.key_combo.currentData(),
            "timing_status": self.timing_combo.currentData(),
            "timing_complete": bool(self.timing_complete_check.isChecked()),
            "beats": beats,
            "notes": self.notes_edit.toPlainText(),
        }
        if self._task and self._task["kind"] == "source_key":
            reference.update({"voice": "not_reviewed", "pitched": "not_reviewed",
                              "drums_only": "not_reviewed", "timing_status": "not_reviewed",
                              "timing_complete": False, "beats": []})
        return reference

    def _is_dirty(self):
        return self._collect_reference() != self._baseline

    def _load_reference_into_ui(self, reference):
        self._loading = True
        try:
            self._set_combo(self.key_combo, reference["key"])
            self._set_combo(self.voice_combo, reference["voice"])
            self._set_combo(self.pitched_combo, reference["pitched"])
            self._set_combo(self.drums_combo, reference["drums_only"])
            self._set_combo(self.timing_combo, reference["timing_status"])
            self.timing_complete_check.setChecked(bool(reference["timing_complete"]))
            self.notes_edit.setPlainText(reference["notes"])
            self.waveform.set_reference_beats(reference["beats"])
        finally:
            self._loading = False
        self._baseline = self._collect_reference()
        self._update_dirty_ui()

    def _on_edited(self, *_):
        if self._loading:
            return
        # Adding a first beat implies an annotated timing intent; completeness stays explicit.
        if self._task and self._task["kind"] == "passage" and self.waveform.reference_beats:
            if self.timing_combo.currentData() == "not_reviewed":
                self._loading = True
                self._set_combo(self.timing_combo, "annotated")
                self._loading = False
        self._update_dirty_ui()

    def _update_dirty_ui(self):
        dirty = self._is_dirty()
        saved = False
        if self.current_task_id and self.reviewer:
            try:
                saved = dp.latest_reference(self.pilot_file, self.current_task_id, self.reviewer) is not None
            except (OSError, ValueError, KeyError):
                saved = False
        self.dirty_label.setText('Unsaved changes' if dirty else 'Answer saved locally' if saved else 'No saved answer yet')
        self.reveal_button.setEnabled(bool(saved and not dirty))
        if self._revealed:
            pass
        elif not self.reviewer:
            self.reveal_state.setText("Suggestions hidden. Enter your reviewer alias, then save to enable Reveal.")
        elif dirty:
            self.reveal_state.setText("Suggestions hidden. Save your current reference to enable Reveal.")
        elif saved:
            self.reveal_state.setText("Suggestions hidden. Reveal will record exposure for this song, then show them.")
        else:
            self.reveal_state.setText("Suggestions hidden. Save your reference to enable Reveal.")
        self._refresh_marker_controls()

    def _refresh_marker_controls(self, *_):
        index = self.waveform.selected_index
        count = len(self.waveform.reference_beats)
        valid = 0 <= index < count
        self.downbeat_button.setEnabled(valid)
        self.delete_beat_button.setEnabled(valid)
        if valid:
            beat = self.waveform.reference_beats[index]
            kind = "beat 1/downbeat" if beat["downbeat"] else "beat"
            self.selected_label.setText(f"Selected marker {index + 1}/{count}: {beat['time']:.3f}s ({kind})")
        elif count:
            self.selected_label.setText(f"{count} marker(s); click one to select, drag to move")
        else:
            self.selected_label.setText("No markers yet")

    def _mark_downbeat(self):
        index = self.waveform.selected_index
        if not (0 <= index < len(self.waveform.reference_beats)):
            return
        beats = [dict(b) for b in self.waveform.reference_beats]
        beats[index]["downbeat"] = True
        self.waveform.set_reference_beats(beats)
        self.waveform.set_selected(index)
        self._on_edited()

    def _delete_beat(self):
        index = self.waveform.selected_index
        if not (0 <= index < len(self.waveform.reference_beats)):
            return
        beats = [dict(b) for b in self.waveform.reference_beats]
        del beats[index]
        self.waveform.set_reference_beats(beats)
        self.waveform.set_selected(min(index, len(beats) - 1))
        self._on_edited()

    # -- unsaved-changes guard -------------------------------------------
    def _confirm_unsaved(self, context):
        """True to proceed (saved or discarded), False to cancel."""
        if not self._is_dirty():
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved reference changes")
        box.setText(f"You have unsaved reference changes ({context}). Save them before continuing?")
        box.setInformativeText("Save writes locally to references.json. Discard loses only these unsaved edits.")
        save_btn = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard_btn = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_btn:
            return self._save_current()
        if clicked is discard_btn:
            return True
        return False

    # -- task loading -----------------------------------------------------
    def _select_task(self, task_id, *, force=False):
        if not force and task_id == self.current_task_id:
            return
        if not force and not self._confirm_unsaved("changing task"):
            # Revert list selection.
            self.task_list.blockSignals(True)
            for row in range(self.task_list.count()):
                if self.task_list.item(row).data(Qt.ItemDataRole.UserRole) == self.current_task_id:
                    self.task_list.setCurrentRow(row)
                    break
            self.task_list.blockSignals(False)
            return
        self._hide_suggestions()
        self.current_task_id = task_id
        self.task_list.blockSignals(True)
        for row in range(self.task_list.count()):
            if self.task_list.item(row).data(Qt.ItemDataRole.UserRole) == task_id:
                self.task_list.setCurrentRow(row)
                break
        self.task_list.blockSignals(False)
        self._load_task()

    def _on_list_changed(self, current, _previous):
        if current is None or self._loading:
            return
        task_id = current.data(Qt.ItemDataRole.UserRole)
        if task_id != self.current_task_id:
            self._select_task(task_id)

    def _load_task(self):
        task = dp.task_for_id(self.pilot, self.current_task_id)
        record = dp.record_for_id(self.pilot, task["record_id"])
        self._task, self._record = task, record
        is_passage = task["kind"] == "passage"
        self.passage_box.setVisible(is_passage)
        for widget in (self.add_beats_check, self.selected_label, self.downbeat_button, self.delete_beat_button):
            widget.setVisible(is_passage)
        self.add_beats_check.setChecked(False)
        self.source_key_note.setVisible(not is_passage)
        self.task_title.setText(f"{record['name']} · {task['name']}")
        span = task["end"] - task["start"]
        kind_label = "First-minute key task" if task["kind"] == "source_key" else "Passage task"
        self.task_scope.setText(
            f"{kind_label} · source {task['start']:.1f}s–{task['end']:.1f}s ({span:.1f}s) · "
            f"song duration {record['duration']:.1f}s. Times below are direct source seconds."
        )
        # Waveform + audio availability (already prepared files only).
        problems = []
        peaks = None
        try:
            waveform_path = dp.child(self.pilot_file, record["waveform"])
            if not waveform_path.is_file():
                problems.append(f"waveform file missing ({record['waveform']})")
            else:
                peaks = dp.load_waveform(self.pilot_file, record, task)
                if peaks is None or len(peaks) == 0:
                    problems.append("waveform has no peaks for this task")
        except Exception as error:  # damaged cache must not be faked
            problems.append(f"waveform unavailable ({error})")
        audio_path = None
        try:
            candidate = dp.child(self.pilot_file, record["audio"])
            if candidate.is_file():
                audio_path = candidate
            else:
                problems.append(f"decoded audio file missing ({record['audio']})")
        except Exception as error:
            problems.append(f"decoded audio unavailable ({error})")
        self._audio_path = audio_path
        self._available = not problems
        if problems:
            self.unavailable_label.setText(
                f"Task {task['id']} unavailable: " + "; ".join(problems)
                + ". Prepare the pilot analysis first — no waveform or audio was invented."
            )
            self.unavailable_label.setVisible(True)
            self.waveform.set_task(None, task["start"], task["end"])
        else:
            self.unavailable_label.setVisible(False)
            self.waveform.set_task(peaks, task["start"], task["end"])
        # Reference for this reviewer.
        entry = None
        if self.reviewer:
            try:
                entry = dp.latest_reference(self.pilot_file, task["id"], self.reviewer)
            except (OSError, ValueError, KeyError):
                entry = None
        reference = copy.deepcopy(entry["reference"]) if entry else dp.empty_reference()
        if task["kind"] == "source_key":
            reference = {**reference, "voice": "not_reviewed", "pitched": "not_reviewed",
                         "drums_only": "not_reviewed", "timing_status": "not_reviewed",
                         "timing_complete": False, "beats": []}
        self._load_reference_into_ui(reference)
        # Transport: load paused at task start; never autoplay.
        self._pending_seek = round(task['start'] * 1000)
        self._pending_play = False
        self.player.stop()
        if audio_path is not None:
            self.player.setSource(QUrl.fromLocalFile(str(audio_path)))
        else:
            self.player.setSource(QUrl())
        self._media_ready(self.player.mediaStatus())
        self.seek_slider.setRange(0, max(0, round(span * 1000)))
        self.seek_slider.setValue(0)
        self.waveform.set_position(task["start"])
        self.time_label.setText(f"{task['start']:.1f}s / {task['end']:.1f}s")
        self._refresh_play_button()
        for widget in (self.play_button, self.replay_button, self.seek_slider):
            widget.setEnabled(audio_path is not None)
        self._refresh_progress()
        self._refresh_exposure_warning()
        self._update_dirty_ui()
        if problems:
            self._set_status(f"Task {task['id']} unavailable — see above. No files were invented.")
        else:
            self._set_status(f"Loaded {record['name']} · {task['name']} ({task['start']:.1f}–{task['end']:.1f}s). Paused; press Play to listen.")

    def _on_reviewer_finished(self):
        new = self.reviewer_edit.text().strip()
        if new == self.reviewer:
            self.reviewer_edit.setText(self.reviewer)
            return
        if not self._confirm_unsaved("changing reviewer"):
            self.reviewer_edit.setText(self.reviewer)
            return
        self.reviewer = new
        self.reviewer_edit.setText(new)
        self.settings.setValue("reviewer", new)
        self._hide_suggestions()
        self._load_task()
        self._set_status(
            f"Showing saved answers for {new or 'no reviewer'}." if new else "Enter a reviewer alias to save and reveal."
        )

    # -- transport ---------------------------------------------------------
    def _refresh_play_button(self, *_):
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("Pause" if playing or self._pending_play else "Play")

    def _media_ready(self, status):
        if status not in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
                          QMediaPlayer.MediaStatus.EndOfMedia):
            return
        if self._pending_seek is not None:
            position, self._pending_seek = self._pending_seek, None
            self.player.setPosition(position)
        if self._pending_play:
            self._pending_play = False
            self.player.play()

    def _seek_to(self, seconds):
        if not self._task or self._audio_path is None:
            return
        seconds = min(max(float(seconds), self._task["start"]), self._task["end"])
        # Seeking never starts playback: paused stays paused.
        self._pending_seek = round(seconds * 1000)
        self._media_ready(self.player.mediaStatus())
        self.waveform.set_position(seconds)
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(round((seconds - self._task["start"]) * 1000))
        self.seek_slider.blockSignals(False)
        self.time_label.setText(f"{seconds:.1f}s / {self._task['end']:.1f}s")

    def _toggle_play(self):
        if self._audio_path is None or not self._task:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState or self._pending_play:
            self._pending_play = False
            self.player.pause()
        else:
            position = self._pending_seek if self._pending_seek is not None else self.player.position()
            if position < round(self._task['start'] * 1000) or position >= round(self._task['end'] * 1000):
                self._pending_seek = round(self._task['start'] * 1000)
            self._pending_play = True
            self._media_ready(self.player.mediaStatus())
        self._refresh_play_button()

    def _replay(self):
        if self._audio_path is None or not self._task:
            return
        self._pending_seek = round(self._task['start'] * 1000)
        self._pending_play = True
        self.waveform.set_position(self._task["start"])
        self._media_ready(self.player.mediaStatus())
        self._refresh_play_button()

    def _on_position(self, ms):
        if not self._task:
            return
        if self._pending_seek is not None:
            ms = self._pending_seek
        seconds = ms / 1000
        if seconds >= self._task['end'] - .02 and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if self.loop_check.isChecked():
                self.player.setPosition(round(self._task['start'] * 1000))
                seconds = self._task['start']
            else:
                self.player.pause()
                self._seek_to(self._task['end'])
                seconds = self._task['end']
        self.waveform.set_position(seconds)
        if not self.seek_slider.isSliderDown():
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(round((seconds - self._task["start"]) * 1000))
            self.seek_slider.blockSignals(False)
        self.time_label.setText(f"{seconds:.1f}s / {self._task['end']:.1f}s")

    # -- saving ------------------------------------------------------------
    def _save_current(self):
        if not self.current_task_id:
            return False
        # Save a dirty form to the alias whose answers it was loaded from,
        # including when a reviewer-change dialog asks to Save first.
        reviewer = self.reviewer or self.reviewer_edit.text().strip()
        if not reviewer:
            self._set_status("Enter a reviewer name or alias before saving.")
            self.reviewer_edit.setFocus()
            return False
        if reviewer != self.reviewer:
            self.reviewer = reviewer
            self.settings.setValue("reviewer", reviewer)
        reference = self._collect_reference()
        try:
            dp.save_reference(self.pilot_file, self.current_task_id, reviewer, reference)
        except Exception as error:
            self._set_status(f"Could not save reference: {error}")
            return False
        self._baseline = copy.deepcopy(reference)
        self._refresh_progress()
        self._refresh_exposure_warning()
        self._update_dirty_ui()
        target = self.pilot_file.parent / "references.json"
        self._set_status(f"Saved locally to {target} for {reviewer} · task {self.current_task_id}.")
        return True

    def _save_and_next(self):
        if not self._save_current():
            return
        row = self.task_list.currentRow()
        if row + 1 < self.task_list.count():
            next_id = self.task_list.item(row + 1).data(Qt.ItemDataRole.UserRole)
            self._select_task(next_id)
        else:
            self._set_status("Saved locally. This was the last task in the list.")

    # -- suggestions ---------------------------------------------------------
    def _hide_suggestions(self):
        self._revealed = False
        self._prediction = {}
        self.suggestions_label.setVisible(False)
        self.suggestions_label.setText("")
        self.waveform.set_predictions([], [], False)

    def _reveal(self):
        if not self.current_task_id or not self.reviewer:
            return
        if self._is_dirty():
            self._set_status("Save your current reference before revealing suggestions.")
            return
        try:
            if dp.latest_reference(self.pilot_file, self.current_task_id, self.reviewer) is None:
                self._set_status("Save your reference before revealing predictions.")
                return
        except (OSError, ValueError, KeyError) as error:
            self._set_status(f"Could not check saved reference: {error}")
            return
        # Exposure is recorded BEFORE any model output is loaded or shown.
        try:
            dp.mark_predictions_exposed(self.pilot_file, self.current_task_id, self.reviewer)
        except Exception as error:
            self._set_status(f"Could not record exposure: {error}")
            return
        try:
            prediction = dp.load_prediction(self.pilot_file, self._task["record_id"])
        except Exception as error:
            prediction = {}
            self._set_status(f"Exposure recorded, but predictions could not be read: {error}")
        self._prediction = prediction
        beats, downs = predicted_events(prediction, self._task)
        lines = build_suggestion_lines(prediction, self._task)
        header = "Suggestions (recorded as exposed for this song) — " + EXPOSURE_NOTE
        self.suggestions_label.setText(header + "\n\n" + "\n".join(lines))
        self.waveform.set_predictions(beats, downs, True)
        self.suggestions_label.setVisible(True)
        self._revealed = True
        self.reveal_state.setText("Suggestions shown for this task only. They hide when you change task or reviewer.")
        self._refresh_exposure_warning()
        self._set_status("Exposure recorded before showing suggestions. Suggestions are shown for this task only.")

    # -- window ------------------------------------------------------------
    def closeEvent(self, event):  # noqa: N802
        if self._confirm_unsaved("closing"):
            self._pending_play = False
            self.player.stop()
            self._review_lock.unlock()
            event.accept()
        else:
            event.ignore()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Native detector pilot review workbench (no inference).")
    parser.add_argument("pilot", nargs="?", type=Path, default=Path("outputs/dataset-pilot/pilot.json"),
                        help="Path to pilot.json (default: outputs/dataset-pilot/pilot.json)")
    args = parser.parse_args(argv)
    pilot_file = Path(args.pilot)
    if not pilot_file.is_file():
        print(f"Pilot file not found: {pilot_file}", file=sys.stderr)
        return 2
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Detector Pilot Review")
    app.setFont(T.font())
    app.setPalette(T.palette())
    app.setStyleSheet(T.stylesheet())
    try:
        window = PilotReviewWindow(pilot_file)
    except Exception as error:
        print(f"Could not open pilot: {error}", file=sys.stderr)
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
