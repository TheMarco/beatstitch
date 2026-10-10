#!/usr/bin/env python3
"""DJ Studio: a native Qt desktop arrangement editor. No browser or web server."""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import Qt, QObject, Signal, QRunnable, QThreadPool, QMimeData, QRectF, QTimer, QUrl, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QFontDatabase, QDrag, QKeySequence, QShortcut, QDesktopServices
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QAbstractScrollArea, QSplitter, QFrame, QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox,
    QFormLayout, QFileDialog, QMessageBox, QSlider, QProgressBar, QStyle, QScrollArea, QSizePolicy)

import djmix
from studio_engine import Session, Track, Clip, layout, preview_range, render_session, load_track, cache_track, estimate_track, blend_advice, suggest_order


ROOT = Path(__file__).resolve().parent
FORMATS = {".wav", ".mp3", ".flac", ".aif", ".aiff", ".m4a", ".ogg", ".aac"}
ACCENT = "#cef18a"
COLORS = ["#a6d7c1", "#94bdd9", "#d5b69a", "#b9b4d5"]


def clock(seconds):
    return f"{int(seconds)//60:02d}:{int(seconds)%60:02d}"


def label(text, role=""):
    widget = QLabel(text)
    if role:
        widget.setObjectName(role)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    return widget


class WorkerSignals(QObject):
    progress = Signal(str)
    item = Signal(object)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, function, cancel):
        super().__init__()
        self.function, self.cancel = function, cancel
        self.signals = WorkerSignals()

    def run(self):
        try:
            def progress(message):
                if self.cancel.is_set():
                    raise djmix.MixError("Operation cancelled.")
                self.signals.progress.emit(message)
            self.signals.result.emit(self.function(progress, self.signals.item.emit))
        except Exception as error:
            self.signals.error.emit(str(error))
        finally:
            self.signals.finished.emit()


class LibraryTable(QTableWidget):
    addRequested = Signal(str)

    def __init__(self):
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["TRACK", "BPM", "KEY", "TIME"])
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(56)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index, width in ((1, 65), (2, 65), (3, 70)):
            self.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(index, width)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.doubleClicked.connect(lambda index: self.addRequested.emit(self.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)))

    def startDrag(self, actions):
        row = self.currentRow()
        if row < 0:
            return
        data = QMimeData()
        data.setData("application/x-dj-track", self.item(row, 0).data(Qt.ItemDataRole.UserRole).encode())
        drag = QDrag(self)
        drag.setMimeData(data)
        drag.exec(Qt.DropAction.CopyAction)


class Timeline(QAbstractScrollArea):
    trackDropped = Signal(str, int)
    clipMoved = Signal(str, int)
    selected = Signal(str)
    seekRequested = Signal(float)

    def __init__(self, session):
        super().__init__()
        self.session = session
        self.selection = ""
        self.scale = 2.0
        self.playhead = 0
        self.drag_origin = None
        self.drag_clip = None
        self.drop_x = None
        self.setAcceptDrops(True)
        self.setMinimumHeight(250)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.horizontalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAccessibleName("Arrangement timeline. Drag tracks here from the library.")

    def positions(self):
        return layout(self.session)

    def refresh(self):
        positions = self.positions()
        duration = positions[-1]["end"] / djmix.SR if positions else 120
        self.horizontalScrollBar().setRange(0, max(0, round(duration * self.scale + 100 - self.viewport().width())))
        self.horizontalScrollBar().setPageStep(self.viewport().width())
        self.viewport().update()

    def fit(self):
        positions = self.positions()
        duration = positions[-1]["end"] / djmix.SR if positions else 120
        self.scale = max(0.1, (self.viewport().width() - 75) / max(30, duration))
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()

    def x(self, seconds):
        return 28 + seconds * self.scale - self.horizontalScrollBar().value()

    def time_at(self, x):
        return max(0, (x - 28 + self.horizontalScrollBar().value()) / self.scale)

    def clip_rect(self, position, index):
        lane = max(100, (self.viewport().height()-58)/2)
        return QRectF(self.x(position["start"]/djmix.SR), 58 + (index % 2)*lane,
                      position["length"]/djmix.SR*self.scale, min(112,lane-22))

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.viewport().rect(), QColor("#131719"))
        w, h = self.viewport().width(), self.viewport().height()
        painter.fillRect(0, 0, w, 35, QColor("#1b2022"))
        bar = 240/self.session.target_bpm
        step = bar
        while step*self.scale < 70:
            step *= 2
        left = self.time_at(0)
        cursor = math.floor(left/step)*step
        painter.setFont(QFont(self.font().family(), 10))
        while self.x(cursor) < w:
            x = self.x(cursor)
            painter.setPen(QColor("#283032"))
            painter.drawLine(int(x), 35, int(x), h)
            painter.setPen(QColor("#929e9c"))
            painter.drawText(QRectF(x+5, 4, 70, 24), clock(cursor))
            cursor += step
        painter.setPen(QColor("#687875"))
        painter.drawText(16, 51, "A")
        painter.drawText(16, int(51+max(100,(h-58)/2)), "B")
        positions = self.positions()
        for index, (clip, position) in enumerate(zip(self.session.clips, positions)):
            track = self.session.tracks[clip.track_id]
            rect = self.clip_rect(position, index)
            if rect.right() < 0 or rect.left() > w:
                continue
            color = QColor(COLORS[index % len(COLORS)])
            painter.setPen(QPen(QColor(ACCENT) if clip.id == self.selection else QColor("#465754"), 2 if clip.id == self.selection else 1))
            background = QColor(color); background.setAlpha(24)
            painter.setBrush(background)
            painter.drawRoundedRect(rect, 7, 7)
            painter.save()
            painter.setClipRect(rect.adjusted(2, 2, -2, -2))
            if track.waveform:
                waveform = track.waveform
                first = clip.cue_in / track.duration * len(waveform)
                extent = (clip.cue_out-clip.cue_in)/track.duration*len(waveform)
                painter.setPen(QPen(color, 1.3))
                for pixel in range(max(0, int(rect.left())+7), min(w, int(rect.right())-7), 2):
                    phase = (pixel-rect.left())/rect.width()
                    sample = min(len(waveform)-1, max(0, int(first+phase*extent)))
                    amplitude = max(1.5, waveform[sample]*max(8,(rect.height()-64)/2))
                    center = rect.top()+rect.height()*.59
                    painter.drawLine(int(pixel), int(center-amplitude), int(pixel), int(center+amplitude))
            painter.setPen(QColor("#ebf1eb"))
            font = QFont(self.font()); font.setBold(True); painter.setFont(font)
            text = painter.fontMetrics().elidedText(track.name, Qt.TextElideMode.ElideRight, max(10, int(rect.width()-24)))
            painter.drawText(QRectF(rect.left()+12, rect.top()+10, rect.width()-24, 24), text)
            painter.setFont(self.font()); painter.setPen(color)
            key = djmix.key_name(djmix.transpose(djmix.parse_key(track.key), clip.shift)) if track.key else "Key pending"
            text = f"{track.bpm:.1f} BPM  ·  {key}  ·  {'original key' if not clip.shift else f'{clip.shift:+d} st'}"
            text = painter.fontMetrics().elidedText(text,Qt.TextElideMode.ElideRight,max(10,int(rect.width()-24)))
            painter.drawText(QRectF(rect.left()+12, rect.bottom()-22, rect.width()-24, 20), text)
            painter.restore()
            if index:
                blend = QRectF(self.x(position["start"]/djmix.SR), 38,
                               position["incoming"]/djmix.SR*self.scale, h-38)
                painter.fillRect(blend, QColor(206, 241, 138, 14))
                painter.setPen(QPen(QColor("#789358"), 1, Qt.PenStyle.DashLine))
                painter.drawLine(int(blend.left()), 38, int(blend.left()), h)
                painter.drawLine(int(blend.right()), 38, int(blend.right()), h)
        if not positions:
            painter.setPen(QColor("#b6c2bd"))
            painter.setFont(QFont(self.font().family(), 18))
            painter.drawText(QRectF(40, 110, w-80, 50), Qt.AlignmentFlag.AlignCenter, "Your next mix starts here")
            painter.setFont(self.font()); painter.setPen(QColor("#83908b"))
            painter.drawText(QRectF(40, 158, w-80, 65), Qt.AlignmentFlag.AlignCenter,
                             "Drag a song from your library.\nOverlaps are arranged automatically.")
        if self.drop_x is not None:
            painter.setPen(QPen(QColor(ACCENT), 3)); painter.drawLine(int(self.drop_x), 35, int(self.drop_x), h)
        if positions:
            x = self.x(self.playhead)
            painter.setPen(QPen(QColor("#f4f6ef"), 1.5)); painter.drawLine(int(x), 35, int(x), h)
            painter.setBrush(QColor("#f4f6ef")); painter.drawEllipse(QRectF(x-3, 29, 6, 6))
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.drag_origin, self.drag_clip = event.position(), None
        for index, position in enumerate(self.positions()):
            if self.clip_rect(position, index).contains(event.position()):
                self.drag_clip = position["clip_id"]
                self.selected.emit(self.drag_clip)
                return
        self.seekRequested.emit(self.time_at(event.position().x()))

    def mouseMoveEvent(self, event):
        if self.drag_clip and self.drag_origin and (event.position()-self.drag_origin).manhattanLength() > 10:
            data = QMimeData(); data.setData("application/x-dj-clip", self.drag_clip.encode())
            drag = QDrag(self); drag.setMimeData(data)
            self.drag_clip = None
            drag.exec(Qt.DropAction.MoveAction)

    def insertion(self, x):
        seconds = self.time_at(x)
        positions = self.positions()
        for index, pos in enumerate(positions):
            if seconds < (pos["start"]+pos["length"]/2)/djmix.SR:
                return index
        return len(positions)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-dj-track") or event.mimeData().hasFormat("application/x-dj-clip"):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        self.drop_x = event.position().x(); self.viewport().update(); event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.drop_x = None; self.viewport().update()

    def dropEvent(self, event):
        index = self.insertion(event.position().x())
        mime = event.mimeData()
        if mime.hasFormat("application/x-dj-track"):
            self.trackDropped.emit(bytes(mime.data("application/x-dj-track")).decode(), index)
        elif mime.hasFormat("application/x-dj-clip"):
            self.clipMoved.emit(bytes(mime.data("application/x-dj-clip")).decode(), index)
        self.drop_x = None; self.viewport().update(); event.acceptProposedAction()


STYLE = """
QMainWindow, QWidget { background:#111517; color:#e5ece5; font-size:13px; }
QLabel { background:transparent; }
QLabel#brand { font-size:23px; font-weight:750; letter-spacing:-1px; color:#cef18a; }
QLabel#title { font-size:18px; font-weight:650; }
QLabel#muted { color:#899793; font-size:12px; }
QLabel#eyebrow { color:#8f9f98; font-size:10px; font-weight:650; letter-spacing:1.4px; }
QLabel#time { font-size:27px; font-weight:500; }
QLabel#selectedTitle { font-size:19px; font-weight:650; }
QPushButton { background:#242d2f; border:1px solid #35403f; border-radius:6px; padding:9px 14px; font-weight:550; }
QPushButton:hover { background:#303c3c; border-color:#657567; }
QPushButton:pressed { background:#3c4843; }
QPushButton:disabled { color:#64716a; background:#1a2222; border-color:#2c3533; }
QPushButton#primary { color:#192516; background:#cef18a; border-color:#cef18a; }
QPushButton#primary:hover { background:#defaaa; }
QPushButton#primary:disabled { background:#525f42; border-color:#525f42; color:#9ea991; }
QPushButton#play { font-size:18px; min-width:34px; min-height:30px; color:#172315; background:#cef18a; border:0; }
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox { background:#1b2325; border:1px solid #394441; border-radius:5px; padding:8px; selection-background-color:#527752; }
QDoubleSpinBox, QSpinBox, QComboBox { min-height:22px; }
QAbstractSpinBox QLineEdit { border:0; padding:0; background:transparent; }
QScrollArea { border:0; background:transparent; }
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus { border-color:#cef18a; }
QLineEdit:disabled, QSpinBox:disabled { color:#637169; }
QComboBox QAbstractItemView { background:#242e2b; selection-background-color:#3e5744; }
QTableWidget { border:0; background:#161c1d; alternate-background-color:#181f20; selection-background-color:#2b3931; selection-color:#eef5e8; outline:0; }
QTableWidget::item { border-bottom:1px solid #252f2d; padding:8px; }
QHeaderView::section { background:#1b2324; color:#8e9f96; font-size:10px; font-weight:650; padding:10px 6px; border:0; border-bottom:1px solid #35413b; }
QFrame#panel { background:#171e20; border:1px solid #2c3634; border-radius:8px; }
QSplitter::handle { background:#111517; width:10px; }
QCheckBox { spacing:10px; padding:6px 0; }
QCheckBox::indicator { width:18px; height:18px; border:1px solid #708074; background:#222d28; border-radius:4px; }
QCheckBox::indicator:checked { background:#cef18a; border:3px solid #4f694a; }
QSlider::groove:horizontal { height:4px; background:#34413a; border-radius:2px; }
QSlider::handle:horizontal { width:12px; background:#b7c6b5; margin:-4px 0; border-radius:6px; }
QSlider::sub-page:horizontal { background:#94b36a; border-radius:2px; }
QScrollBar:horizontal { height:10px; background:#19211f; }
QScrollBar:vertical { width:10px; background:#19211f; }
QScrollBar::handle { background:#46594c; border-radius:4px; min-width:25px; min-height:25px; }
QScrollBar::add-line, QScrollBar::sub-line { width:0; height:0; }
QProgressBar { border:0; background:#26352b; max-height:3px; }
QProgressBar::chunk { background:#cef18a; }
QToolTip { background:#27332e; color:#edf3e7; border:1px solid #667b62; padding:6px; }
"""


class Studio(QMainWindow):
    def __init__(self, data_dir: Path, project: Path | None = None):
        super().__init__()
        self.data = data_dir; self.data.mkdir(parents=True, exist_ok=True)
        self.session = Session()
        self.project_path = None
        self.selected_id = ""
        self.jobs = set(); self.cancel = threading.Event(); self.closing = False
        self.rendering = False; self.importing = False; self.updating = False
        self.history = []; self.touched = set()
        self.revision = 0; self.audio_revision = -1; self.audio_origin = 0
        self.setWindowTitle("DJ Studio")
        self.resize(1500, 910); self.setMinimumSize(1120, 740)
        self.setStyleSheet(STYLE)
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(2)
        self.audio = QAudioOutput(self); self.audio.setVolume(0.7)
        self.media_devices = QMediaDevices(self)
        self.output_preference = "system"
        try:self.output_preference=json.loads((self.data/"preferences.json").read_text()).get("audio_output","system")
        except (OSError,ValueError):pass
        self.player = QMediaPlayer(self); self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playbackStateChanged.connect(self.playback_changed)
        self.player.errorOccurred.connect(lambda *args: self.status.setText("Playback: " + self.player.errorString()))
        self.build_ui()
        self.media_devices.audioOutputsChanged.connect(self.refresh_audio_outputs)
        self.refresh_audio_outputs()
        for sequence, action in (("Ctrl+S", self.save_project), ("Ctrl+O", self.open_project),
                                 ("Ctrl+Z", self.undo), ("Ctrl+I", self.import_folder)):
            shortcut = QShortcut(QKeySequence(sequence), self); shortcut.activated.connect(action)
        if project:
            self.load_project(project)
        elif (self.data / "autosave.djmix").exists():
            try:
                self.load_project(self.data / "autosave.djmix")
                self.project_path = None
            except Exception:
                pass
        self.refresh()

    def build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(20, 18, 20, 14); outer.setSpacing(16)
        top = QHBoxLayout(); top.setSpacing(18)
        top.addWidget(label("DJ / STUDIO", "brand"))
        self.project_title = label("Untitled mix", "title"); top.addWidget(self.project_title)
        top.addStretch()
        self.new_btn = button("New", self.new_project); top.addWidget(self.new_btn)
        self.open_btn = button("Open project", self.open_project); top.addWidget(self.open_btn)
        top.addWidget(button("Save project", self.save_project))
        self.export_btn = button("Export mix", self.export_mix, True); top.addWidget(self.export_btn)
        outer.addLayout(top)
        transport = QFrame(); transport.setObjectName("panel")
        bar = QHBoxLayout(transport); bar.setContentsMargins(18, 12, 18, 12); bar.setSpacing(16)
        self.play = button("▶", self.toggle_play); self.play.setObjectName("play"); self.play.setAccessibleName("Play or pause rendered audio")
        bar.addWidget(self.play)
        stop = button("■", self.player.stop); stop.setAccessibleName("Stop playback"); bar.addWidget(stop)
        self.time_label = label("00:00", "time"); self.time_label.setMinimumWidth(91); bar.addWidget(self.time_label)
        self.play_label = label("Preview a blend to start listening", "muted");self.play_label.setMinimumWidth(0)
        self.play_label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);bar.addWidget(self.play_label, 1)
        bar.addWidget(label("PROJECT TEMPO", "eyebrow"))
        self.tempo = QDoubleSpinBox(); self.tempo.setRange(20, 300); self.tempo.setDecimals(2); self.tempo.setValue(128)
        self.tempo.setSuffix(" BPM"); self.tempo.setFixedWidth(130); self.tempo.editingFinished.connect(self.change_tempo); bar.addWidget(self.tempo)
        bar.addWidget(label("OUTPUT", "eyebrow"))
        self.output_device=QComboBox();self.output_device.setAccessibleName("Audio output device")
        self.output_device.setMinimumContentsLength(14);self.output_device.setMaximumWidth(230)
        self.output_device.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.output_device.currentIndexChanged.connect(self.change_audio_output);bar.addWidget(self.output_device)
        self.volume = QSlider(Qt.Orientation.Horizontal); self.volume.setRange(0,100); self.volume.setValue(70); self.volume.setFixedWidth(90)
        self.volume.valueChanged.connect(lambda value:self.audio.setVolume(value/100)); bar.addWidget(self.volume)
        outer.addWidget(transport)
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.setRange(0,0)
        self.seek.sliderMoved.connect(self.player.setPosition); self.player.durationChanged.connect(lambda value:self.seek.setRange(0, int(value)))
        outer.addWidget(self.seek)
        body = QSplitter(Qt.Orientation.Horizontal)
        library = QFrame(); library.setObjectName("panel")
        left = QVBoxLayout(library); left.setContentsMargins(14,16,14,12); left.setSpacing(12)
        library_header = QHBoxLayout(); library_header.addWidget(label("YOUR LIBRARY", "eyebrow")); library_header.addStretch()
        self.library_count = label("0 songs", "muted"); library_header.addWidget(self.library_count); left.addLayout(library_header)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search your tracks…"); self.search.textChanged.connect(self.filter_library); left.addWidget(self.search)
        self.table = LibraryTable(); self.table.addRequested.connect(lambda identity:self.add_track(identity, len(self.session.clips))); left.addWidget(self.table,1)
        tools = QHBoxLayout(); self.import_btn = button("Import folder", self.import_folder); tools.addWidget(self.import_btn)
        self.files_btn = button("Add files", self.import_files); tools.addWidget(self.files_btn); left.addLayout(tools)
        self.analyze_btn = button("Analyze selected song", self.analyze_selected); left.addWidget(self.analyze_btn)
        hint = label("Drag to the timeline or double-click to add.\nEstimated BPM and keys stay editable.", "muted"); hint.setWordWrap(True); left.addWidget(hint)
        body.addWidget(library)
        center = QWidget(); middle = QVBoxLayout(center); middle.setContentsMargins(2,2,2,0); middle.setSpacing(12)
        timeline_header = QHBoxLayout(); timeline_header.addWidget(label("ARRANGEMENT", "eyebrow"))
        self.arrangement_count = label("0 tracks", "muted"); timeline_header.addWidget(self.arrangement_count); timeline_header.addStretch()
        timeline_header.addWidget(button("Fit", lambda:self.timeline.fit()))
        self.order_btn = button("Suggest order", self.propose_order); timeline_header.addWidget(self.order_btn)
        self.zoom = QSlider(Qt.Orientation.Horizontal); self.zoom.setRange(0,70); self.zoom.setValue(33); self.zoom.setFixedWidth(90)
        self.zoom.valueChanged.connect(self.zoom_changed); timeline_header.addWidget(self.zoom); middle.addLayout(timeline_header)
        self.timeline = Timeline(self.session); self.timeline.trackDropped.connect(self.add_track)
        self.timeline.clipMoved.connect(self.move_clip); self.timeline.selected.connect(self.select_clip); self.timeline.seekRequested.connect(self.seek_timeline)
        middle.addWidget(self.timeline,1)
        self.blend_card = QFrame(); self.blend_card.setObjectName("panel")
        details = QVBoxLayout(self.blend_card); details.setContentsMargins(20,20,20,20); details.setSpacing(12)
        details.addWidget(label("TRANSITION REVIEW", "eyebrow"))
        self.transition_select=QComboBox();self.transition_select.setAccessibleName("Transition to preview")
        self.transition_select.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.transition_select.setMinimumContentsLength(20)
        self.transition_select.currentIndexChanged.connect(self.select_transition);details.addWidget(self.transition_select)
        self.blend_title = label("Make room for the next track", "title"); self.blend_title.setWordWrap(True); details.addWidget(self.blend_title)
        self.harmony = label("Select an incoming track to inspect and preview its blend.", "muted"); self.harmony.setWordWrap(True); details.addWidget(self.harmony)
        self.preview_btn = button("Preview this blend", self.preview, True); details.addWidget(self.preview_btn)
        self.render_btn = button("Render full arrangement for playback", self.render_full); details.addWidget(self.render_btn)
        middle.addWidget(self.blend_card)
        middle.addWidget(label("8-bar overlaps by default  ·  Original keys preserved  ·  Audio stays local", "muted"))
        body.addWidget(center)
        inspector = QFrame(); inspector.setObjectName("panel")
        right = QVBoxLayout(inspector); right.setContentsMargins(18,18,18,18); right.setSpacing(16)
        right.addWidget(label("TRACK & BLEND", "eyebrow"))
        self.clip_title = label("Select a track", "selectedTitle"); self.clip_title.setWordWrap(True); right.addWidget(self.clip_title)
        self.clip_state = label("Your controls live here", "muted"); self.clip_state.setWordWrap(True); right.addWidget(self.clip_state)
        form = QFormLayout(); form.setVerticalSpacing(14)
        self.bpm = QDoubleSpinBox(); self.bpm.setDecimals(3); self.bpm.setRange(20,300); self.bpm.setSingleStep(.1)
        self.key = QComboBox(); self.key.addItem("Unknown"); self.key.addItems([f"{n} {mode}" for mode in ("major","minor") for n in djmix.NOTES])
        self.cue_in = QDoubleSpinBox(); self.cue_out = QDoubleSpinBox()
        for spin in (self.cue_in,self.cue_out):
            spin.setDecimals(3); spin.setRange(0,100000); spin.setSingleStep(.25); spin.setSuffix(" s")
        self.bars = QSpinBox(); self.bars.setRange(1,32); self.bars.setSuffix(" bars")
        form.addRow("Source BPM", self.bpm); form.addRow("Source key", self.key)
        form.addRow("In point", self.cue_in); form.addRow("Out point", self.cue_out); form.addRow("Incoming blend", self.bars)
        right.addLayout(form)
        self.transpose = QCheckBox("Transpose this track"); right.addWidget(self.transpose)
        self.shift = QSpinBox(); self.shift.setRange(-12,12); self.shift.setSuffix(" semitones"); right.addWidget(self.shift)
        self.shift_hint = label("Original key is the default.", "muted"); self.shift_hint.setWordWrap(True); right.addWidget(self.shift_hint)
        self.bass = QCheckBox("Use a bass handoff"); self.bass.setChecked(True); right.addWidget(self.bass)
        self.intro_drums = QCheckBox("Intro overlap is beats only"); right.addWidget(self.intro_drums)
        self.outro_drums = QCheckBox("Outro overlap is beats only"); right.addWidget(self.outro_drums)
        for check in (self.intro_drums,self.outro_drums):
            check.setToolTip("Mark only if the entire overlap has no pitched bass, vocals, melody, or tonal tails. This changes harmonic advice, not the audio.")
        self.apply_btn = button("Apply track settings", self.apply_settings); right.addWidget(self.apply_btn)
        right.addStretch()
        reorder = QHBoxLayout(); self.earlier = button("← Earlier", lambda:self.nudge(-1)); reorder.addWidget(self.earlier)
        self.later = button("Later →", lambda:self.nudge(1)); reorder.addWidget(self.later); right.addLayout(reorder)
        self.remove_btn = button("Remove from mix", self.remove_clip); right.addWidget(self.remove_btn)
        self.inspector_fields = [self.bpm,self.key,self.cue_in,self.cue_out,self.bars,self.transpose,self.shift,self.bass,self.intro_drums,self.outro_drums,self.apply_btn,self.earlier,self.later,self.remove_btn]
        self.transpose.toggled.connect(self.transpose_changed)
        inspector_scroll=QScrollArea(); inspector_scroll.setWidgetResizable(True); inspector_scroll.setWidget(inspector)
        inspector_scroll.setMinimumWidth(280); inspector_scroll.setMaximumWidth(350)
        body.addWidget(inspector_scroll); body.setSizes([420,720,300]); outer.addWidget(body,1)
        self.progress = QProgressBar(); self.progress.setRange(0,0); self.progress.hide(); outer.addWidget(self.progress)
        footer = QHBoxLayout(); self.status = label("Ready", "muted"); footer.addWidget(self.status,1)
        footer.addWidget(label("NATIVE DESKTOP  /  LOCAL AUDIO", "eyebrow")); outer.addLayout(footer)

    def refresh_audio_outputs(self):
        devices=QMediaDevices.audioOutputs();default=QMediaDevices.defaultAudioOutput()
        self.output_device.blockSignals(True);self.output_device.clear()
        self.output_device.addItem("System default · "+(default.description() or "Unavailable"),"system")
        for device in devices:
            self.output_device.addItem(device.description(),bytes(device.id()).hex())
        index=self.output_device.findData(self.output_preference)
        if index<0:
            # A disconnected, explicitly selected headset must not silently send
            # playback to speakers. Remember it so reconnecting restores the route.
            self.output_device.addItem("Selected output disconnected",self.output_preference)
            index=self.output_device.count()-1
        self.output_device.setCurrentIndex(index);self.output_device.blockSignals(False)
        chosen=default if self.output_preference=="system" else next((d for d in devices if bytes(d.id()).hex()==self.output_preference),None)
        if chosen is None or chosen.isNull():
            self.player.pause();self.audio.setMuted(True)
            self.status.setText("Audio output unavailable. Connect it or choose another output.")
            return
        self.audio.setMuted(False)
        if self.audio.device().id()!=chosen.id():self.audio.setDevice(chosen)
        self.output_device.setToolTip("Playback output: "+chosen.description())

    def change_audio_output(self,index):
        if index<0:return
        self.output_preference=self.output_device.itemData(index)
        (self.data/"preferences.json").write_text(json.dumps({"audio_output":self.output_preference}))
        self.refresh_audio_outputs()
        self.status.setText(self.output_device.toolTip())

    def job(self, function, finished, *, importing=False, rendering=False):
        worker = Worker(function, self.cancel); self.jobs.add(worker)
        self.importing |= importing; self.rendering |= rendering
        worker.signals.progress.connect(self.status.setText)
        worker.signals.item.connect(self.receive_track)
        worker.signals.result.connect(finished)
        worker.signals.error.connect(self.show_error)
        def done():
            self.jobs.discard(worker)
            if importing:self.importing=False
            if rendering:self.rendering=False
            self.refresh()
            if not self.jobs:
                self.progress.hide()
                if self.closing:self.close()
        worker.signals.finished.connect(done)
        self.progress.show(); self.refresh(); self.pool.start(worker)

    def show_error(self, message):
        self.status.setText(message)
        if not self.closing:
            QMessageBox.warning(self, "DJ Studio", message)

    def checkpoint(self):
        self.history.append({"clips":copy.deepcopy(self.session.clips),"tempo":self.session.target_bpm,
                             "metadata":{k:(t.bpm,t.key,t.status,t.estimated) for k,t in self.session.tracks.items()}})
        self.history=self.history[-30:]

    def changed(self):
        self.revision+=1; self.player.pause()
        self.session.save(self.data/"autosave.djmix")
        self.refresh()

    def refresh(self):
        self.updating=True
        self.project_title.setText(self.session.name)
        self.tempo.setValue(self.session.target_bpm)
        self.timeline.session=self.session; self.timeline.selection=self.selected_id; self.timeline.refresh()
        positions=layout(self.session)
        self.transition_select.blockSignals(True);self.transition_select.clear()
        for index in range(1,len(self.session.clips)):
            a,b=self.session.clips[index-1:index+1]
            self.transition_select.addItem(f"{index} → {index+1} · {self.session.tracks[a.track_id].name} → {self.session.tracks[b.track_id].name}",b.id)
        selected_index=next((i for i,c in enumerate(self.session.clips) if c.id==self.selected_id),1)
        self.transition_select.setCurrentIndex(max(0,selected_index-1));self.transition_select.blockSignals(False)
        self.transition_select.setEnabled(len(positions)>1 and not self.rendering)
        pair=max(1,selected_index)
        self.preview_btn.setText(f"Preview {pair} → {pair+1}" if len(positions)>1 else "Preview this blend")
        duration=positions[-1]["end"]/djmix.SR if positions else 0
        self.arrangement_count.setText(f"{len(positions)} tracks · {clock(duration)}")
        self.library_count.setText(f"{len(self.session.tracks)} songs")
        self.export_btn.setEnabled(bool(positions) and not self.rendering)
        self.render_btn.setEnabled(bool(positions) and not self.rendering)
        self.preview_btn.setEnabled(len(positions)>1 and not self.rendering)
        for widget in (self.import_btn,self.files_btn,self.analyze_btn): widget.setEnabled(not self.importing and not self.rendering)
        self.open_btn.setEnabled(not self.jobs); self.tempo.setEnabled(not self.rendering)
        self.new_btn.setEnabled(not self.jobs); self.order_btn.setEnabled(len(positions)>2 and not self.rendering)
        self.timeline.setEnabled(not self.rendering)
        clip=self.selected_clip()
        for widget in self.inspector_fields:widget.setEnabled(clip is not None and not self.rendering)
        if clip:
            track=self.session.tracks[clip.track_id]; index=self.session.clips.index(clip)
            self.clip_title.setText(track.name); self.clip_state.setText(track.status)
            self.bpm.setValue(track.bpm or 128)
            self.key.setCurrentText(track.key if track.key else "Unknown")
            self.cue_in.setMaximum(track.duration); self.cue_out.setMaximum(track.duration)
            self.cue_in.setValue(clip.cue_in); self.cue_out.setValue(clip.cue_out)
            self.bars.setValue(clip.blend_bars); self.bars.setEnabled(index>0 and not self.rendering)
            self.transpose.setChecked(clip.transpose); self.shift.setValue(clip.semitones)
            self.shift.setEnabled(clip.transpose and not self.rendering); self.bass.setChecked(clip.bass_swap)
            self.intro_drums.setChecked(clip.intro_percussive); self.outro_drums.setChecked(clip.outro_percussive)
            self.earlier.setEnabled(index>0 and not self.rendering); self.later.setEnabled(index<len(positions)-1 and not self.rendering)
            preview_index=max(1,index) if len(positions)>1 else 0
            if preview_index:
                a,b=self.session.clips[preview_index-1:preview_index+1]
                ta,tb=self.session.tracks[a.track_id],self.session.tracks[b.track_id]
                advice=blend_advice(self.session,a,b)
                self.blend_title.setText(f"{ta.name}  →  {tb.name}")
                self.harmony.setText(f"{b.blend_bars} bars · {'Bass handoff' if b.bass_swap else 'Smooth crossfade'}\n"+advice["assessment"]+"\nKeys and phrase starts are estimates. Your preview decides.")
                suggestions=advice["suggested_b_shifts"]
                suggestion=suggestions[0] if suggestions else None
                self.shift_hint.setText((f"Suggested for incoming track: {suggestion['semitones']:+d} st → {suggestion['resulting_key']}. Optional." if index and suggestion else "Original key is preserved until you enable transposition."))
            else:
                self.blend_title.setText("Add a second track to create a blend")
                self.harmony.setText("Each song keeps its original key unless you choose to transpose it.")
        else:
            self.clip_title.setText("Select a track"); self.clip_state.setText("Click a waveform to edit its settings.")
            self.blend_title.setText("Make room for the next track")
            self.harmony.setText("Select an incoming track to inspect and preview its blend.")
        self.updating=False

    def selected_clip(self):
        return next((clip for clip in self.session.clips if clip.id==self.selected_id),None)

    def receive_track(self, track):
        if track.id in self.touched and track.id in self.session.tracks:
            self.session.tracks[track.id].waveform=track.waveform
        else:
            self.session.tracks[track.id]=track
        self.populate_library(); self.session.save(self.data/"autosave.djmix"); self.refresh()

    def populate_library(self):
        selected=self.table.item(self.table.currentRow(),0)
        identity=selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self.table.setRowCount(len(self.session.tracks))
        for row,track in enumerate(self.session.tracks.values()):
            values=[track.name, f"{track.bpm:.1f}" if track.bpm else "—", track.key.replace(" minor","m").replace(" major","") or "—", clock(track.duration)]
            for column,value in enumerate(values):
                item=QTableWidgetItem(value); item.setData(Qt.ItemDataRole.UserRole,track.id)
                item.setToolTip(f"{track.name}\n{track.status}\n{track.path}")
                if column==2:item.setForeground(QColor(ACCENT))
                if column:item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row,column,item)
            if identity==track.id:self.table.selectRow(row)
        self.filter_library(self.search.text())

    def filter_library(self, query):
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, query.casefold() not in self.table.item(row,0).text().casefold())

    def import_folder(self):
        folder=QFileDialog.getExistingDirectory(self,"Import music folder")
        if folder:self.import_paths(sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in FORMATS))

    def import_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,"Import songs",filter="Audio (*.wav *.mp3 *.flac *.aif *.aiff *.m4a *.ogg *.aac)")
        if paths:self.import_paths([Path(p) for p in paths])

    def import_paths(self, paths):
        if self.importing or self.rendering:return
        existing=copy.deepcopy(self.session.tracks); cache=self.data/"library-cache"
        def work(progress,emit):
            pending=[]; failures=[]
            for path in paths:
                progress(f"Importing {path.name}")
                try:
                    track=load_track(path,cache)
                    if track.id in existing and existing[track.id].bpm:
                        prior=existing[track.id]; prior.waveform=track.waveform; track=prior
                    emit(copy.deepcopy(track))
                    if not track.bpm:pending.append(track)
                    else:cache_track(track,cache)
                except Exception as error:failures.append(f"{path.name}: {error}")
            for index,track in enumerate(pending):
                progress(f"Estimating BPM & key · {index+1}/{len(pending)} · {track.name}")
                try:
                    track=estimate_track(track); cache_track(track,cache)
                except Exception as error:
                    track.status=f"Needs manual analysis: {error}"
                emit(copy.deepcopy(track))
            return failures
        def finished(failures):
            self.status.setText("Library ready. Drag songs to the timeline." if not failures else "Some files could not be imported: "+"; ".join(failures))
            self.timeline.fit()
        self.job(work,finished,importing=True)

    def analyze_selected(self):
        row=self.table.currentRow()
        if row<0:return
        track=copy.deepcopy(self.session.tracks[self.table.item(row,0).data(Qt.ItemDataRole.UserRole)])
        if any(c.track_id==track.id for c in self.session.clips):
            self.status.setText("This song is already arranged. Edit its BPM/key in Track & Blend to preserve your cue choices.")
            return
        def work(progress,emit):
            progress("Analyzing "+track.name); result=estimate_track(track); cache_track(result,self.data/"library-cache"); emit(result)
        self.job(work,lambda _:self.status.setText("Analysis complete. Values remain editable."),importing=True)

    def add_track(self, identity, index):
        if self.rendering:return
        track=self.session.tracks.get(identity)
        if not track:return
        if not track.bpm:
            from PySide6.QtWidgets import QInputDialog
            value,ok=QInputDialog.getDouble(self,"Source BPM","Enter the song's BPM to arrange it:",128,20,300,3)
            if not ok:return
            track.bpm=value; track.status="BPM entered manually"; self.touched.add(identity)
        clip=Clip(identity,track.cue_in,track.cue_out or track.duration)
        self.checkpoint(); self.session.clips.insert(index,clip)
        try:layout(self.session)
        except djmix.MixError as error:
            self.session.clips.remove(clip); self.history.pop(); self.show_error(str(error)); return
        self.selected_id=clip.id; self.changed(); self.timeline.fit()

    def select_clip(self, identity):
        self.selected_id=identity; self.refresh()

    def select_transition(self,index):
        if self.updating or index<0:return
        identity=self.transition_select.itemData(index)
        if identity:self.select_clip(identity)

    def move_clip(self, identity, index):
        if self.rendering:return
        original=next((i for i,c in enumerate(self.session.clips) if c.id==identity),None)
        if original is None:return
        self.checkpoint(); clip=self.session.clips.pop(original)
        if index>original:index-=1
        self.session.clips.insert(max(0,min(index,len(self.session.clips))),clip)
        try:layout(self.session)
        except djmix.MixError as error:self.undo();self.show_error(str(error));return
        self.selected_id=identity;self.changed()

    def nudge(self, amount):
        clip=self.selected_clip()
        if clip:
            index=self.session.clips.index(clip); self.move_clip(clip.id,index-1 if amount<0 else index+2)

    def remove_clip(self):
        clip=self.selected_clip()
        if not clip or self.rendering:return
        self.checkpoint();self.session.clips.remove(clip)
        try:layout(self.session)
        except djmix.MixError as error:self.undo();self.show_error(str(error));return
        self.selected_id=self.session.clips[-1].id if self.session.clips else "";self.changed()

    def undo(self):
        if not self.history or self.rendering:return
        previous=self.history.pop();self.session.clips=previous["clips"];self.session.target_bpm=previous["tempo"]
        for identity,values in previous["metadata"].items():
            if identity in self.session.tracks:
                track=self.session.tracks[identity]
                track.bpm,track.key,track.status,track.estimated=values
        if not self.selected_clip():self.selected_id=self.session.clips[-1].id if self.session.clips else ""
        self.populate_library();self.changed()

    def transpose_changed(self, enabled):
        if self.updating:return
        self.shift.setEnabled(enabled)
        clip=self.selected_clip()
        if not clip:return
        if enabled and self.shift.value()==0:
            index=self.session.clips.index(clip)
            if index:
                prev=self.session.clips[index-1]
                candidate=copy.copy(clip);candidate.transpose=False
                advice=blend_advice(self.session,prev,candidate)
                if advice["suggested_b_shifts"]:self.shift.setValue(advice["suggested_b_shifts"][0]["semitones"])
        self.apply_settings()

    def apply_settings(self):
        if self.updating or self.rendering:return
        clip=self.selected_clip()
        if not clip:return
        track=self.session.tracks[clip.track_id]
        old_clip=copy.deepcopy(clip);old_track=copy.deepcopy(track)
        self.checkpoint()
        # Do not round precise analyzed cues/BPMs merely by toggling another control.
        if self.bpm.value()!=round(track.bpm,self.bpm.decimals()):track.bpm=self.bpm.value()
        track.key="" if self.key.currentText()=="Unknown" else self.key.currentText()
        if self.cue_in.value()!=round(clip.cue_in,self.cue_in.decimals()):clip.cue_in=self.cue_in.value()
        if self.cue_out.value()!=round(clip.cue_out,self.cue_out.decimals()):clip.cue_out=self.cue_out.value()
        clip.blend_bars=self.bars.value()
        clip.transpose=self.transpose.isChecked();clip.semitones=self.shift.value();clip.bass_swap=self.bass.isChecked()
        clip.intro_percussive=self.intro_drums.isChecked();clip.outro_percussive=self.outro_drums.isChecked()
        try:layout(self.session)
        except djmix.MixError as error:
            self.session.tracks[track.id]=old_track;self.session.clips[self.session.clips.index(clip)]=old_clip;self.history.pop()
            self.refresh();self.show_error(str(error));return
        if track.bpm!=old_track.bpm or track.key!=old_track.key:
            track.status="BPM/key edited manually";track.estimated=False;self.touched.add(track.id)
        self.populate_library();self.changed();self.status.setText("Settings applied. Preview again to hear the change.")

    def change_tempo(self):
        if self.updating or self.rendering or self.tempo.value()==self.session.target_bpm:return
        self.checkpoint();self.session.target_bpm=self.tempo.value();self.changed()

    def zoom_changed(self,value):
        self.timeline.scale=.2*2**(value/10);self.timeline.refresh()

    def open_project(self):
        if self.jobs:return
        path,_=QFileDialog.getOpenFileName(self,"Open mix project",filter="DJ Studio project (*.djmix)")
        if path:
            try:self.load_project(Path(path))
            except Exception as error:self.show_error(str(error))

    def load_project(self,path):
        loaded=Session.from_dict(json.loads(path.read_text()))
        self.session=loaded;self.project_path=path;self.selected_id=loaded.clips[-1].id if loaded.clips else ""
        self.history=[];self.touched=set();self.player.stop();self.audio_revision=-1;self.revision+=1
        self.populate_library();self.refresh();QTimer.singleShot(100,self.timeline.fit)

    def save_project(self):
        path,_=QFileDialog.getSaveFileName(self,"Save mix project",str(self.project_path or (self.data/(self.session.name+".djmix"))),"DJ Studio project (*.djmix)")
        if path:
            try:
                self.project_path=Path(path);self.session.name=self.project_path.stem
                self.session.save(self.project_path);self.refresh();self.status.setText("Project saved: "+str(self.project_path))
            except Exception as error:self.show_error(str(error))

    def preview(self):
        if len(self.session.clips)<2 or self.rendering:return
        identity=self.transition_select.currentData()
        index=next((i for i,c in enumerate(self.session.clips) if c.id==identity),1)
        self.start_render(self.data/"renders"/f"preview-{time.time_ns()}.wav",preview_range(self.session,index),True)

    def render_full(self):
        if not self.rendering:self.start_render(self.data/"renders"/f"mix-{time.time_ns()}.wav",None,True)

    def export_mix(self):
        path,_=QFileDialog.getSaveFileName(self,"Export your mix",str(self.data/(self.session.name+".wav")),"WAV audio (*.wav)")
        if path:self.start_render(Path(path),None,False)

    def new_project(self):
        if self.jobs:return
        self.session.save(self.data/f"recovery-{time.time_ns()}.djmix")
        self.session=Session(tracks=self.session.tracks);self.project_path=None;self.selected_id="";self.history=[]
        self.changed();self.status.setText("New arrangement. The previous project is retained in local recovery files.")

    def propose_order(self):
        if self.rendering:return
        proposal=suggest_order(self.session)
        if proposal==self.session.clips:
            self.status.setText("The current order already matches this key/BPM suggestion.");return
        text="\n".join(f"{i+1}. {self.session.tracks[c.track_id].name}" for i,c in enumerate(proposal))
        answer=QMessageBox.question(self,"Suggested order","Keep the opening song, then prefer compatible keys and nearby BPMs. Pitch and cue points stay as chosen. Preview each blend.\n\n"+text+"\n\nApply this order?",QMessageBox.StandardButton.Apply|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer!=QMessageBox.StandardButton.Apply:return
        self.checkpoint();self.session.clips=proposal
        try:layout(self.session)
        except djmix.MixError as error:self.undo();self.show_error(str(error));return
        self.changed();self.timeline.fit()

    def start_render(self,destination,region,play):
        if self.rendering:return
        snapshot=copy.deepcopy(self.session);revision=self.revision;self.player.stop()
        self.player.setSource(QUrl())
        preview_label=("Transition " + self.transition_select.currentText().split(" · ")[0]) if region else "Full arrangement"
        def work(progress,emit):return render_session(snapshot,destination,region=region,progress=progress,cache_dir=self.data/"audio-cache")
        def finished(result):
            self.audio_origin=result["start"];self.audio_revision=revision
            self.player.setSource(QUrl.fromLocalFile(str(destination.resolve())))
            self.play_label.setText(preview_label)
            self.status.setText(f"{'Preview ready' if region else 'Mix exported'} · {clock(result['duration'])} · {result['reused_tracks']} reused / {result['processed_tracks']} processed · {result['true_peak_db']:.1f} dBTP")
            if play and revision==self.revision:self.player.play()
        self.job(work,finished,rendering=True)

    def toggle_play(self):
        self.refresh_audio_outputs()
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:self.player.pause()
        elif self.audio_revision==self.revision and not self.player.source().isEmpty():self.player.play()
        else:self.preview() if len(self.session.clips)>1 else self.render_full()

    def position_changed(self,value):
        self.time_label.setText(clock(value/1000));self.seek.setValue(int(value))
        self.timeline.playhead=self.audio_origin+value/1000;self.timeline.viewport().update()

    def playback_changed(self,state):
        self.play.setText("Ⅱ" if state==QMediaPlayer.PlaybackState.PlayingState else "▶")

    def seek_timeline(self,seconds):
        relative=seconds-self.audio_origin
        if self.audio_revision==self.revision and 0<=relative<=self.player.duration()/1000:
            self.player.setPosition(round(relative*1000))
        else:self.status.setText("Render the full arrangement to seek anywhere, or preview the selected blend.")

    def closeEvent(self,event):
        self.player.stop();self.session.save(self.data/"autosave.djmix")
        if self.jobs:
            self.cancel.set();self.closing=True;self.status.setText("Finishing the current audio step before closing…");event.ignore()
        else:event.accept()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library",type=Path)
    parser.add_argument("--project",type=Path)
    parser.add_argument("--data-dir",type=Path)
    args=parser.parse_args()
    app=QApplication(sys.argv[:1]);app.setApplicationName("DJ Studio");app.setOrganizationName("DJ Studio")
    app.setStyle("Fusion")
    font=QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont);font.setPointSize(11);app.setFont(font)
    if args.data_dir:data=args.data_dir
    else:
        from PySide6.QtCore import QStandardPaths
        data=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    window=Studio(data,args.project);window.show()
    if args.library:
        paths=sorted(p for p in args.library.iterdir() if p.suffix.lower() in FORMATS)
        QTimer.singleShot(250,lambda:window.import_paths(paths))
    return app.exec()


if __name__=="__main__":
    raise SystemExit(main())
