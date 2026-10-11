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
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMenu, QProgressBar, QPushButton, QScrollArea,
                               QSlider, QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

import djmix
import studio_theme as T
from studio_engine import (Clip, Session, blend_advice, cache_track, estimate_track, layout, load_track,
                           preview_range, render_session, suggest_order)
from studio_waveform import BINS_PER_SECOND, band_envelope, level_envelope
from studio_widgets import (ComboBox, CueEditor, DoubleSpinBox, ElidedLabel, FlowLayout, LevelMeter, LibraryModel,
                            LibraryView, Notice, Overview, SignedSpinBox, SpinBox, Stepper, Switch, TimeSpinBox, Timeline,
                            camelot, clock, display_key, group, parsed_key, tool_button)


ROOT = Path(__file__).resolve().parent
FORMATS = {".wav", ".mp3", ".flac", ".aif", ".aiff", ".m4a", ".ogg", ".aac"}
REVEAL = "Show in Finder" if sys.platform == "darwin" else "Show in folder"
FREE_KEY = "Option" if sys.platform == "darwin" else "Alt"
RELATIONS = {"same key": "Same key", "relative major/minor": "Relative major and minor",
             "neighboring keys on the circle of fifths": "Neighboring keys"}
SUGGESTION_REASONS = {"same key": "the same key as the previous song",
                      "relative major/minor": "the relative key of the previous song",
                      "neighboring keys on the circle of fifths": "a neighboring key to the previous song"}


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
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.build_top_bar())
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(1)
        body.addWidget(self.build_library())
        body.addWidget(self.build_center())
        body.addWidget(self.build_inspector())
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setStretchFactor(2, 0)
        body.setSizes([330, 850, 340])
        column.addWidget(body, 1)
        column.addWidget(self.build_status_bar())

    def build_top_bar(self):
        bar = QFrame()
        bar.setObjectName("topBar")
        bar.setFixedHeight(76)
        row = QHBoxLayout(bar)
        row.setContentsMargins(20, 0, 20, 0)
        row.setSpacing(0)

        left = QWidget()
        left_row = QHBoxLayout(left)
        left_row.setContentsMargins(0, 0, 0, 0)
        left_row.setSpacing(10)
        self.project_title = ElidedLabel("Untitled mix")
        self.project_title.setProperty("role", "heading")
        self.edited_label = label("", "hint")
        left_row.addWidget(self.project_title)
        left_row.addWidget(self.edited_label)
        left_row.addStretch(1)
        left.setMinimumWidth(140)

        center = QWidget()
        transport = QHBoxLayout(center)
        transport.setContentsMargins(0, 0, 0, 0)
        transport.setSpacing(6)
        self.rewind_btn = tool_button("rewind", "Back to the start", self.rewind, 18)
        self.play_btn = QToolButton()
        self.play_btn.setObjectName("play")
        self.play_btn.setFixedSize(40, 40)
        self.play_btn.setIconSize(QSize(20, 20))
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.play_btn.clicked.connect(self.toggle_play)
        self.stop_btn = tool_button("stop", "Stop", self.stop, 18)
        transport.addWidget(self.rewind_btn)
        transport.addWidget(self.play_btn)
        transport.addWidget(self.stop_btn)
        transport.addSpacing(16)
        readout = QVBoxLayout()
        readout.setSpacing(0)
        readout.setContentsMargins(0, 0, 0, 0)
        times = QHBoxLayout()
        times.setSpacing(6)
        self.time_label = label("0:00.0", "readout")
        self.time_label.setFont(T.font(T.SIZE_READOUT, QFont.Weight.Medium, tabular=True))
        self.time_label.setMinimumWidth(96)
        self.duration_label = label("", "hint")
        self.duration_label.setFont(T.font(13, tabular=True))
        times.addWidget(self.time_label, 0, Qt.AlignmentFlag.AlignBottom)
        times.addWidget(self.duration_label, 0, Qt.AlignmentFlag.AlignBottom)
        times.addStretch(1)
        readout.addLayout(times)
        self.source_label = ElidedLabel("Nothing rendered yet")
        self.source_label.setProperty("role", "small")
        self.source_label.setFixedWidth(200)
        readout.addWidget(self.source_label)
        transport.addLayout(readout)
        transport.addSpacing(10)
        self.meter = LevelMeter()
        transport.addWidget(self.meter)

        right = QWidget()
        right_row = QHBoxLayout(right)
        right_row.setContentsMargins(0, 0, 0, 0)
        right_row.setSpacing(8)
        right_row.addStretch(1)
        self.tempo = DoubleSpinBox()
        self.tempo.setProperty("role", "readout")
        self.tempo.setButtonSymbols(DoubleSpinBox.ButtonSymbols.NoButtons)
        self.tempo.setRange(20, 300)
        self.tempo.setDecimals(2)
        self.tempo.setValue(128)
        self.tempo.setKeyboardTracking(False)
        self.tempo.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.tempo.setFont(T.font(20, QFont.Weight.DemiBold, tabular=True))
        self.tempo.setFixedWidth(96)
        self.tempo.setToolTip("Project tempo. Every song is stretched to this BPM; keys stay as they are.")
        self.tempo.setAccessibleName("Project tempo in BPM")
        self.tempo.editingFinished.connect(self.change_tempo)
        right_row.addWidget(self.tempo)
        right_row.addWidget(label("BPM", "hint"))
        right_row.addSpacing(18)
        speaker = QLabel()
        speaker.setPixmap(T.pixmap("speaker", T.TEXT_3, 16))
        right_row.addWidget(speaker)
        self.output_device = ComboBox()
        self.output_device.setProperty("role", "quiet")
        self.output_device.setAccessibleName("Audio output device")
        self.output_device.setSizeAdjustPolicy(ComboBox.SizeAdjustPolicy.AdjustToContents)
        self.output_device.setMinimumWidth(96)
        self.output_device.setMaximumWidth(220)
        self.output_device.currentIndexChanged.connect(self.change_audio_output)
        right_row.addWidget(self.output_device)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(self.prefs.get("volume", 70)))
        self.volume.setFixedWidth(76)
        self.volume.setAccessibleName("Output volume")
        self.volume.setToolTip("Output volume")
        self.volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        right_row.addWidget(self.volume)
        right_row.addSpacing(18)
        self.export_btn = button("Export mix", self.export_mix, None, "export")
        right_row.addWidget(self.export_btn)

        row.addWidget(left, 1)
        row.addWidget(center, 0)
        row.addWidget(right, 1)
        return bar

    def build_library(self):
        pane = QFrame()
        pane.setObjectName("pane")
        pane.setMinimumWidth(300)
        column = QVBoxLayout(pane)
        column.setContentsMargins(14, 14, 10, 12)
        column.setSpacing(10)
        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 0, 0)
        header.addWidget(label("Library", "heading"))
        header.addSpacing(6)
        self.library_count = label("", "secondary")
        header.addWidget(self.library_count)
        header.addStretch(1)
        self.sort = ComboBox()
        self.sort.setProperty("role", "quiet")
        self.sort.setAccessibleName("Sort songs by")
        self.sort.setSizeAdjustPolicy(ComboBox.SizeAdjustPolicy.AdjustToContents)
        for text, value in (("Recently added", "added"), ("Name", "name"), ("BPM", "bpm"), ("Key", "key")):
            self.sort.addItem(text, value)
        self.sort.setMinimumWidth(self.sort.sizeHint().width())
        self.sort.currentIndexChanged.connect(lambda _: self.refresh_library())
        header.addWidget(self.sort)
        column.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search songs")
        self.search.addAction(T.icon("search", T.TEXT_3, 14), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh_library())
        column.addWidget(self.search)
        self.library_model = LibraryModel(self)
        self.library = LibraryView(self.library_model)
        self.library.addRequested.connect(lambda identity: self.add_track(identity, len(self.session.clips)))
        self.library.filesDropped.connect(lambda paths: self.import_paths(self.audio_files([Path(p) for p in paths])))
        self.library.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library.customContextMenuRequested.connect(self.library_menu)
        self.library.selectionModel().currentChanged.connect(lambda *_: self.refresh_actions())
        column.addWidget(self.library, 1)
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.import_btn = button("Import folder", self.import_folder, None, "folder")
        self.files_btn = button("Add files", self.import_files, None, "plus")
        tools.addWidget(self.import_btn)
        tools.addWidget(self.files_btn)
        tools.addStretch(1)
        column.addLayout(tools)
        hint = label("Double-click a song or drag it onto the timeline to add it. Right-click for more.", "hint", True)
        hint.setContentsMargins(4, 0, 0, 0)
        column.addWidget(hint)
        return pane

    def build_center(self):
        pane = QFrame()
        pane.setObjectName("pane")
        column = QVBoxLayout(pane)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(18, 14, 14, 10)
        header.setSpacing(4)
        header.addWidget(label("Arrangement", "heading"))
        header.addSpacing(8)
        self.arrangement_summary = label("", "secondary")
        header.addWidget(self.arrangement_summary)
        header.addStretch(1)
        self.order_btn = button("Suggest order", self.propose_order, "ghost", "order")
        header.addWidget(self.order_btn)
        header.addSpacing(8)
        self.timeline = Timeline(self.session, self.bands)
        header.addWidget(tool_button("minus", "Zoom out", lambda: self.timeline.zoom(1 / 1.5)))
        header.addWidget(tool_button("plus", "Zoom in", lambda: self.timeline.zoom(1.5)))
        header.addWidget(tool_button("fit", "Fit the whole mix", self.timeline.fit))
        column.addLayout(header)
        self.notice = Notice()
        holder = QHBoxLayout()
        holder.setContentsMargins(18, 0, 18, 10)
        holder.addWidget(self.notice)
        column.addLayout(holder)
        self.overview = Overview(self.timeline)
        self.overview.navigate.connect(self.navigate)
        holder = QHBoxLayout()
        holder.setContentsMargins(18, 0, 18, 12)
        holder.addWidget(self.overview)
        column.addLayout(holder)
        stage = QFrame()
        stage.setObjectName("stage")
        stage_layout = QVBoxLayout(stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.addWidget(self.timeline)
        self.timeline.trackDropped.connect(self.add_track)
        self.timeline.clipMoved.connect(self.move_clip)
        self.timeline.selected.connect(self.select_clip)
        self.timeline.seekRequested.connect(self.seek_timeline)
        self.timeline.transitionSelected.connect(self.select_transition)
        column.addWidget(stage, 1)
        column.addWidget(self.build_transition_bar())
        return pane

    def build_transition_bar(self):
        bar = QFrame()
        bar.setObjectName("transitionBar")
        column = QVBoxLayout(bar)
        column.setContentsMargins(18, 14, 18, 16)
        column.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.transition_title = label("Transition", "heading")
        top.addWidget(self.transition_title)
        top.addSpacing(8)
        self.prev_transition = tool_button("chevron-left", "Previous transition", lambda: self.step_transition(-1))
        self.next_transition = tool_button("chevron-right", "Next transition", lambda: self.step_transition(1))
        top.addWidget(self.prev_transition)
        top.addWidget(self.next_transition)
        top.addStretch(1)
        self.render_btn = button("Render full mix", self.render_full)
        self.render_btn.setToolTip("Render the whole arrangement so you can play and seek anywhere")
        self.preview_btn = button("Preview transition", self.preview, "primary", "play")
        self.preview_btn.setToolTip("Render this blend with four bars either side, then play it (P)")
        top.addWidget(self.render_btn)
        top.addSpacing(6)
        top.addWidget(self.preview_btn)
        column.addLayout(top)

        self.transition_details = QWidget()
        details = QVBoxLayout(self.transition_details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(12)
        self.from_name = ElidedLabel()
        self.from_name.setFont(T.font(T.SIZE_BODY, QFont.Weight.Medium))
        self.from_key = label("", "secondary")
        arrow = QLabel()
        arrow.setPixmap(T.pixmap("arrow-right", T.TEXT_3, 14))
        self.to_name = ElidedLabel()
        self.to_name.setFont(T.font(T.SIZE_BODY, QFont.Weight.Medium))
        self.to_key = label("", "secondary")
        self.verdict = label("", "secondary")
        self.verdict.setTextFormat(Qt.TextFormat.RichText)
        names = QWidget()
        flow = FlowLayout(names, spacing=12, row_spacing=6)
        flow.addWidget(group(self.from_name, self.from_key, arrow, self.to_name, self.to_key, spacing=8))
        flow.addWidget(self.verdict)
        details.addWidget(names)
        self.blend_bars = SpinBox()
        self.blend_bars.setRange(1, 32)
        self.blend_bars.setSuffix(" bars")
        blend = Stepper(self.blend_bars, None, "blend length")
        blend.setFixedWidth(150)
        self.bass = Switch("Bass handoff")
        self.bass.setToolTip("Swap the low end in one bar at the middle of the blend instead of crossfading it")
        self.outro_drums = Switch("Outro is beats only")
        self.intro_drums = Switch("Intro is beats only")
        for switch in (self.outro_drums, self.intro_drums):
            switch.setToolTip("Turn on only if the whole overlap has no pitched bass, vocals, melody or tonal tails. "
                              "This changes the key advice, not the audio.")
        controls = QWidget()
        flow = FlowLayout(controls, spacing=28, row_spacing=6)
        flow.addWidget(group(label("Blend", "secondary"), blend, spacing=10))
        for widget in (self.bass, self.outro_drums, self.intro_drums):
            flow.addWidget(widget)
        details.addWidget(controls)
        column.addWidget(self.transition_details)
        self.transition_empty = label("Add a second song to the arrangement to create a transition.", "secondary")
        column.addWidget(self.transition_empty)
        self.blend_bars.valueChanged.connect(lambda _: self.apply_transition())
        for switch in (self.bass, self.outro_drums, self.intro_drums):
            switch.toggled.connect(lambda _: self.apply_transition())
        return bar

    def build_inspector(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(320)
        scroll.setMaximumWidth(420)
        pane = QFrame()
        pane.setObjectName("pane")
        self.inspector_stack = QStackedWidget(pane)
        outer = QVBoxLayout(pane)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.inspector_stack)

        empty = QWidget()
        empty_column = QVBoxLayout(empty)
        empty_column.setContentsMargins(22, 20, 22, 20)
        empty_column.setSpacing(6)
        empty_column.addWidget(label("No clip selected", "heading"))
        empty_column.addWidget(label("Select a clip in the arrangement to edit its cue points, key, pitch and timing.",
                                     "secondary", True))
        empty_column.addStretch(1)
        self.inspector_stack.addWidget(empty)

        self.editor = QWidget()
        column = QVBoxLayout(self.editor)
        column.setContentsMargins(20, 18, 20, 16)
        column.setSpacing(0)
        self.clip_title = label("", "title", True)
        column.addWidget(self.clip_title)
        column.addSpacing(4)
        self.clip_state = label("", "secondary", True)
        column.addWidget(self.clip_state)
        column.addSpacing(16)
        self.cue_editor = CueEditor(self.bands)
        self.cue_editor.cuesChanged.connect(self.set_cues)
        column.addWidget(self.cue_editor)
        column.addSpacing(10)
        cues = QGridLayout()
        cues.setHorizontalSpacing(12)
        cues.setVerticalSpacing(4)
        cues.addWidget(label("In point", "small"), 0, 0)
        cues.addWidget(label("Out point", "small"), 0, 1)
        self.cue_in, self.cue_out = TimeSpinBox(), TimeSpinBox()
        cues.addWidget(Stepper(self.cue_in, self.bar_length, "in point by one bar"), 1, 0)
        cues.addWidget(Stepper(self.cue_out, self.bar_length, "out point by one bar"), 1, 1)
        column.addLayout(cues)
        column.addSpacing(6)
        column.addWidget(label(f"Handles and buttons move by bars. Hold Shift while dragging for beats, "
                               f"or {FREE_KEY} to place freely.", "hint", True))
        column.addSpacing(22)

        column.addWidget(label("Tempo and key", "section"))
        column.addSpacing(10)
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setColumnStretch(1, 1)
        self.bpm = DoubleSpinBox()
        self.bpm.setDecimals(3)
        self.bpm.setRange(20, 300)
        self.bpm.setSingleStep(.1)
        self.key = ComboBox()
        self.key.addItem("Unknown", "")
        keys = [(code, f"{note} {mode}") for mode in ("minor", "major") for note in djmix.NOTES
                for code in [camelot(f"{note} {mode}")]]
        for code, name in sorted(keys, key=lambda item: (item[0][-1], int(item[0][:-1]))):
            self.key.addItem(f"{code}   {display_key(name)}", name)
        form.addWidget(label("Source BPM", "secondary"), 0, 0)
        form.addWidget(Stepper(self.bpm, None, "BPM"), 0, 1)
        form.addWidget(label("Key", "secondary"), 1, 0)
        form.addWidget(self.key, 1, 1)
        column.addLayout(form)
        column.addSpacing(22)

        column.addWidget(label("Pitch", "section"))
        column.addSpacing(6)
        self.transpose = Switch("Transpose this song")
        column.addWidget(self.transpose)
        column.addSpacing(4)
        pitch = QHBoxLayout()
        pitch.setSpacing(12)
        pitch.addWidget(label("Semitones", "secondary"))
        self.shift = SignedSpinBox()
        self.shift.setRange(-12, 12)
        shift = Stepper(self.shift, None, "semitones")
        shift.setFixedWidth(130)
        pitch.addStretch(1)
        pitch.addWidget(shift)
        column.addLayout(pitch)
        column.addSpacing(6)
        self.shift_hint = label("", "hint", True)
        column.addWidget(self.shift_hint)
        column.addSpacing(22)

        column.addWidget(label("Timing", "section"))
        column.addSpacing(6)
        self.timing_hint = label("", "secondary", True)
        column.addWidget(self.timing_hint)
        column.addSpacing(10)
        timing = QHBoxLayout()
        timing.setSpacing(6)
        self.timing_btn = button("Analyze timing", self.analyze_timing)
        self.use_bpm_btn = button("Use suggested BPM", self.use_timing_bpm, "ghost")
        timing.addWidget(self.timing_btn)
        timing.addWidget(self.use_bpm_btn)
        timing.addStretch(1)
        column.addLayout(timing)
        column.addSpacing(6)
        self.stabilize = Switch("Stabilize tempo")
        self.stabilize.setToolTip("Optional gentle correction in confidently detected passages. Cached for previews and exports.")
        column.addWidget(self.stabilize)
        column.addSpacing(6)
        self.compare_timing_btn = button("Compare A and B", self.compare_timing)
        self.compare_timing_btn.setToolTip("Listen to fixed tempo against stabilized tempo, switching while it plays")
        compare = QHBoxLayout()
        compare.addWidget(self.compare_timing_btn)
        compare.addStretch(1)
        column.addLayout(compare)
        column.addStretch(1)
        column.addSpacing(20)
        divider = QFrame()
        divider.setObjectName("divider")
        column.addWidget(divider)
        column.addSpacing(10)
        moves = QHBoxLayout()
        moves.setSpacing(4)
        self.earlier = button("Earlier", lambda: self.nudge(-1), "ghost", "arrow-left")
        self.later = button("Later", lambda: self.nudge(1), "ghost", "arrow-right")
        self.later.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.remove_btn = button("Remove", self.remove_clip, "danger", "trash")
        self.remove_btn.setToolTip("Remove this clip from the mix. The song stays in your library.")
        moves.addWidget(self.earlier)
        moves.addWidget(self.later)
        moves.addStretch(1)
        moves.addWidget(self.remove_btn)
        column.addLayout(moves)
        self.inspector_stack.addWidget(self.editor)

        for spin in (self.cue_in, self.cue_out, self.shift):
            spin.valueChanged.connect(lambda _: self.apply_clip())
        self.bpm.valueChanged.connect(lambda _: self.apply_track())
        self.key.currentIndexChanged.connect(lambda _: self.apply_track())
        self.transpose.toggled.connect(self.transpose_changed)
        self.stabilize.toggled.connect(lambda _: self.apply_clip())
        scroll.setWidget(pane)
        return scroll

    def build_status_bar(self):
        bar = QFrame()
        bar.setObjectName("statusBar")
        bar.setFixedHeight(32)
        row = QHBoxLayout(bar)
        row.setContentsMargins(18, 0, 12, 0)
        row.setSpacing(10)
        self.status = ElidedLabel("Ready")
        self.status.setProperty("role", "small")
        row.addWidget(self.status, 1)
        self.job_area = QWidget()
        job = QHBoxLayout(self.job_area)
        job.setContentsMargins(0, 0, 0, 0)
        job.setSpacing(10)
        self.job_text = ElidedLabel("")
        self.job_text.setProperty("role", "small")
        self.job_text.setFixedWidth(300)
        self.job_text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.job_bar = QProgressBar()
        self.job_bar.setRange(0, 0)
        self.job_bar.setFixedWidth(120)
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

    def refresh(self):
        self.updating = True
        try:
            clips = self.session.clips
            self.timeline.session = self.session
            self.timeline.selection = self.selected_id
            self.timeline.transition = self.transition_index()
            self.timeline.harmony = {i: self.harmony(i)[0] for i in range(1, len(clips))}
            self.timeline.rendered = self.rendered_range()
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
        self.setWindowTitle(f"{name}[*]")
        self.setWindowModified(self.dirty)
        self.project_title.set_text(name)
        self.edited_label.setText(("Edited" if self.project_path else "Not saved yet") if self.dirty else "")
        if not self.tempo.hasFocus():
            self.tempo.blockSignals(True)
            self.tempo.setValue(self.session.target_bpm)
            self.tempo.blockSignals(False)
        duration = self.timeline.duration()
        count = len(self.session.clips)
        self.arrangement_summary.setText(f"{count} {'song' if count == 1 else 'songs'}, {clock(duration)}" if count else "Empty")

    def refresh_library(self):
        model = self.library_model
        current = self.library.current_id()
        scroll = self.library.verticalScrollBar().value()
        model.session = self.session
        model.query = self.search.text()
        model.order = self.sort.currentData() or "added"
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
        self.transition_title.setText(f"Transition {index} of {len(clips) - 1}")
        outgoing, incoming = clips[index - 1], clips[index]
        a, b = self.session.tracks[outgoing.track_id], self.session.tracks[incoming.track_id]
        self.from_name.set_text(a.name)
        self.to_name.set_text(b.name)
        for widget, track, clip in ((self.from_key, a, outgoing), (self.to_key, b, incoming)):
            key = djmix.transpose(parsed_key(track.key), clip.shift)
            text = display_key(key) if key else "Key unknown"
            widget.setText(text + (f", {clip.shift:+d}" if clip.shift else ""))
        state, text = self.harmony(index)
        dot = {"good": T.GOOD, "bad": T.BAD}.get(state, T.TEXT_3)
        self.verdict.setText(f'<span style="color:{dot}">●</span>&nbsp; {text}')
        self.blend_bars.setValue(incoming.blend_bars)
        self.bass.set_state(incoming.bass_swap)
        self.outro_drums.set_state(outgoing.outro_percussive)
        self.intro_drums.set_state(incoming.intro_percussive)

    def refresh_inspector(self):
        clip = self.selected_clip()
        if not clip:
            self.inspector_stack.setCurrentIndex(0)
            return
        self.inspector_stack.setCurrentIndex(1)
        self.editor.setEnabled(not self.rendering)
        clips = self.session.clips
        index = clips.index(clip)
        track = self.session.tracks[clip.track_id]
        self.clip_title.setText(track.name)
        self.clip_state.setText(f"Song {index + 1} of {len(clips)}. {plain(track.status)}.".replace("..", "."))
        following = clips[index + 1].blend_bars if index + 1 < len(clips) else 0
        self.cue_editor.set_clip(track, clip, clip.blend_bars if index else 0, following)
        self.cue_in.setMaximum(track.duration)
        self.cue_out.setMaximum(track.duration)
        self.cue_in.setValue(clip.cue_in)
        self.cue_out.setValue(clip.cue_out)
        self.bpm.setValue(track.bpm or 128)
        position = self.key.findData(track.key or "")
        self.key.setCurrentIndex(max(0, position))
        self.transpose.set_state(clip.transpose)
        self.shift.setValue(clip.semitones)
        self.shift.parentWidget().setEnabled(clip.transpose)
        suggestion = None
        if index:
            previous = clips[index - 1]
            candidate = copy.copy(clip)
            candidate.transpose = False
            shifts = blend_advice(self.session, previous, candidate)["suggested_b_shifts"]
            suggestion = next((s for s in shifts if s["semitones"]), None) if shifts and shifts[0]["semitones"] else None
        processed = djmix.transpose(parsed_key(track.key), clip.shift)
        if suggestion:
            reason = SUGGESTION_REASONS.get(suggestion["reason"], suggestion["reason"])
            advice = f"{display_key(suggestion['resulting_key'])}, {reason}"
        if clip.shift and processed:
            text = f"Shifted {clip.shift:+d} to {display_key(processed)}."
            if suggestion and suggestion["semitones"] == clip.shift:
                text = f"Shifted {clip.shift:+d} to {advice}."
            elif suggestion:
                text += f" {suggestion['semitones']:+d} would give {advice}."
        elif suggestion:
            text = f"Transposing by {suggestion['semitones']:+d} gives {advice}."
        else:
            text = "The song plays in its original key unless you turn this on."
        self.shift_hint.setText(text)
        timing = track.timing
        state, bpm_suggestion = timing.get("state"), timing.get("bpm")
        idle = not self.jobs
        self.stabilize.set_state(clip.stabilize)
        self.stabilize.setEnabled((state == "drift" or clip.stabilize) and idle)
        self.timing_btn.setEnabled(idle)
        self.compare_timing_btn.setEnabled(state == "drift" and idle)
        show_bpm = bpm_suggestion is not None and abs(bpm_suggestion - (track.bpm or 0)) > 1e-6
        self.use_bpm_btn.setVisible(show_bpm)
        self.use_bpm_btn.setEnabled(idle)
        if show_bpm:
            self.use_bpm_btn.setText(f"Use {bpm_suggestion:.3f} BPM")
        if state == "drift":
            self.timing_hint.setText(f"Tempo varies in this song. Correction would cover {timing['coverage']:.0%} of it; "
                                     "other passages keep fixed timing with smooth joins. Compare A and B before using it.")
        elif state == "steady":
            self.timing_hint.setText("Tempo is steady in the analyzed passages. No correction needed.")
        elif state == "uncertain":
            self.timing_hint.setText("Not enough reliable beats to measure. Check the BPM by ear; correction is unavailable.")
        else:
            self.timing_hint.setText("Analyze timing to check whether the tempo drifts across the song.")
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
        self.output_device.blockSignals(True)
        self.output_device.clear()
        self.output_device.addItem(default.description() or "System output", "system")
        self.output_device.setItemData(0, "Follows the system output setting", Qt.ItemDataRole.ToolTipRole)
        for device in devices:
            self.output_device.addItem(device.description(), bytes(device.id()).hex())
        index = self.output_device.findData(self.output_preference)
        if index < 0:
            # A disconnected, explicitly selected headset must not silently send
            # playback to speakers. Remember it so reconnecting restores the route.
            self.output_device.addItem("Selected output disconnected", self.output_preference)
            index = self.output_device.count() - 1
        self.output_device.setCurrentIndex(index)
        self.output_device.blockSignals(False)
        chosen = default if self.output_preference == "system" else next(
            (d for d in devices if bytes(d.id()).hex() == self.output_preference), None)
        if chosen is None or chosen.isNull():
            self.player.pause()
            self.audio.setMuted(True)
            self.say("Audio output unavailable. Connect it or choose another output.")
            return
        self.audio.setMuted(False)
        if self.audio.device().id() != chosen.id():
            self.audio.setDevice(chosen)
        self.output_device.setToolTip("Playback output: " + chosen.description())

    def change_audio_output(self, index):
        if index < 0:
            return
        self.output_preference = self.output_device.itemData(index)
        self.write_prefs(audio_output=self.output_preference)
        self.refresh_audio_outputs()
        self.say(self.output_device.toolTip())

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

    def apply_track(self):
        clip = self.selected_clip()
        if self.updating or not clip:
            return
        track = self.session.tracks[clip.track_id]
        # Do not round precise analyzed BPMs merely because the field shows three decimals.
        bpm = self.bpm.value() if self.bpm.value() != round(track.bpm, self.bpm.decimals()) else track.bpm
        key = self.key.currentData() or ""
        if bpm == track.bpm and key == track.key:
            return

        def mutate():
            track.bpm, track.key = bpm, key
            track.status, track.estimated = "BPM and key edited manually", False

        if self.edit(mutate):
            self.touched.add(track.id)
            self.say("Saved. Preview again to hear the change.")

    def apply_clip(self):
        clip = self.selected_clip()
        if self.updating or not clip:
            return
        values = {}
        if self.cue_in.value() != round(clip.cue_in, self.cue_in.decimals()):
            values["cue_in"] = self.cue_in.value()
        if self.cue_out.value() != round(clip.cue_out, self.cue_out.decimals()):
            values["cue_out"] = self.cue_out.value()
        for name, value in (("transpose", self.transpose.isChecked()), ("semitones", self.shift.value()),
                            ("stabilize", self.stabilize.isChecked())):
            if getattr(clip, name) != value:
                values[name] = value
        if values:
            self.edit(lambda: [setattr(clip, name, value) for name, value in values.items()])

    def apply_transition(self):
        index = self.transition_index()
        if self.updating or not index:
            return
        outgoing, incoming = self.session.clips[index - 1], self.session.clips[index]
        changes = [(incoming, "blend_bars", self.blend_bars.value()), (incoming, "bass_swap", self.bass.isChecked()),
                   (outgoing, "outro_percussive", self.outro_drums.isChecked()),
                   (incoming, "intro_percussive", self.intro_drums.isChecked())]
        changes = [(clip, name, value) for clip, name, value in changes if getattr(clip, name) != value]
        if changes:
            self.edit(lambda: [setattr(clip, name, value) for clip, name, value in changes])

    def transpose_changed(self, enabled):
        if self.updating:
            return
        clip = self.selected_clip()
        if not clip:
            return
        if enabled and self.shift.value() == 0:
            index = self.session.clips.index(clip)
            if index:
                candidate = copy.copy(clip)
                candidate.transpose = False
                advice = blend_advice(self.session, self.session.clips[index - 1], candidate)
                if advice["suggested_b_shifts"]:
                    self.shift.blockSignals(True)
                    self.shift.setValue(advice["suggested_b_shifts"][0]["semitones"])
                    self.shift.blockSignals(False)
        self.apply_clip()

    def change_tempo(self):
        value = self.tempo.value()
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
        self.play_btn.setIcon(T.icon("pause" if playing else "play", T.ON_ACCENT, 20, T.ON_ACCENT))
        self.play_btn.setToolTip("Pause (Space)" if playing else "Play (Space)")
        self.play_btn.setAccessibleName("Pause" if playing else "Play")

    def update_time(self):
        loaded = not self.player.source().isEmpty()
        self.time_label.setText(clock(self.player.position() / 1000, True) if loaded else "0:00.0")
        duration = self.player.duration() / 1000
        self.duration_label.setText(f"/ {clock(duration)}" if loaded and duration > 0 else "")
        if not loaded:
            self.source_label.set_text("Nothing rendered yet")
        elif self.audio_revision != self.revision:
            self.source_label.set_text(f"{self.loaded_label}, changed since")
        else:
            self.source_label.set_text(self.loaded_label)

    def sync_position(self):
        self.update_time()
        if self.player.source().isEmpty():
            playhead = None
        else:
            playhead = self.audio_origin + self.player.position() / 1000
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
