#!/usr/bin/env python3
"""DJ Studio: a native Qt desktop arrangement editor. No browser or web server."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, QRunnable, QSize, QStandardPaths, QThreadPool, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QFont, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMenu, QProgressBar, QPushButton, QSlider, QSplitter,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

import djmix
import studio_theme as T
from studio_engine import (Clip, Session, blend_advice, cache_track, estimate_track, layout, load_track,
                           preview_range, render_session, suggest_order)
from studio_waveform import BINS_PER_SECOND, band_envelope, level_envelope
from studio_widgets import (DARK_TEXT, BrowserPreview, ComboBox, CueEditor, ElidedLabel, Knob, LevelMeter,
                            LibraryModel, LibraryTable, Notice, Overview, Timeline, ValueBox, bar_position, camelot,
                            clock, display_key, parsed_key, toggle, tool_button)


ROOT = Path(__file__).resolve().parent
FORMATS = {".wav", ".mp3", ".flac", ".aif", ".aiff", ".m4a", ".ogg", ".aac"}
REVEAL = "Show in Finder" if sys.platform == "darwin" else "Show in folder"
FREE_KEY = "Option" if sys.platform == "darwin" else "Alt"
RELATIONS = {"same key": "Same key", "relative major/minor": "Relative major and minor",
             "neighboring keys on the circle of fifths": "Neighboring keys"}
SUGGESTION_REASONS = {"same key": "the same key as the previous song",
                      "relative major/minor": "the relative key of the previous song",
                      "neighboring keys on the circle of fifths": "a neighboring key to the previous song"}


def clock_ms(seconds):
    total = int(round(seconds * 1000))
    return f"{total // 60000}:{total % 60000 / 1000:06.3f}"


def parse_time(text):
    """Seconds from m:ss.mmm or plain seconds."""
    text = text.strip()
    if ":" in text:
        minutes, rest = text.split(":", 1)
        return int(minutes or 0) * 60 + float(rest or 0)
    return float(text)


def plain(text):
    """Engine messages use middle dots between parts; the interface uses commas."""
    return str(text).replace(" · ", ", ")


def label(text="", role=None, wrap=False):
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


def button(text, callback, role=None, icon=None):
    widget = QPushButton(text)
    if role:
        widget.setProperty("role", role)
    if icon:
        ink = {"primary": T.ON_ACCENT, "danger": T.BAD}.get(role, T.TEXT_2)
        widget.setIcon(T.icon(icon, ink, 14, ink if role in ("primary", "danger") else None))
        widget.setIconSize(QSize(14, 14))
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
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


class Studio(QMainWindow):
    def __init__(self, data_dir: Path, project: Path | None = None):
        super().__init__()
        self.data = data_dir
        self.data.mkdir(parents=True, exist_ok=True)
        self.session = Session()
        self.project_path = None
        self.selected_id = ""
        self.jobs = {}
        self.closing = False
        self.rendering = self.importing = self.updating = False
        self.history, self.future, self.touched = [], [], set()
        self.revision, self.audio_revision, self.audio_origin = 0, -1, 0.0
        self.audio_span, self.levels, self.loaded_label = None, None, ""
        self.dirty = False
        self.bands, self.band_pending, self.band_failed, self.band_workers = {}, set(), set(), set()
        self.band_cancel = threading.Event()
        self.comparison = None
        self.key_suggestion = None
        self.prefs = self.read_prefs()
        self.output_preference = self.prefs.get("audio_output", "system")
        self.setWindowTitle("DJ Studio")
        self.resize(1520, 940)
        self.setMinimumSize(1200, 760)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(self.prefs.get("volume", 70) / 100)
        self.media_devices = QMediaDevices(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(lambda _: self.sync_position())
        self.player.durationChanged.connect(lambda _: self.update_time())
        self.player.playbackStateChanged.connect(self.playback_changed)
        self.player.errorOccurred.connect(lambda *_: self.notify("Playback failed: " + self.player.errorString(), "error"))
        self.meter_timer = QTimer(self)
        self.meter_timer.setInterval(33)
        self.meter_timer.timeout.connect(self.tick)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(600)
        self.autosave_timer.timeout.connect(self.autosave)
        self.build_ui()
        self.build_menus()
        self.media_devices.audioOutputsChanged.connect(self.refresh_audio_outputs)
        self.refresh_audio_outputs()
        self.restore_session(project)
        self.refresh()
        QTimer.singleShot(0, self.timeline.fit)
        self.timeline.setFocus()
        self.load_bands()

    # Preferences and session restore -------------------------------------------------------

    def read_prefs(self):
        try:
            value = json.loads((self.data / "preferences.json").read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def write_prefs(self, **values):
        self.prefs.update(values)
        try:
            (self.data / "preferences.json").write_text(json.dumps(self.prefs))
        except OSError:
            pass

    def restore_session(self, project):
        if project:
            try:
                self.load_project(project)
            except Exception as error:
                self.notify(f"Could not open {project.name}: {error}", "error")
            return
        autosave = self.data / "autosave.djmix"
        if not autosave.exists():
            return
        try:
            self.load_project(autosave, remember=False)
        except Exception:
            kept = self.data / f"autosave-unreadable-{time.time_ns()}.djmix"
            try:
                autosave.rename(kept)
            except OSError:
                kept = autosave
            self.session = Session()
            self.notify(f"The last session could not be opened. It was kept as {kept.name}.", "error")
            return
        last = Path(self.prefs.get("last_project", "")) if self.prefs.get("last_project") else None
        if last and last.is_file() and last.stem == self.session.name:
            self.project_path = last
            try:
                saved = Session.from_dict(json.loads(last.read_text()))
                self.dirty = saved.to_dict() != self.session.to_dict()
            except Exception:
                self.dirty = True
        else:
            self.dirty = bool(self.session.clips)

    # Layout --------------------------------------------------------------------------------

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        column = QVBoxLayout(root)
        column.setContentsMargins(4, 4, 4, 0)
        column.setSpacing(4)
        column.addWidget(self.build_control_bar())
        work = QSplitter(Qt.Orientation.Vertical)
        work.setChildrenCollapsible(False)
        work.setHandleWidth(4)
        work.addWidget(self.build_arrangement())
        work.addWidget(self.build_detail())
        work.setStretchFactor(0, 1)
        work.setStretchFactor(1, 0)
        work.setSizes([640, 220])
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(4)
        body.addWidget(self.build_browser())
        body.addWidget(work)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([300, 1212])
        column.addWidget(body, 1)
        column.addWidget(self.build_status_bar())

    def build_control_bar(self):
        bar = QFrame()
        bar.setObjectName("controlBar")
        bar.setFixedHeight(40)
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(6)
        self.tempo = ValueBox(20, 300, step=1, fine=.01, decimals=2, width=74, height=26, font_size=15)
        self.tempo.setToolTip("Project tempo. Drag up or down (Shift for hundredths) or double-click to type. "
                              "Every song is stretched to it; keys stay as they are.")
        self.tempo.setAccessibleName("Project tempo in BPM")
        self.tempo.committed.connect(self.change_tempo)
        row.addWidget(self.tempo)
        row.addWidget(label("BPM", "small"))
        row.addSpacing(6)
        signature = label("4 / 4", "lcd")
        signature.setFont(T.font(15, QFont.Weight.Medium, tabular=True))
        signature.setToolTip("DJ Studio assumes 4/4 time")
        row.addWidget(signature)
        row.addStretch(1)
        self.rewind_btn = self.transport_button("rewind", "Back to the start (Home)", self.rewind)
        self.play_btn = self.transport_button("play", "Play (Space)", self.toggle_play)
        self.stop_btn = self.transport_button("stop", "Stop", self.stop)
        for widget in (self.rewind_btn, self.play_btn, self.stop_btn):
            row.addWidget(widget)
        row.addSpacing(8)
        self.position_label = label("1.1.1", "lcd")
        self.position_label.setFont(T.font(15, QFont.Weight.Medium, tabular=True))
        self.position_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.position_label.setMinimumWidth(86)
        self.position_label.setToolTip("Playhead position in bars, beats and sixteenths")
        self.time_label = label("0:00.0", "lcd")
        self.time_label.setFont(T.font(15, QFont.Weight.Medium, tabular=True))
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setMinimumWidth(78)
        self.time_label.setToolTip("Playhead position in minutes and seconds")
        row.addWidget(self.position_label)
        row.addWidget(self.time_label)
        row.addSpacing(8)
        self.source_label = ElidedLabel("Nothing rendered yet")
        self.source_label.setProperty("role", "small")
        self.source_label.setFixedWidth(210)
        row.addWidget(self.source_label)
        row.addStretch(1)
        self.meter = LevelMeter()
        row.addWidget(self.meter)
        row.addSpacing(6)
        self.output_button = tool_button("speaker", "Audio output", size=16)
        self.output_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.output_menu = QMenu(self)
        self.output_button.setMenu(self.output_menu)
        self.output_group = QActionGroup(self)
        self.output_group.setExclusive(True)
        row.addWidget(self.output_button)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(self.prefs.get("volume", 70)))
        self.volume.setFixedWidth(72)
        self.volume.setAccessibleName("Output volume")
        self.volume.setToolTip("Output volume")
        self.volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        row.addWidget(self.volume)
        row.addSpacing(8)
        self.export_btn = button("Export…", self.export_mix, None, "export")
        self.export_btn.setToolTip("Render the whole mix to a WAV file (Cmd+E)")
        row.addWidget(self.export_btn)
        return bar

    def transport_button(self, icon, tooltip, callback):
        widget = tool_button(icon, tooltip, callback, 14)
        widget.setProperty("role", "transport")
        widget.setFixedSize(32, 26)
        return widget

    def build_browser(self):
        pane = QFrame()
        pane.setObjectName("panel")
        pane.setMinimumWidth(250)
        column = QVBoxLayout(pane)
        column.setContentsMargins(6, 6, 6, 6)
        column.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 0, 0)
        header.addWidget(label("Library", "boxTitle"))
        header.addSpacing(4)
        self.library_count = label("", "small")
        header.addWidget(self.library_count)
        header.addStretch(1)
        column.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.addAction(T.icon("search", T.TEXT_3, 12), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh_library())
        column.addWidget(self.search)
        self.library_model = LibraryModel(self)
        self.library = LibraryTable(self.library_model)
        self.library.addRequested.connect(lambda identity: self.add_track(identity, len(self.session.clips)))
        self.library.filesDropped.connect(lambda paths: self.import_paths(self.audio_files([Path(p) for p in paths])))
        self.library.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library.customContextMenuRequested.connect(self.library_menu)
        self.library.selectionModel().currentChanged.connect(lambda *_: self.library_selected())
        column.addWidget(self.library, 1)
        self.preview_strip = BrowserPreview(self.bands)
        column.addWidget(self.preview_strip)
        tools = QHBoxLayout()
        tools.setSpacing(4)
        self.import_btn = button("Add folder…", self.import_folder, None, "folder")
        self.files_btn = button("Add files…", self.import_files, None, "plus")
        tools.addWidget(self.import_btn)
        tools.addWidget(self.files_btn)
        tools.addStretch(1)
        column.addLayout(tools)
        return pane

    def build_arrangement(self):
        pane = QFrame()
        pane.setObjectName("panel")
        column = QVBoxLayout(pane)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(8, 4, 6, 4)
        header.setSpacing(2)
        header.addWidget(label("Arrangement", "boxTitle"))
        header.addSpacing(8)
        self.arrangement_summary = label("", "small")
        header.addWidget(self.arrangement_summary)
        header.addStretch(1)
        self.order_btn = button("Suggest order", self.propose_order, "ghost", "order")
        header.addWidget(self.order_btn)
        header.addSpacing(6)
        self.timeline = Timeline(self.session, self.bands, self.meter)
        header.addWidget(tool_button("minus", "Zoom out (Cmd+-)", lambda: self.timeline.zoom(1 / 1.5)))
        header.addWidget(tool_button("plus", "Zoom in (Cmd+=)", lambda: self.timeline.zoom(1.5)))
        header.addWidget(tool_button("fit", "Fit the whole mix (Cmd+0)", self.timeline.fit))
        column.addLayout(header)
        self.notice = Notice()
        holder = QHBoxLayout()
        holder.setContentsMargins(6, 0, 6, 4)
        holder.addWidget(self.notice)
        column.addLayout(holder)
        self.overview = Overview(self.timeline)
        self.overview.navigate.connect(self.navigate)
        column.addWidget(self.overview)
        column.addSpacing(1)
        column.addWidget(self.timeline, 1)
        self.timeline.trackDropped.connect(self.add_track)
        self.timeline.clipMoved.connect(self.move_clip)
        self.timeline.selected.connect(self.select_clip)
        self.timeline.seekRequested.connect(self.seek_timeline)
        self.timeline.transitionSelected.connect(self.select_transition)
        return pane

    def box(self, title=None, width=None, name="box"):
        """A detail-view box: a title strip over a compact body, as in a DAW clip view."""
        frame = QFrame()
        frame.setObjectName("box")
        if width:
            frame.setFixedWidth(width)
        column = QVBoxLayout(frame)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        strip = QFrame()
        strip.setObjectName("boxTitle")
        strip.setFixedHeight(22)
        strip_row = QHBoxLayout(strip)
        strip_row.setContentsMargins(8, 0, 4, 0)
        strip_row.setSpacing(2)
        if title is not None:
            strip_row.addWidget(label(title, "boxTitle"))
            strip_row.addStretch(1)
        column.addWidget(strip)
        body = QVBoxLayout()
        body.setContentsMargins(8, 7, 8, 8)
        body.setSpacing(6)
        column.addLayout(body, 1)
        return frame, strip, strip_row, body

    def build_detail(self):
        detail = QFrame()
        detail.setObjectName("panel")
        detail.setMinimumHeight(196)
        row = QHBoxLayout(detail)
        row.setContentsMargins(6, 6, 6, 6)
        row.setSpacing(6)
        self.clip_stack = QStackedWidget()
        empty = QWidget()
        empty_column = QVBoxLayout(empty)
        empty_column.addStretch(1)
        hint = label("Select a clip in the arrangement to edit its start and end, tempo, key and pitch.", "secondary")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_column.addWidget(hint)
        empty_column.addStretch(1)
        self.clip_stack.addWidget(empty)
        self.clip_editor = QWidget()
        boxes = QHBoxLayout(self.clip_editor)
        boxes.setContentsMargins(0, 0, 0, 0)
        boxes.setSpacing(6)
        boxes.addWidget(self.build_clip_box())
        boxes.addWidget(self.build_sample_box())
        boxes.addWidget(self.build_display_box(), 1)
        self.clip_stack.addWidget(self.clip_editor)
        row.addWidget(self.clip_stack, 1)
        row.addWidget(self.build_transition_box())
        return detail

    def build_clip_box(self):
        frame, strip, strip_row, body = self.box(width=196)
        self.clip_strip = strip
        self.clip_name = ElidedLabel()
        self.clip_name.setFont(T.font(12, QFont.Weight.DemiBold))
        strip_row.addWidget(self.clip_name, 1)
        self.clip_state = label("", "hint", True)
        body.addWidget(self.clip_state)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        self.cue_in = ValueBox(0, 3600, step=self.bar_length, fine=self.beat_length, decimals=3, width=96,
                               text=lambda v: clock_ms(v), parse=parse_time)
        self.cue_out = ValueBox(0, 3600, step=self.bar_length, fine=self.beat_length, decimals=3, width=96,
                                text=lambda v: clock_ms(v), parse=parse_time)
        for widget, name in ((self.cue_in, "start"), (self.cue_out, "end")):
            widget.setToolTip(f"Clip {name} in the original song. Drag to move by bars, Shift for beats, "
                              "or double-click to type a time.")
        self.cue_in.committed.connect(lambda value: self.set_clip_value("cue_in", value))
        self.cue_out.committed.connect(lambda value: self.set_clip_value("cue_out", value))
        grid.addWidget(label("Start", "field"), 0, 0)
        grid.addWidget(self.cue_in, 0, 1)
        grid.addWidget(label("End", "field"), 1, 0)
        grid.addWidget(self.cue_out, 1, 1)
        body.addLayout(grid)
        body.addStretch(1)
        moves = QHBoxLayout()
        moves.setSpacing(2)
        self.earlier = tool_button("arrow-left", "Move earlier in the mix (Cmd+Left)", lambda: self.nudge(-1))
        self.later = tool_button("arrow-right", "Move later in the mix (Cmd+Right)", lambda: self.nudge(1))
        self.remove_btn = button("Remove", self.remove_clip, "danger", "trash")
        self.remove_btn.setToolTip("Remove this clip from the mix. The song stays in your library. (Delete)")
        moves.addWidget(self.earlier)
        moves.addWidget(self.later)
        moves.addStretch(1)
        moves.addWidget(self.remove_btn)
        body.addLayout(moves)
        return frame

    def build_sample_box(self):
        frame, strip, strip_row, body = self.box("Sample", width=236)
        tempo = QHBoxLayout()
        tempo.setSpacing(6)
        tempo.addWidget(label("BPM", "field"))
        self.bpm = ValueBox(20, 300, step=.1, fine=.001, decimals=3, width=78)
        self.bpm.setToolTip("The song's own tempo. Drag (Shift for fine steps) or double-click to type.")
        self.bpm.committed.connect(self.set_bpm)
        tempo.addWidget(self.bpm)
        self.use_bpm_btn = button("Use suggested", self.use_timing_bpm, "ghost")
        tempo.addWidget(self.use_bpm_btn)
        tempo.addStretch(1)
        body.addLayout(tempo)
        key = QHBoxLayout()
        key.setSpacing(6)
        key.addWidget(label("Key", "field"))
        self.key = ComboBox()
        self.key.addItem("Unknown", "")
        keys = [(code, f"{note} {mode}") for mode in ("minor", "major") for note in djmix.NOTES
                for code in [camelot(f"{note} {mode}")]]
        for code, name in sorted(keys, key=lambda item: (item[0][-1], int(item[0][:-1]))):
            self.key.addItem(f"{code}  {display_key(name)}", name)
        self.key.currentIndexChanged.connect(lambda _: self.set_key())
        key.addWidget(self.key, 1)
        body.addLayout(key)
        pitch = QHBoxLayout()
        pitch.setSpacing(8)
        self.transpose_knob = Knob()
        self.transpose_knob.setToolTip("Transpose in semitones. Drag up or down; double-click for the original key.")
        self.transpose_knob.committed.connect(self.set_transpose)
        pitch.addWidget(self.transpose_knob)
        advice = QVBoxLayout()
        advice.setSpacing(3)
        self.match_btn = button("Match key", self.match_key)
        self.match_btn.setToolTip("Transpose to the suggested key for a smoother blend with the previous song")
        advice.addWidget(self.match_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.shift_hint = label("", "hint", True)
        advice.addWidget(self.shift_hint)
        pitch.addLayout(advice, 1)
        body.addLayout(pitch)
        warp = QHBoxLayout()
        warp.setSpacing(4)
        self.stabilize = toggle("Stabilize", "Gentle tempo correction in confidently detected passages. "
                                             "Cached for previews and exports.")
        self.stabilize.clicked.connect(self.set_stabilize)
        self.timing_btn = button("Analyze", self.analyze_timing)
        self.timing_btn.setToolTip("Check whether the tempo drifts across the whole song")
        self.compare_timing_btn = button("A/B", self.compare_timing)
        self.compare_timing_btn.setToolTip("Compare fixed and stabilized tempo while it plays")
        warp.addWidget(self.stabilize)
        warp.addWidget(self.timing_btn)
        warp.addWidget(self.compare_timing_btn)
        warp.addStretch(1)
        body.addLayout(warp)
        self.timing_hint = label("", "hint", True)
        body.addWidget(self.timing_hint)
        body.addStretch(1)
        return frame

    def build_display_box(self):
        frame, strip, strip_row, body = self.box()
        self.sample_name = ElidedLabel()
        self.sample_name.setProperty("role", "boxTitle")
        strip_row.addWidget(self.sample_name, 1)
        body.setContentsMargins(0, 0, 0, 0)
        self.cue_editor = CueEditor(self.bands)
        self.cue_editor.setToolTip(f"Drag the Start and End markers. They snap to bars; hold Shift for beats "
                                   f"or {FREE_KEY} to place them freely.")
        self.cue_editor.cuesChanged.connect(self.set_cues)
        body.addWidget(self.cue_editor, 1)
        return frame

    def build_transition_box(self):
        frame, strip, strip_row, body = self.box(width=280)
        self.transition_title = label("Transition", "boxTitle")
        strip_row.addWidget(self.transition_title)
        strip_row.addStretch(1)
        self.prev_transition = tool_button("chevron-left", "Previous transition ([)", lambda: self.step_transition(-1), 12)
        self.next_transition = tool_button("chevron-right", "Next transition (])", lambda: self.step_transition(1), 12)
        strip_row.addWidget(self.prev_transition)
        strip_row.addWidget(self.next_transition)
        self.transition_details = QWidget()
        details = QVBoxLayout(self.transition_details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(6)
        names = QGridLayout()
        names.setHorizontalSpacing(8)
        names.setVerticalSpacing(2)
        self.from_name, self.to_name = ElidedLabel(), ElidedLabel()
        self.from_key, self.to_key = label("", "small"), label("", "small")
        names.addWidget(label("Out", "field"), 0, 0)
        names.addWidget(self.from_name, 0, 1)
        names.addWidget(self.from_key, 0, 2)
        names.addWidget(label("In", "field"), 1, 0)
        names.addWidget(self.to_name, 1, 1)
        names.addWidget(self.to_key, 1, 2)
        names.setColumnStretch(1, 1)
        details.addLayout(names)
        self.verdict = label("", "small")
        self.verdict.setTextFormat(Qt.TextFormat.RichText)
        details.addWidget(self.verdict)
        blend = QHBoxLayout()
        blend.setSpacing(6)
        blend.addWidget(label("Blend", "field"))
        self.blend_bars = ValueBox(1, 32, step=1, suffix=" bars", width=70)
        self.blend_bars.setToolTip("Overlap length in bars. Drag up or down, or double-click to type.")
        self.blend_bars.committed.connect(lambda value: self.set_transition_value("in", "blend_bars", int(value)))
        blend.addWidget(self.blend_bars)
        blend.addStretch(1)
        self.bass = toggle("Bass swap", "Hand the low end over in one bar at the middle of the blend instead of crossfading it")
        self.bass.clicked.connect(lambda checked: self.set_transition_value("in", "bass_swap", checked))
        blend.addWidget(self.bass)
        details.addLayout(blend)
        drums = QHBoxLayout()
        drums.setSpacing(4)
        explain = ("Turn on only if the whole overlap has no pitched bass, vocals, melody or tonal tails. "
                   "This changes the key advice, not the audio.")
        self.outro_drums = toggle("Drum outro", "The outgoing song's overlap is beats only. " + explain)
        self.intro_drums = toggle("Drum intro", "The incoming song's overlap is beats only. " + explain)
        self.outro_drums.clicked.connect(lambda checked: self.set_transition_value("out", "outro_percussive", checked))
        self.intro_drums.clicked.connect(lambda checked: self.set_transition_value("in", "intro_percussive", checked))
        drums.addWidget(self.outro_drums)
        drums.addWidget(self.intro_drums)
        drums.addStretch(1)
        details.addLayout(drums)
        body.addWidget(self.transition_details)
        self.transition_empty = label("Add a second song to the arrangement to create a transition.", "hint", True)
        body.addWidget(self.transition_empty)
        body.addStretch(1)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        self.preview_btn = button("Preview", self.preview, "primary", "play")
        self.preview_btn.setToolTip("Render this blend with four bars either side, then play it (P)")
        self.render_btn = button("Render mix", self.render_full)
        self.render_btn.setToolTip("Render the whole arrangement so you can play and seek anywhere (Cmd+R)")
        actions.addWidget(self.preview_btn)
        actions.addWidget(self.render_btn)
        actions.addStretch(1)
        body.addLayout(actions)
        return frame

    def build_status_bar(self):
        bar = QFrame()
        bar.setObjectName("statusBar")
        bar.setFixedHeight(24)
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 0, 4, 0)
        row.setSpacing(8)
        self.status = ElidedLabel("Ready")
        self.status.setProperty("role", "small")
        row.addWidget(self.status, 1)
        self.job_area = QWidget()
        job = QHBoxLayout(self.job_area)
        job.setContentsMargins(0, 0, 0, 0)
        job.setSpacing(8)
        self.job_text = ElidedLabel("")
        self.job_text.setProperty("role", "small")
        self.job_text.setFixedWidth(300)
        self.job_text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.job_bar = QProgressBar()
        self.job_bar.setRange(0, 0)
        self.job_bar.setFixedWidth(110)
        self.job_bar.setTextVisible(False)
        self.cancel_btn = button("Cancel", self.cancel_job, "ghost")
        self.cancel_btn.setToolTip("Stop after the current processing step")
        job.addWidget(self.job_text)
        job.addWidget(self.job_bar)
        job.addWidget(self.cancel_btn)
        self.job_area.hide()
        row.addWidget(self.job_area)
        return bar

    def build_menus(self):
        bar = self.menuBar()
        standard = QKeySequence.StandardKey

        def add(menu, text, callback, *shortcuts):
            action = QAction(text, self)
            action.setShortcuts([QKeySequence(s) for s in shortcuts])
            action.triggered.connect(lambda checked=False: callback())
            menu.addAction(action)
            self.addAction(action)
            return action

        file = bar.addMenu("File")
        self.act_new = add(file, "New mix", self.new_project, standard.New)
        self.act_open = add(file, "Open…", self.open_project, standard.Open)
        file.addSeparator()
        self.act_save = add(file, "Save", self.save_project, standard.Save)
        self.act_save_as = add(file, "Save as…", self.save_project_as, standard.SaveAs)
        file.addSeparator()
        self.act_import = add(file, "Import folder…", self.import_folder, "Ctrl+I")
        self.act_files = add(file, "Add files…", self.import_files, "Ctrl+Shift+I")
        file.addSeparator()
        self.act_export = add(file, "Export mix…", self.export_mix, "Ctrl+E")
        file.addSeparator()
        add(file, "Quit", self.close, standard.Quit).setMenuRole(QAction.MenuRole.QuitRole)

        edit = bar.addMenu("Edit")
        self.act_undo = add(edit, "Undo", self.undo, standard.Undo)
        self.act_redo = add(edit, "Redo", self.redo, standard.Redo)
        edit.addSeparator()
        self.act_remove = add(edit, "Remove from mix", self.remove_clip, "Delete", "Backspace")
        self.act_earlier = add(edit, "Move earlier", lambda: self.nudge(-1), "Ctrl+Left")
        self.act_later = add(edit, "Move later", lambda: self.nudge(1), "Ctrl+Right")
        edit.addSeparator()
        self.act_order = add(edit, "Suggest order", self.propose_order)

        playback = bar.addMenu("Playback")
        self.act_play = add(playback, "Play or pause", self.toggle_play, "Space")
        add(playback, "Stop", self.stop, "Ctrl+.")
        add(playback, "Back to start", self.rewind, "Home")
        playback.addSeparator()
        self.act_preview = add(playback, "Preview transition", self.preview, "P")
        self.act_render = add(playback, "Render full mix", self.render_full, "Ctrl+R")
        playback.addSeparator()
        self.act_prev = add(playback, "Previous transition", lambda: self.step_transition(-1), "[")
        self.act_next = add(playback, "Next transition", lambda: self.step_transition(1), "]")

        view = bar.addMenu("View")
        add(view, "Zoom in", lambda: self.timeline.zoom(1.5), standard.ZoomIn, "Ctrl+=")
        add(view, "Zoom out", lambda: self.timeline.zoom(1 / 1.5), standard.ZoomOut)
        add(view, "Fit the whole mix", self.timeline.fit, "Ctrl+0")

    # State ---------------------------------------------------------------------------------

    def selected_clip(self):
        return next((clip for clip in self.session.clips if clip.id == self.selected_id), None)

    def selected_index(self):
        return next((i for i, clip in enumerate(self.session.clips) if clip.id == self.selected_id), -1)

    def transition_index(self):
        if len(self.session.clips) < 2:
            return 0
        return max(1, self.selected_index())

    def bar_length(self):
        clip = self.selected_clip()
        track = self.session.tracks.get(clip.track_id) if clip else None
        return 240 / track.bpm if track and track.bpm else 1.0

    def beat_length(self):
        return self.bar_length() / 4

    def snapshot(self):
        return {"clips": copy.deepcopy(self.session.clips), "tempo": self.session.target_bpm,
                "metadata": {k: (t.bpm, t.key, t.status, t.estimated, copy.deepcopy(t.timing))
                             for k, t in self.session.tracks.items()}}

    def restore(self, state):
        self.session.clips = state["clips"]
        self.session.target_bpm = state["tempo"]
        for identity, values in state["metadata"].items():
            if identity in self.session.tracks:
                track = self.session.tracks[identity]
                track.bpm, track.key, track.status, track.estimated, track.timing = values

    def edit(self, mutate, force=False):
        """Apply one undoable change. Invalid results are rolled back and explained inline."""
        if self.updating or (self.rendering and not force):
            return False
        state = self.snapshot()
        try:
            mutate()
            layout(self.session)
        except djmix.MixError as error:
            self.restore(state)
            self.refresh()
            self.notify(str(error), "error")
            return False
        self.history.append(state)
        self.history = self.history[-50:]
        self.future.clear()
        self.changed()
        return True

    def undo(self):
        if not self.history or self.rendering:
            return
        self.future.append(self.snapshot())
        self.restore(self.history.pop())
        self.after_history("Undone.")

    def redo(self):
        if not self.future or self.rendering:
            return
        self.history.append(self.snapshot())
        self.restore(self.future.pop())
        self.after_history("Redone.")

    def after_history(self, message):
        if not self.selected_clip():
            self.selected_id = self.session.clips[-1].id if self.session.clips else ""
        self.changed()
        self.say(message)

    def changed(self):
        self.revision += 1
        self.player.pause()
        self.dirty = True
        self.autosave()
        self.refresh()

    def autosave(self):
        self.autosave_timer.stop()
        try:
            self.session.save(self.data / "autosave.djmix")
        except OSError as error:
            self.notify(f"Autosave failed: {error}", "error")

    def rendered_range(self):
        current = self.audio_revision == self.revision and not self.player.source().isEmpty()
        return self.audio_span if current else None

    def harmony(self, index):
        outgoing, incoming = self.session.clips[index - 1], self.session.clips[index]
        if outgoing.outro_percussive or incoming.intro_percussive:
            return "good", "Beats-only overlap"
        a = djmix.transpose(parsed_key(self.session.tracks[outgoing.track_id].key), outgoing.shift)
        b = djmix.transpose(parsed_key(self.session.tracks[incoming.track_id].key), incoming.shift)
        if not a or not b:
            return "unknown", "Key unknown"
        relation = djmix.relationship(a, b)
        if relation:
            return "good", RELATIONS.get(relation, relation.capitalize())
        suggestions = blend_advice(self.session, outgoing, incoming)["suggested_b_shifts"]
        if suggestions:
            return "bad", f"Keys clash. Try {suggestions[0]['semitones']:+d} on the incoming song"
        return "bad", "Keys clash"

    # Refresh -------------------------------------------------------------------------------

    def mix_audio(self):
        if not self.audio_span or self.player.source().isEmpty():
            return None
        return (*self.audio_span, self.levels, self.audio_revision == self.revision)

    def refresh(self):
        self.updating = True
        try:
            clips = self.session.clips
            self.timeline.session = self.session
            self.timeline.selection = self.selected_id
            self.timeline.transition = self.transition_index()
            self.timeline.harmony = {i: self.harmony(i)[0] for i in range(1, len(clips))}
            self.timeline.mix_audio = self.mix_audio()
            self.timeline.refresh()
            self.overview.invalidate()
            self.refresh_header()
            self.refresh_library()
            self.refresh_transition()
            self.refresh_inspector()
            self.refresh_actions()
            self.update_time()
        finally:
            self.updating = False

    def refresh_header(self):
        name = self.session.name or "Untitled mix"
        state = "" if not self.dirty else " (edited)" if self.project_path else " (not saved)"
        self.setWindowTitle(f"{name}[*]")
        self.setWindowModified(self.dirty)
        self.setWindowFilePath(str(self.project_path) if self.project_path else "")
        if self.tempo.editor is None:
            self.tempo.setValue(self.session.target_bpm)
        duration = self.timeline.duration()
        count = len(self.session.clips)
        summary = f"{count} {'song' if count == 1 else 'songs'}, {clock(duration)}" if count else "Empty"
        self.arrangement_summary.setText(f"{name}{state}   {summary}")

    def refresh_library(self):
        model = self.library_model
        current = self.library.current_id()
        scroll = self.library.verticalScrollBar().value()
        model.session = self.session
        model.query = self.search.text()
        clip = self.selected_clip()
        if clip:
            model.context = djmix.transpose(parsed_key(self.session.tracks[clip.track_id].key), clip.shift)
            model.context_track = clip.track_id
        else:
            model.context = model.context_track = None
        model.rebuild()
        if current:
            self.library.select_id(current)
        self.library.verticalScrollBar().setValue(scroll)
        count = len(self.session.tracks)
        self.library_count.setText(f"{count} {'song' if count == 1 else 'songs'}" if count else "")
        self.preview_strip.set_track(self.session.tracks.get(self.library.current_id() or ""))

    def library_selected(self):
        self.preview_strip.set_track(self.session.tracks.get(self.library.current_id() or ""))
        self.refresh_actions()

    def refresh_transition(self):
        clips = self.session.clips
        index = self.transition_index()
        has = index > 0
        self.transition_details.setVisible(has)
        self.transition_empty.setVisible(not has)
        self.prev_transition.setEnabled(has and index > 1)
        self.next_transition.setEnabled(has and index < len(clips) - 1)
        self.transition_details.setEnabled(not self.rendering)
        if not has:
            self.transition_title.setText("Transition")
            return
        self.transition_title.setText(f"Transition {index} → {index + 1}")
        outgoing, incoming = clips[index - 1], clips[index]
        a, b = self.session.tracks[outgoing.track_id], self.session.tracks[incoming.track_id]
        self.from_name.set_text(a.name)
        self.to_name.set_text(b.name)
        for widget, track, clip in ((self.from_key, a, outgoing), (self.to_key, b, incoming)):
            key = djmix.transpose(parsed_key(track.key), clip.shift)
            text = f"{camelot(key)} {display_key(key, True)}" if key else "Key?"
            widget.setText(text + (f" ({clip.shift:+d})" if clip.shift else ""))
        state, text = self.harmony(index)
        dot = {"good": T.GOOD, "bad": T.BAD}.get(state, T.TEXT_3)
        self.verdict.setText(f'<span style="color:{dot}">●</span>&nbsp; {text}')
        self.blend_bars.setValue(incoming.blend_bars)
        self.bass.setChecked(incoming.bass_swap)
        self.outro_drums.setChecked(outgoing.outro_percussive)
        self.intro_drums.setChecked(incoming.intro_percussive)

    def refresh_inspector(self):
        clip = self.selected_clip()
        if not clip:
            self.clip_stack.setCurrentIndex(0)
            return
        self.clip_stack.setCurrentIndex(1)
        self.clip_editor.setEnabled(not self.rendering)
        clips = self.session.clips
        index = clips.index(clip)
        track = self.session.tracks[clip.track_id]
        color = T.clip_color(track.id)
        self.clip_strip.setStyleSheet(f"QFrame#boxTitle {{ background: {color}; }}")
        self.clip_name.setStyleSheet(f"color: {DARK_TEXT};")
        self.clip_name.set_text(track.name)
        self.clip_state.setText(f"Song {index + 1} of {len(clips)}. {plain(track.status)}.".replace("..", "."))
        following = clips[index + 1].blend_bars if index + 1 < len(clips) else 0
        self.cue_editor.set_clip(track, clip, clip.blend_bars if index else 0, following)
        self.sample_name.set_text(Path(track.path).name)
        for widget, value in ((self.cue_in, clip.cue_in), (self.cue_out, clip.cue_out)):
            widget.setMaximum(track.duration)
            widget.setValue(value)
        self.bpm.setValue(track.bpm or 128)
        self.key.setCurrentIndex(max(0, self.key.findData(track.key or "")))
        self.transpose_knob.setValue(clip.shift)
        self.key_suggestion = None
        if index:
            candidate = copy.copy(clip)
            candidate.transpose = False
            shifts = blend_advice(self.session, clips[index - 1], candidate)["suggested_b_shifts"]
            if shifts and shifts[0]["semitones"]:
                self.key_suggestion = shifts[0]
        processed = djmix.transpose(parsed_key(track.key), clip.shift)
        suggestion = self.key_suggestion
        if suggestion and suggestion["semitones"] == clip.shift:
            text = f"In {display_key(processed)}, {SUGGESTION_REASONS.get(suggestion['reason'], suggestion['reason'])}."
        elif suggestion:
            text = (f"{suggestion['semitones']:+d} gives {display_key(suggestion['resulting_key'])}, "
                    f"{SUGGESTION_REASONS.get(suggestion['reason'], suggestion['reason'])}.")
        elif clip.shift and processed:
            text = f"Plays in {display_key(processed)}."
        else:
            text = "Original key."
        self.shift_hint.setText(text)
        self.match_btn.setEnabled(bool(suggestion) and suggestion["semitones"] != clip.shift)
        timing = track.timing
        state, bpm_suggestion = timing.get("state"), timing.get("bpm")
        idle = not self.jobs
        self.stabilize.setChecked(clip.stabilize)
        self.stabilize.setEnabled((state == "drift" or clip.stabilize) and idle)
        self.timing_btn.setEnabled(idle)
        self.compare_timing_btn.setEnabled(state == "drift" and idle)
        show_bpm = bpm_suggestion is not None and abs(bpm_suggestion - (track.bpm or 0)) > 1e-6
        self.use_bpm_btn.setVisible(show_bpm)
        self.use_bpm_btn.setEnabled(idle)
        if show_bpm:
            self.use_bpm_btn.setText(f"Use {bpm_suggestion:.3f}")
            self.use_bpm_btn.setToolTip("Apply the BPM measured across the whole song. Your cues stay as they are.")
        if state == "drift":
            self.timing_hint.setText(f"Tempo drifts. Stabilize covers {timing['coverage']:.0%} of the song; "
                                     "compare A and B first.")
        elif state == "steady":
            self.timing_hint.setText("Tempo is steady. No correction needed.")
        elif state == "uncertain":
            self.timing_hint.setText("Not enough reliable beats to measure. Check the BPM by ear.")
        else:
            self.timing_hint.setText("Analyze to check for tempo drift across the song.")
        self.earlier.setEnabled(index > 0)
        self.later.setEnabled(index < len(clips) - 1)

    def refresh_actions(self):
        clips = len(self.session.clips)
        busy, rendering = bool(self.jobs), self.rendering
        clip = self.selected_clip()
        index = self.selected_index()
        for widget in (self.export_btn, self.render_btn, self.act_export, self.act_render):
            widget.setEnabled(clips > 0 and not rendering)
        for widget in (self.preview_btn, self.act_preview):
            widget.setEnabled(clips > 1 and not rendering)
        for widget in (self.import_btn, self.files_btn, self.act_import, self.act_files):
            widget.setEnabled(not self.importing and not rendering)
        for widget in (self.act_open, self.act_new):
            widget.setEnabled(not busy)
        self.tempo.setEnabled(not rendering)
        self.order_btn.setEnabled(clips > 2 and not rendering)
        self.act_order.setEnabled(clips > 2 and not rendering)
        self.act_undo.setEnabled(bool(self.history) and not rendering)
        self.act_redo.setEnabled(bool(self.future) and not rendering)
        self.act_remove.setEnabled(clip is not None and not rendering)
        self.act_earlier.setEnabled(index > 0 and not rendering)
        self.act_later.setEnabled(0 <= index < clips - 1 and not rendering)
        self.act_prev.setEnabled(self.transition_index() > 1)
        self.act_next.setEnabled(0 < self.transition_index() < clips - 1)
        self.timeline.setEnabled(not rendering)
        self.play_btn.setEnabled(clips > 0)
        self.update_play_icon()

    # Feedback ------------------------------------------------------------------------------

    def say(self, text):
        self.status.set_text(plain(text))

    def notify(self, text, tone="info", actions=()):
        if self.closing:
            self.say(text)
            return
        self.notice.show_message(plain(text), tone, actions)
        if tone == "info":
            self.say(text)

    # Audio output --------------------------------------------------------------------------

    def refresh_audio_outputs(self):
        devices = QMediaDevices.audioOutputs()
        default = QMediaDevices.defaultAudioOutput()
        entries = [(f"System output ({default.description()})" if default.description() else "System output", "system")]
        entries += [(device.description(), bytes(device.id()).hex()) for device in devices]
        if self.output_preference not in [value for _, value in entries]:
            # A disconnected, explicitly selected headset must not silently send
            # playback to speakers. Remember it so reconnecting restores the route.
            entries.append(("Selected output (disconnected)", self.output_preference))
        self.output_menu.clear()
        for action in self.output_group.actions():
            self.output_group.removeAction(action)
        for index, (text, value) in enumerate(entries):
            action = self.output_menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(value == self.output_preference)
            action.triggered.connect(lambda checked=False, v=value: self.change_audio_output(v))
            self.output_group.addAction(action)
            if index == 0 and len(entries) > 1:
                self.output_menu.addSeparator()
        chosen = default if self.output_preference == "system" else next(
            (d for d in devices if bytes(d.id()).hex() == self.output_preference), None)
        if chosen is None or chosen.isNull():
            self.player.pause()
            self.audio.setMuted(True)
            self.output_button.setIcon(T.icon("speaker", T.BAD, 16))
            self.output_button.setToolTip("Audio output unavailable")
            self.say("Audio output unavailable. Connect it or choose another output.")
            return
        self.audio.setMuted(False)
        if self.audio.device().id() != chosen.id():
            self.audio.setDevice(chosen)
        self.output_button.setIcon(T.icon("speaker", T.TEXT_2, 16))
        self.output_button.setToolTip("Playing through " + chosen.description())

    def change_audio_output(self, value):
        self.output_preference = value
        self.write_prefs(audio_output=value)
        self.refresh_audio_outputs()
        self.say(self.output_button.toolTip())

    # Background work -----------------------------------------------------------------------

    def job(self, function, finished, *, importing=False, rendering=False, cancellable=False, item=None):
        cancel = threading.Event()
        worker = Worker(function, cancel)
        self.jobs[worker] = (cancel, importing, rendering, cancellable)
        self.update_job_flags()

        def settle():
            if self.jobs.pop(worker, None) is not None:
                self.update_job_flags()

        def deliver(result):
            settle()
            finished(result)

        def failed(message):
            settle()
            if message == "Operation cancelled.":
                self.say("Stopped.")
            else:
                self.notify(message, "error")

        def done():
            settle()
            self.refresh()
            if not self.jobs and self.closing:
                self.close()

        worker.signals.progress.connect(lambda message: self.job_text.set_text(plain(message)))
        worker.signals.item.connect(item or self.receive_track)
        worker.signals.result.connect(deliver)
        worker.signals.error.connect(failed)
        worker.signals.finished.connect(done)
        self.job_text.set_text("Working")
        self.refresh()
        self.pool.start(worker)

    def update_job_flags(self):
        jobs = list(self.jobs.values())
        self.importing = any(j[1] for j in jobs)
        self.rendering = any(j[2] for j in jobs)
        self.job_area.setVisible(bool(jobs))
        self.cancel_btn.setVisible(any(j[3] for j in jobs))

    def cancel_job(self):
        for cancel, _, _, cancellable in self.jobs.values():
            if cancellable:
                cancel.set()
        self.job_text.set_text("Stopping after the current step")

    def load_bands(self):
        order = [c.track_id for c in self.session.clips] + list(self.session.tracks)
        wanted = []
        for identity in dict.fromkeys(order):
            track = self.session.tracks.get(identity)
            if (track and identity not in self.bands and identity not in self.band_pending
                    and identity not in self.band_failed and Path(track.path).is_file()):
                wanted.append((identity, track.path))
        if not wanted:
            return
        self.band_pending.update(identity for identity, _ in wanted)
        cache, cancel = self.data / "library-cache", self.band_cancel

        def work(progress, emit):
            for identity, path in wanted:
                if cancel.is_set():
                    return
                try:
                    emit((identity, band_envelope(Path(path), cache, identity)))
                except Exception:
                    emit((identity, None))

        worker = Worker(work, cancel)
        self.band_workers.add(worker)
        worker.signals.item.connect(self.receive_bands)
        worker.signals.finished.connect(lambda: self.band_workers.discard(worker))
        self.pool.start(worker)

    def receive_bands(self, payload):
        identity, bands = payload
        self.band_pending.discard(identity)
        if bands is None or not len(bands):
            self.band_failed.add(identity)
            return
        self.bands[identity] = bands
        self.timeline.invalidate()
        self.overview.invalidate()
        self.cue_editor.update()
        self.preview_strip.update()

    # Library -------------------------------------------------------------------------------

    def receive_track(self, track):
        if track.id in self.touched and track.id in self.session.tracks:
            self.session.tracks[track.id].waveform = track.waveform
        else:
            self.session.tracks[track.id] = track
        self.autosave_timer.start()
        self.refresh()

    @staticmethod
    def audio_files(paths):
        found = []
        for path in paths:
            if path.is_dir():
                found += sorted(p for p in path.rglob("*")
                                if p.is_file() and p.suffix.lower() in FORMATS and not p.name.startswith("."))
            elif path.suffix.lower() in FORMATS:
                found.append(path)
        return found

    def import_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Import a music folder")
        if folder:
            self.import_paths(self.audio_files([Path(folder)]))

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add songs",
                                                filter="Audio (*.wav *.mp3 *.flac *.aif *.aiff *.m4a *.ogg *.aac)")
        if paths:
            self.import_paths([Path(p) for p in paths])

    def import_paths(self, paths):
        if not paths:
            self.notify("No audio files found there. DJ Studio reads WAV, AIFF, FLAC, MP3, M4A, AAC and OGG.", "error")
            return
        if self.importing or self.rendering:
            self.say("Wait for the current import or render to finish.")
            return
        existing = copy.deepcopy(self.session.tracks)
        cache, timing_cache = self.data / "library-cache", self.data / "timing-cache"

        def work(progress, emit):
            pending, failures = [], []
            for path in paths:
                progress(f"Importing {path.name}")
                try:
                    track = load_track(path, cache)
                    if track.id in existing and existing[track.id].bpm:
                        prior = existing[track.id]
                        prior.waveform = track.waveform
                        track = prior
                    emit(copy.deepcopy(track))
                    if not track.bpm:
                        pending.append(track)
                    else:
                        cache_track(track, cache)
                except Exception as error:
                    failures.append(f"{path.name}: {error}")
            for index, track in enumerate(pending):
                progress(f"Estimating BPM and key for {track.name} ({index + 1} of {len(pending)})")
                try:
                    track = estimate_track(track, timing_cache, progress)
                    cache_track(track, cache)
                except Exception as error:
                    track.status = f"Needs manual analysis: {error}"
                emit(copy.deepcopy(track))
            return len(paths) - len(failures), failures

        def finished(result):
            imported, failures = result
            if failures:
                self.notify("Some files could not be imported. " + "; ".join(failures), "error")
            else:
                self.say(f"Imported {imported} {'song' if imported == 1 else 'songs'}. Drag them onto the timeline.")
            self.load_bands()

        self.job(work, finished, importing=True)

    def analyze_selected(self):
        identity = self.library.current_id()
        if not identity:
            return
        if any(c.track_id == identity for c in self.session.clips):
            self.say("This song is in the mix. Edit its BPM and key in the clip panel to keep your cue choices.")
            return
        track = copy.deepcopy(self.session.tracks[identity])

        def work(progress, emit):
            progress("Analyzing " + track.name)
            result = estimate_track(track, self.data / "timing-cache", progress)
            cache_track(result, self.data / "library-cache")
            emit(result)

        self.job(work, lambda _: self.say("Analysis complete. The values stay editable."), importing=True)

    def library_menu(self, point):
        index = self.library.indexAt(point)
        if not index.isValid():
            return
        self.library.setCurrentIndex(index)
        identity = self.library.current_id()
        track = self.session.tracks[identity]
        arranged = any(c.track_id == identity for c in self.session.clips)
        menu = QMenu(self)
        add = menu.addAction("Add to the end of the mix")
        add.setEnabled(not self.rendering)
        analyze = menu.addAction("Analyze BPM and key again")
        analyze.setEnabled(not arranged and not self.importing and not self.rendering)
        menu.addSeparator()
        reveal = menu.addAction(REVEAL)
        chosen = menu.exec(self.library.viewport().mapToGlobal(point))
        if chosen is add:
            self.add_track(identity, len(self.session.clips))
        elif chosen is analyze:
            self.analyze_selected()
        elif chosen is reveal:
            self.reveal(Path(track.path))

    @staticmethod
    def reveal(path: Path):
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        elif sys.platform == "win32":
            subprocess.run(["explorer", "/select,", str(path)], check=False)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # Arrangement edits ---------------------------------------------------------------------

    def add_track(self, identity, index):
        if self.rendering:
            return
        track = self.session.tracks.get(identity)
        if not track:
            return
        if not track.bpm:
            value, ok = QInputDialog.getDouble(self, "Song tempo", f"Enter the BPM of {track.name} to add it to the mix:",
                                               128, 20, 300, 3)
            if not ok:
                return
            track.bpm = value
            track.status = "BPM entered manually"
            self.touched.add(identity)
        clip = Clip(identity, track.cue_in, track.cue_out or track.duration)
        if self.edit(lambda: self.session.clips.insert(index, clip)):
            self.selected_id = clip.id
            self.refresh()
            self.timeline.fit()
            self.load_bands()

    def select_clip(self, identity):
        self.selected_id = identity
        self.refresh()

    def select_transition(self, index):
        if 0 < index < len(self.session.clips):
            self.select_clip(self.session.clips[index].id)
            self.timeline.center_on(self.timeline.positions[index]["start"] / djmix.SR)

    def step_transition(self, delta):
        index = self.transition_index()
        if index:
            self.select_transition(min(len(self.session.clips) - 1, max(1, index + delta)))

    def move_clip(self, identity, index):
        if self.rendering:
            return
        original = next((i for i, c in enumerate(self.session.clips) if c.id == identity), None)
        if original is None:
            return

        def mutate():
            clip = self.session.clips.pop(original)
            target = index - 1 if index > original else index
            self.session.clips.insert(max(0, min(target, len(self.session.clips))), clip)

        if self.edit(mutate):
            self.selected_id = identity
            self.refresh()

    def nudge(self, amount):
        clip = self.selected_clip()
        if clip:
            index = self.session.clips.index(clip)
            self.move_clip(clip.id, index - 1 if amount < 0 else index + 2)

    def remove_clip(self):
        clip = self.selected_clip()
        if not clip or self.rendering:
            return
        index = self.session.clips.index(clip)
        name = self.session.tracks[clip.track_id].name
        if self.edit(lambda: self.session.clips.remove(clip)):
            clips = self.session.clips
            self.selected_id = clips[min(index, len(clips) - 1)].id if clips else ""
            self.refresh()
            self.notify(f"Removed {name} from the mix.", "info", [("Undo", self.undo)])

    def set_cues(self, cue_in, cue_out):
        clip = self.selected_clip()
        if clip:
            self.edit(lambda: (setattr(clip, "cue_in", cue_in), setattr(clip, "cue_out", cue_out)))

    def set_clip_value(self, name, value):
        clip = self.selected_clip()
        if clip and not self.updating and getattr(clip, name) != value:
            self.edit(lambda: setattr(clip, name, value))

    def set_bpm(self, value):
        clip = self.selected_clip()
        if self.updating or not clip:
            return
        track = self.session.tracks[clip.track_id]
        if value == track.bpm:
            return

        def mutate():
            track.bpm = value
            track.status, track.estimated = "BPM edited manually", False

        if self.edit(mutate):
            self.touched.add(track.id)
            self.say("BPM changed. Preview again to hear it.")

    def set_key(self):
        clip = self.selected_clip()
        if self.updating or not clip:
            return
        track = self.session.tracks[clip.track_id]
        key = self.key.currentData() or ""
        if key == track.key:
            return

        def mutate():
            track.key = key
            track.status, track.estimated = "Key edited manually", False

        if self.edit(mutate):
            self.touched.add(track.id)

    def set_transpose(self, semitones):
        clip = self.selected_clip()
        if self.updating or not clip or clip.shift == semitones:
            return
        self.edit(lambda: (setattr(clip, "semitones", int(semitones)), setattr(clip, "transpose", semitones != 0)))

    def match_key(self):
        if self.key_suggestion:
            self.set_transpose(self.key_suggestion["semitones"])

    def set_stabilize(self, checked):
        self.set_clip_value("stabilize", bool(checked))
        self.refresh()

    def set_transition_value(self, side, name, value):
        index = self.transition_index()
        if self.updating or not index:
            return
        clip = self.session.clips[index if side == "in" else index - 1]
        if getattr(clip, name) != value:
            self.edit(lambda: setattr(clip, name, value))
        self.refresh()

    def change_tempo(self, value=None):
        value = self.tempo.value() if value is None else value
        if self.updating or self.rendering or value == self.session.target_bpm:
            return
        self.edit(lambda: setattr(self.session, "target_bpm", value))

    def propose_order(self):
        if self.rendering or len(self.session.clips) < 3:
            return
        proposal = suggest_order(self.session)
        if proposal == self.session.clips:
            self.say("The current order already follows the key and tempo suggestion.")
            return
        order = [clip.id for clip in proposal]
        names = ", ".join(self.session.tracks[clip.track_id].name for clip in proposal)
        self.notify(f"Suggested order, keeping your opening song and favoring compatible keys and nearby tempos: {names}.",
                    "info", [("Apply order", lambda: self.apply_order(order)), ("Keep mine", lambda: None)])

    def apply_order(self, order):
        clips = {clip.id: clip for clip in self.session.clips}
        if set(order) != set(clips):
            self.say("The arrangement changed since the suggestion. Ask for a new one.")
            return
        if self.edit(lambda: setattr(self.session, "clips", [clips[i] for i in order])):
            self.timeline.fit()
            self.say("Order applied. Preview each transition.")

    # Timing --------------------------------------------------------------------------------

    def analyze_timing(self):
        if self.jobs:
            return
        clip = self.selected_clip()
        if not clip:
            return
        track = copy.deepcopy(self.session.tracks[clip.track_id])
        from studio_timing import analyze

        def work(progress, emit):
            return analyze(Path(track.path), track.bpm, track.duration, self.data / "timing-cache", progress)

        def finished(timing):
            current = self.session.tracks.get(track.id)
            if current is None:
                return

            def mutate():
                current.timing = timing
                if timing["state"] != "drift":
                    for item in self.session.clips:
                        if item.track_id == track.id:
                            item.stabilize = False

            if self.edit(mutate, force=True):
                cache_track(current, self.data / "library-cache")
                self.say("Timing analysis ready. Your BPM and cues are unchanged; use the suggested BPM if you want it.")

        self.job(work, finished, rendering=True)

    def use_timing_bpm(self):
        if self.jobs:
            return
        clip = self.selected_clip()
        if not clip:
            return
        track = self.session.tracks[clip.track_id]
        suggestion = track.timing.get("bpm")
        if suggestion is None:
            return

        def mutate():
            track.bpm = suggestion
            track.status = "BPM from whole-song timing analysis, cues kept"

        if self.edit(mutate):
            self.touched.add(track.id)
            cache_track(track, self.data / "library-cache")

    def compare_timing(self):
        if self.jobs:
            return
        clip = self.selected_clip()
        if not clip:
            return
        from studio_audition import TimingComparison, prepare_comparison
        snapshot, selected = copy.deepcopy(self.session), copy.deepcopy(clip)
        self.player.pause()

        def work(progress, emit):
            return prepare_comparison(snapshot, selected, self.data / "audio-cache", progress)

        def finished(report):
            if self.closing:
                return
            try:
                self.comparison = TimingComparison(report, self.output_preference, self.volume.value() / 100, self)
                self.comparison.open()
                self.say("A and B are ready. Switch while it plays; turn on Stabilize tempo to use the correction.")
            except Exception as error:
                self.notify(str(error), "error")

        self.job(work, finished, rendering=True, cancellable=True)

    # Projects ------------------------------------------------------------------------------

    def keep_recovery_copy(self):
        if self.dirty and self.session.clips:
            path = self.data / f"recovery-{time.time_ns()}.djmix"
            self.session.save(path)
            return path
        return None

    def new_project(self):
        if self.jobs:
            return
        kept = self.keep_recovery_copy()
        self.session = Session(tracks=self.session.tracks)
        self.project_path, self.selected_id = None, ""
        self.history, self.future = [], []
        self.changed()
        self.dirty = False
        self.refresh()
        self.timeline.fit()
        self.say("New mix. Your previous arrangement is kept as a recovery copy." if kept else "New mix.")

    def open_project(self):
        if self.jobs:
            return
        start = str(self.project_path.parent if self.project_path else self.data)
        path, _ = QFileDialog.getOpenFileName(self, "Open a mix", start, "DJ Studio project (*.djmix)")
        if not path:
            return
        kept = self.keep_recovery_copy()
        try:
            self.load_project(Path(path))
        except Exception as error:
            self.notify(f"Could not open {Path(path).name}: {error}", "error")
            return
        self.autosave()
        self.say(f"Opened {Path(path).name}." + (" Your unsaved arrangement is kept as a recovery copy." if kept else ""))

    def load_project(self, path, remember=True):
        loaded = Session.from_dict(json.loads(path.read_text()))
        self.session = loaded
        self.project_path = path if remember else None
        self.selected_id = loaded.clips[-1].id if loaded.clips else ""
        self.history, self.future, self.touched = [], [], set()
        self.player.stop()
        self.audio_revision, self.audio_span, self.levels, self.loaded_label = -1, None, None, ""
        self.revision += 1
        self.dirty = False
        if remember:
            self.write_prefs(last_project=str(path))
        self.refresh()
        QTimer.singleShot(60, self.timeline.fit)
        self.load_bands()

    def save_project(self):
        if self.project_path:
            self.write_project(self.project_path)
        else:
            self.save_project_as()

    def save_project_as(self):
        start = self.project_path or (self.data / f"{self.session.name}.djmix")
        path, _ = QFileDialog.getSaveFileName(self, "Save mix", str(start), "DJ Studio project (*.djmix)")
        if path:
            self.write_project(Path(path))

    def write_project(self, path):
        try:
            self.session.name = path.stem
            self.session.save(path)
        except Exception as error:
            self.notify(f"Could not save: {error}", "error")
            return
        self.project_path, self.dirty = path, False
        self.write_prefs(last_project=str(path))
        self.autosave()
        self.refresh()
        self.say(f"Saved {path.name}.")

    # Rendering and playback ----------------------------------------------------------------

    def preview(self):
        index = self.transition_index()
        if len(self.session.clips) < 2 or self.rendering or not index:
            return
        try:
            region = preview_range(self.session, index)
        except djmix.MixError as error:
            self.notify(str(error), "error")
            return
        self.start_render(self.data / "renders" / f"preview-{time.time_ns()}.wav", region, True,
                          f"Transition {index} to {index + 1}")

    def render_full(self):
        if self.session.clips and not self.rendering:
            self.start_render(self.data / "renders" / f"mix-{time.time_ns()}.wav", None, True, "Full mix")

    def export_mix(self):
        if not self.session.clips or self.rendering:
            return
        folder = self.prefs.get("export_folder") or QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.MusicLocation) or str(self.data)
        path, _ = QFileDialog.getSaveFileName(self, "Export mix", str(Path(folder) / f"{self.session.name}.wav"),
                                              "WAV audio (*.wav)")
        if path:
            self.write_prefs(export_folder=str(Path(path).parent))
            self.start_render(Path(path), None, False, "Exported mix", export=True)

    def start_render(self, destination, region, play, title, export=False):
        if self.rendering:
            return
        snapshot, revision = copy.deepcopy(self.session), self.revision
        self.player.stop()
        self.player.setSource(QUrl())
        self.audio_span, self.levels, self.loaded_label = None, None, ""

        def work(progress, emit):
            result = render_session(snapshot, destination, region=region, progress=progress,
                                    cache_dir=self.data / "audio-cache")
            progress("Measuring levels")
            try:
                result["levels"] = level_envelope(destination)
            except Exception:
                result["levels"] = None
            return result

        def finished(result):
            self.audio_origin, self.audio_revision = result["start"], revision
            self.audio_span = (result["start"], result["start"] + result["duration"])
            self.levels, self.loaded_label = result.get("levels"), title
            self.player.setSource(QUrl.fromLocalFile(str(destination.resolve())))
            reused, processed = result["reused_tracks"], result["processed_tracks"]
            detail = (f"{reused} {'song' if reused == 1 else 'songs'} from cache, {processed} processed, "
                      f"peak {result['true_peak_db']:.1f} dBTP")
            if export:
                self.notify(f"Exported {destination.name}, {clock(result['duration'])} long. {detail}.", "info",
                            [(REVEAL, lambda: self.reveal(destination))])
            else:
                self.say(f"{title} is ready to play. {detail}.")
            self.refresh()
            if play and revision == self.revision:
                self.player.play()

        self.job(work, finished, rendering=True, cancellable=True)

    def toggle_play(self):
        self.refresh_audio_outputs()
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif self.audio_revision == self.revision and not self.player.source().isEmpty():
            self.player.play()
        elif len(self.session.clips) > 1:
            self.preview()
        elif self.session.clips:
            self.render_full()

    def stop(self):
        self.player.stop()
        self.sync_position()

    def rewind(self):
        self.player.setPosition(0)
        self.sync_position()

    def navigate(self, seconds):
        self.timeline.center_on(seconds)
        span = self.rendered_range()
        if span and span[0] <= seconds <= span[1]:
            self.player.setPosition(round((seconds - self.audio_origin) * 1000))

    def seek_timeline(self, seconds):
        span = self.rendered_range()
        if span and span[0] <= seconds <= span[1]:
            self.player.setPosition(round((seconds - self.audio_origin) * 1000))
        else:
            self.say("Render the full mix to play from anywhere, or preview the selected transition.")

    def playback_changed(self, state):
        self.update_play_icon()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.meter_timer.start()

    def update_play_icon(self):
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        # The transport lights green while playing, as in a DAW.
        self.play_btn.setIcon(T.icon("pause" if playing else "play", T.PLAYING if playing else T.TEXT, 14,
                                     T.PLAYING if playing else T.TEXT))
        self.play_btn.setToolTip("Pause (Space)" if playing else "Play (Space)")
        self.play_btn.setAccessibleName("Pause" if playing else "Play")

    def update_time(self):
        loaded = not self.player.source().isEmpty()
        seconds = self.audio_origin + self.player.position() / 1000 if loaded else 0.0
        self.position_label.setText(bar_position(seconds, self.session.target_bpm))
        self.time_label.setText(clock(seconds, True))
        duration = self.player.duration() / 1000
        if not loaded:
            self.source_label.set_text("Nothing rendered yet")
        else:
            length = f", {clock(duration)}" if duration > 0 else ""
            changed = ", changed since" if self.audio_revision != self.revision else ""
            self.source_label.set_text(f"{self.loaded_label}{length}{changed}")

    def sync_position(self):
        self.update_time()
        playhead = None if self.player.source().isEmpty() else self.audio_origin + self.player.position() / 1000
        self.timeline.set_playhead(playhead)
        self.overview.playhead = playhead
        self.overview.update()

    def tick(self):
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if playing:
            self.sync_position()
            if self.levels is not None and len(self.levels):
                index = int(self.player.position() / 1000 * BINS_PER_SECOND)
                window = self.levels[max(0, index - 1):index + 2]
                if len(window):
                    left, right = window.max(axis=0)
                    self.meter.set_levels(float(left), float(right), time.monotonic())
        elif not self.meter.decay():
            self.meter_timer.stop()
        self.timeline.viewport().update()

    def closeEvent(self, event):
        self.player.stop()
        self.autosave()
        self.write_prefs(volume=self.volume.value())
        self.band_cancel.set()
        if self.jobs:
            for cancel, *_ in self.jobs.values():
                cancel.set()
            self.closing = True
            self.say("Finishing the current audio step before closing.")
            event.ignore()
        else:
            event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("DJ Studio")
    app.setOrganizationName("DJ Studio")
    app.setStyle("Fusion")
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except AttributeError:
        pass
    app.setPalette(T.palette())
    app.setFont(T.font(T.SIZE_BODY))
    app.setStyleSheet(T.stylesheet())
    if args.data_dir:
        data = args.data_dir
    else:
        data = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    window = Studio(data, args.project)
    window.show()
    if args.library:
        QTimer.singleShot(250, lambda: window.import_paths(window.audio_files([args.library])))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
