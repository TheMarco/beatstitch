#!/usr/bin/env python3
"""BeatStitch: a native Qt desktop arrangement editor. No browser or web server."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

# The package check writes only into its new output directory and opens no UI.
if __name__ == '__main__' and sys.argv[1:2] == ['--check-package']:
    from studio_package_check import main as package_check_main
    raise SystemExit(package_check_main(sys.argv[2:]))

if __name__ == '__main__' and sys.argv[1:2] == ['--check-model-download']:
    from studio_model_check import main as model_check_main
    raise SystemExit(model_check_main(sys.argv[2:]))

# Enter the analysis worker before loading Qt or creating another window.
if __name__ == '__main__' and sys.argv[1:2] == ['--section-worker']:
    from studio_section_worker import main as section_worker_main
    raise SystemExit(section_worker_main(sys.argv[2:]))

if __name__ == '__main__' and sys.argv[1:2] == ['--separation-worker']:
    from studio_separation_worker import main as separation_worker_main
    raise SystemExit(separation_worker_main(sys.argv[2:]))

if __name__ == '__main__' and sys.argv[1:2] == ['--key-worker']:
    from studio_key_worker import main as key_worker_main
    raise SystemExit(key_worker_main(sys.argv[2:]))

from PySide6.QtCore import QEvent, QObject, QRunnable, QSignalBlocker, QSize, QStandardPaths, QThreadPool, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QFont, QIcon, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (QAbstractButton, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMenu, QProgressBar, QPushButton, QSlider, QSplitter,
                               QStackedWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget)

import djmix
import studio_theme as T
from studio_engine import (Clip, Session, cache_track, estimate_track, layout, load_track,
                           overlap_limit, preview_range, render_session, suggest_order)
from studio_waveform import BINS_PER_SECOND, band_envelope, level_envelope
from studio_guidance import timing_guidance
from studio_listening import SourceAudition
from studio_recommendations import pitch_advice, review_mix, tempo_advice, transition_harmony
from studio_review import MixReview
from studio_widgets import (DARK_TEXT, BrowserPreview, ComboBox, CueEditor, ElidedLabel, Knob, LevelMeter,
                            LibraryModel, LibraryTable, Notice, Overview, Timeline, ValueBox, bar_position, camelot,
                            clock, display_key, parsed_key, toggle, tool_button)


ROOT = Path(__file__).resolve().parent
FORMATS = {".wav", ".mp3", ".flac", ".aif", ".aiff", ".m4a", ".ogg", ".aac"}
REVEAL = "Show in Finder" if sys.platform == "darwin" else "Show in folder"
FREE_KEY = "Option" if sys.platform == "darwin" else "Alt"
RELATIONS = {"same key": "Same key", "relative major/minor": "Relative major and minor",
             "neighboring keys on the circle of fifths": "Neighboring keys"}


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
    def __init__(self, function, cancel, cache_lock=None, hold_cache=False):
        super().__init__()
        self.function, self.cancel = function, cancel
        self.signals = WorkerSignals()
        self.cache_lock, self.hold_cache = cache_lock, hold_cache

    def run(self):
        token = None
        try:
            def progress(message):
                if self.cancel.is_set():
                    raise djmix.MixError("Operation cancelled.")
                self.signals.progress.emit(message)
            if self.cache_lock is not None:
                from filelock import Timeout
                while True:
                    try:
                        self.cache_lock.acquire(timeout=.1)
                        break
                    except Timeout:
                        progress('Waiting for another audio or analysis job')
                from studio_storage import mark_work
                token = mark_work(self.cache_lock)
            from studio_process import cancel_on, check_cancelled
            with cancel_on(self.cancel):
                check_cancelled()
                result = self.function(progress, self.signals.item.emit)
                check_cancelled()
                self.signals.result.emit(result)
        except Exception as error:
            self.signals.error.emit(str(error))
        finally:
            if token is not None:
                from studio_storage import unmark_work
                unmark_work(token)
            if self.cache_lock is not None and not self.hold_cache:
                self.cache_lock.release()
            self.signals.finished.emit()


class Studio(QMainWindow):
    def __init__(self, data_dir: Path, project: Path | None = None):
        super().__init__()
        self.data = data_dir
        self.data.mkdir(parents=True, exist_ok=True)
        from studio_model_store import configure
        configure(self.data / 'models')
        self.session = Session()
        self.project_path = None
        self.selected_id = ""
        self.jobs = {}
        self.closing = False
        self.rendering = self.importing = self.updating = False
        self.history, self.future, self.touched = [], [], set()
        self.revision, self.audio_revision, self.audio_origin = 0, -1, 0.0
        self.audio_span, self.levels, self.loaded_label = None, None, ""
        from studio_tempo_curve import session_curve
        self.audio_curve = session_curve(self.session)
        self.listen_kind, self.mix_cursor = "mix", 0.0
        self.listen_transition = None
        self.rendered_assets = {}
        self.loaded_record = None
        self.pending_playback = None
        from studio_review_walk import ReviewWalk
        self.listening_review = ReviewWalk()
        self.dirty = False
        self.listening_notes_dialog = None
        self.autosave_time, self.autosave_error = None, ''
        self.autosave_blocked = False
        self.project_error = ''
        self.projects_dialog = None
        self.relink_dialog = None
        self.relink_error = ''
        self.portable_dialog = None
        self.bands, self.band_pending, self.band_failed, self.band_workers = {}, set(), set(), set()
        self.band_cancel = threading.Event()
        self.comparison = None
        self.transition_editor = None
        self.finishing_dialog = None
        self.mastering_dialog = None
        self.loop_dialog = None
        self.separation_dialog = None
        self.lessons_dialog = None
        self.order_dialog = None
        self.guide_dialog = None
        self.cue_review = None
        self.help_window = None
        self.about_window = None
        self.downloads_window = None
        self.key_suggestion = None
        self.prefs = self.read_prefs()
        self.advanced_mode = bool(self.prefs.get('advanced_controls', False))
        from studio_storage import StorageManager
        self.storage = StorageManager(self.data)
        self.output_preference = self.prefs.get("audio_output", "system")
        self.setWindowTitle("BeatStitch")
        self.setWindowIcon(QIcon(str(ROOT / 'assets/about/beatstitch-icon.png')))
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
        self.player.mediaStatusChanged.connect(self.media_ready)
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
        QApplication.instance().installEventFilter(self)
        self.install_guidance()
        self.set_advanced_mode(self.advanced_mode, persist=False)
        self.media_devices.audioOutputsChanged.connect(self.refresh_audio_outputs)
        self.refresh_audio_outputs()
        self.restore_session(project)
        self.refresh()
        QTimer.singleShot(0, self.timeline.fit)
        self.timeline.setFocus()
        self.load_bands()
        self.update_storage_pins()
        self.storage.prune()
        from studio_model_store import available
        if not self.prefs.get('downloads_intro_seen') and not any(available(p) for p in ('beats', 'key', 'content')):
            QTimer.singleShot(600, self.show_downloads)

    def eventFilter(self, watched, event):
        # Destructive arrangement shortcuts must not act on a different focused panel.
        if (event.type() == QEvent.Type.ShortcutOverride and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and isinstance(watched, QWidget) and watched.window() is self):
            focused = QApplication.focusWidget()
            if focused is not self.timeline and (focused is None or not self.timeline.isAncestorOf(focused)):
                event.accept()
                return True
        # Focused controls own Space; elsewhere it remains the mix transport shortcut.
        if (event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Space
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and isinstance(watched, (QAbstractButton, QComboBox)) and watched.window() is self):
            event.accept()
            return True
        # A focused slider owns Home; the mix's rewind action is window-wide.
        if (event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Home
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and isinstance(watched, QSlider) and watched.window() is self):
            event.accept()
            return True
        return super().eventFilter(watched, event)

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
            path = self.data / 'preferences.json'
            temporary = path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(self.prefs))
            temporary.replace(path)
        except OSError:
            pass

    def restore_session(self, project):
        if project:
            try:
                import shutil
                from studio_projects import is_recovery, read_project
                loaded, _ = read_project(project)
                previous = self.data / 'autosave.djmix'
                if previous.exists() and project.resolve() != previous.resolve():
                    try:
                        prior, _ = read_project(previous)
                        differs = prior.to_dict() != loaded.to_dict()
                    except Exception:
                        differs = True
                    if differs:
                        # Keep even unreadable bytes before a launch request replaces autosave.
                        shutil.copy2(previous, self.data / f'recovery-{time.time_ns()}.djmix')
                recovery = is_recovery(project, self.data)
                self.load_project(project, remember=not recovery, loaded=loaded)
                if recovery:
                    self.dirty = True
                    self.write_prefs(last_project=None)
                    self.autosave()
                return
            except Exception as error:
                self.notify(f"Could not open {project.name}: {error}", "error")
                # A failed launch request must still resume the previous autosave.
        autosave = self.data / "autosave.djmix"
        if not autosave.exists():
            return
        try:
            self.load_project(autosave, remember=False)
        except Exception:
            kept = self.data / f"autosave-unreadable-{time.time_ns()}.djmix"
            try:
                autosave.rename(kept)
            except OSError as error:
                kept = autosave
                self.autosave_blocked = True
                self.autosave_error = f'Could not preserve the unreadable autosave: {error}. Use Save as… to keep new work elsewhere.'
            self.session = Session()
            self.notify(f"The last session could not be opened. It was kept as {kept.name}.", "error")
            return
        # The owner travels atomically with the snapshot. A name (or the last
        # opened file in preferences) cannot identify an unsaved arrangement.
        owner = json.loads(autosave.read_text()).get("_recovery_project")
        last = Path(owner) if isinstance(owner, str) and owner else None
        if last and last.is_file() and last.resolve() != autosave.resolve():
            self.project_path = last
            try:
                saved = Session.from_dict(json.loads(last.read_text()))
                self.dirty = saved.to_dict() != self.session.to_dict()
            except Exception:
                self.dirty = True
        else:
            self.dirty = bool(self.session.clips or self.session.tracks)
        self.autosave_time = autosave.stat().st_mtime

    # Layout --------------------------------------------------------------------------------

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        column = QVBoxLayout(root)
        column.setContentsMargins(4, 4, 4, 0)
        column.setSpacing(4)
        column.addWidget(self.build_control_bar())
        column.addWidget(self.build_work_banner())
        self.review_strip = self.build_review_strip()
        column.addWidget(self.review_strip)
        work = QSplitter(Qt.Orientation.Vertical)
        work.setChildrenCollapsible(False)
        work.setHandleWidth(4)
        work.addWidget(self.build_arrangement())
        work.addWidget(self.build_detail())
        work.setStretchFactor(0, 1)
        work.setStretchFactor(1, 0)
        work.setSizes([574, 286])
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(4)
        self.library_tabs = QTabWidget()
        self.library_tabs.setMinimumWidth(250)
        self.library_tabs.addTab(self.build_browser(), "Library")
        self.mix_review = MixReview(self.prefs.get("preferred_tempo_change", 6), self.prefs.get("suggested_pitch_limit", 2))
        self.mix_review.limitsChanged.connect(self.change_suggestion_limits)
        self.mix_review.actionRequested.connect(self.review_action)
        self.library_tabs.addTab(self.mix_review, "Mix review")
        self.library_tabs.setTabToolTip(1, "Persistent checks and suggestions for tempo, key, timing and missing source files. Nothing changes until you choose an action.")
        body.addWidget(self.library_tabs)
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
        self.tempo_curve_btn = QPushButton('Tempo curve…')
        self.tempo_curve_btn.setToolTip('Gradually change BPM across the set. Edit points by bar, or switch the curve off to use one fixed tempo. Pitch is preserved.')
        self.tempo_curve_btn.clicked.connect(self.show_tempo_curve)
        row.addWidget(self.tempo_curve_btn)
        row.addSpacing(6)
        signature = label("4 / 4", "lcd")
        self.time_signature = signature
        signature.setFont(T.font(15, QFont.Weight.Medium, tabular=True))
        signature.setToolTip("BeatStitch assumes 4/4 time")
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
        self.volume.valueChanged.connect(self.set_listening_volume)
        row.addWidget(self.volume)
        row.addSpacing(8)
        self.export_btn = button("Export…", self.export_mix, None, "export")
        self.export_btn.setToolTip("Render the whole mix to a WAV file (Cmd+E)")
        self.mastering_btn = button('Mastering: Off…', self.show_mastering)
        self.mastering_btn.setToolTip('Open optional final mastering settings and volume-matched A/B listening. Applying a choice affects whole-mix playback and export. Unchanged songs are reused.')
        self.mastering_btn.setProperty('helpTopic', 'automatic-mastering')
        row.addWidget(self.mastering_btn)
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
        header.addWidget(self.help_button("library", "Library and importing"))
        column.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.addAction(T.icon("search", T.TEXT_3, 12), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh_library())
        column.addWidget(self.search)
        from studio_library_ui import LibraryTools
        self.library_tools = LibraryTools(self)
        column.addWidget(self.library_tools)
        self.library_model = LibraryModel(self)
        self.library = LibraryTable(self.library_model)
        self.library.addRequested.connect(lambda identity: self.add_track(identity, len(self.session.clips)))
        self.library.addManyRequested.connect(lambda identities: self.add_tracks(identities, len(self.session.clips)))
        self.library.filesDropped.connect(lambda paths: self.import_paths(self.audio_files([Path(p) for p in paths])))
        self.library.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library.customContextMenuRequested.connect(self.library_menu)
        self.library.selectionModel().currentChanged.connect(lambda *_: self.library_selected())
        self.library.selectionModel().selectionChanged.connect(lambda *_: self.library_tools.update_selection())
        column.addWidget(self.library, 1)
        self.preview_strip = BrowserPreview(self.bands)
        column.addWidget(self.preview_strip)
        self.source_audition = SourceAudition()
        self.source_audition.output.setVolume(self.volume.value() / 100)
        self.source_audition.aboutToPlay.connect(self.pause_for_source)
        self.source_audition.failed.connect(lambda message: self.notify(message, "error"))
        column.addWidget(self.source_audition)
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
        self.advanced_toggle = toggle('Advanced controls', 'Show exact source, pitch, timing and drum-only controls. Your mix, waveforms and prepared audio stay unchanged.')
        self.advanced_toggle.setProperty('helpTopic', 'workspace')
        self.advanced_toggle.toggled.connect(self.set_advanced_mode)
        header.addWidget(self.advanced_toggle)
        header.addSpacing(6)
        self.guide_btn = button('Mix guide', self.start_mix_guide, 'ghost')
        self.guide_btn.setToolTip('Choose songs, review tempo and overlaps, and listen to a draft before applying it. One Undo restores the previous arrangement.')
        header.addWidget(self.guide_btn)
        self.order_btn = button("Suggest order", self.propose_order, "ghost", "order")
        header.addWidget(self.order_btn)
        header.addSpacing(6)
        self.timeline = Timeline(self.session, self.bands, self.meter)
        header.addWidget(tool_button("minus", "Zoom out (Cmd+-)", lambda: self.timeline.zoom(1 / 1.5)))
        header.addWidget(tool_button("plus", "Zoom in (Cmd+=)", lambda: self.timeline.zoom(1.5)))
        header.addWidget(tool_button("fit", "Fit the whole mix (Cmd+0)", self.timeline.fit))
        header.addWidget(self.help_button("arrangement", "Arranging a mix"))
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
        self.timeline.tracksDropped.connect(self.add_tracks)
        self.timeline.clipMoved.connect(self.move_clip)
        self.timeline.selected.connect(self.select_clip)
        self.timeline.seekRequested.connect(self.seek_timeline)
        self.timeline.transitionSelected.connect(self.select_transition)
        self.timeline.overlapChanged.connect(self.drag_overlap)
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
        self.detail_panel = detail
        detail.setObjectName("panel")
        detail.setMinimumHeight(276)
        row = QHBoxLayout(detail)
        row.setContentsMargins(6, 6, 6, 6)
        row.setSpacing(6)
        self.clip_stack = QStackedWidget()
        empty = QWidget()
        empty_column = QVBoxLayout(empty)
        empty_column.addStretch(1)
        hint = label("Add songs to the library, then drag them onto the arrangement.\n"
                     "Select a clip to edit it. Select the incoming song to preview its transition.", "secondary", True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_column.addWidget(hint)
        empty_column.addWidget(button("Make your first mix", self.start_mix_guide),
                               0, Qt.AlignmentFlag.AlignCenter)
        self.demo_btn = button('Try a demo mix', self.open_demo)
        self.demo_btn.setToolTip('Open an editable three-song mix with included demo music. No downloads or imports needed. Unsaved work is kept in Projects → Recovery copies.')
        self.demo_btn.setProperty('helpTopic', 'demo')
        empty_column.addWidget(self.demo_btn, 0, Qt.AlignmentFlag.AlignCenter)
        empty_column.addStretch(1)
        self.clip_stack.addWidget(empty)
        self.clip_editor = QWidget()
        boxes = QHBoxLayout(self.clip_editor)
        boxes.setContentsMargins(0, 0, 0, 0)
        boxes.setSpacing(6)
        from studio_simple import SimpleSong
        self.simple_song = SimpleSong(self)
        boxes.addWidget(self.simple_song)
        self.advanced_clip = self.build_clip_box(); boxes.addWidget(self.advanced_clip)
        self.advanced_sample = self.build_sample_box(); boxes.addWidget(self.advanced_sample)
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
        strip_row.addWidget(self.help_button("arrangement", "Clip cues and waveform zoom"))
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
            widget.setAccessibleName(f"Clip {name} in seconds")
            widget.setToolTip(f"Clip {name} in the original song. Drag to move by bars, Shift for beats, "
                              "or double-click to type a time.")
        self.cue_in.committed.connect(lambda value: self.set_clip_value("cue_in", value))
        self.cue_out.committed.connect(lambda value: self.set_clip_value("cue_out", value))
        grid.addWidget(label("Start", "field"), 0, 0)
        grid.addWidget(self.cue_in, 0, 1)
        grid.addWidget(label("End", "field"), 1, 0)
        grid.addWidget(self.cue_out, 1, 1)
        body.addLayout(grid)
        self.song_cues_btn = button("Find musical cues…", self.review_song_cues)
        self.song_cues_btn.setToolTip("Find supported bar starts and changes in energy or texture. Listen to suggested Start/End points "
                                     "and try them in a draft before applying. Your source BPM and cues stay under your control.")
        self.song_cues_btn.setProperty("helpTopic", "musical-cues")
        body.addWidget(self.song_cues_btn)
        self.loop_btn = button('Extend drums…', self.show_loop_extension)
        self.loop_btn.setToolTip('Repeat complete drum bars near this song’s beginning or ending. Compare an extended draft, then apply it as a separate source copy with Undo.')
        self.loop_btn.setProperty('helpTopic', 'drum-loops')
        body.addWidget(self.loop_btn)
        self.separation_btn = button('Reduce competing parts…', self.show_separation)
        self.separation_btn.setToolTip('Try reducing this song’s voice or keeping mostly drums inside one overlap. Compare the actual blend, then apply a separate copy with Undo.')
        self.separation_btn.setProperty('helpTopic', 'separation')
        body.addWidget(self.separation_btn)
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
        frame, strip, strip_row, body = self.box("Song settings", width=236)
        strip_row.addWidget(self.help_button("tempo", "Tempo, key and pitch"))
        tempo = QHBoxLayout()
        tempo.setSpacing(6)
        tempo.addWidget(label("BPM", "field"))
        self.bpm = ValueBox(20, 300, step=.1, fine=.001, decimals=3, width=78)
        self.bpm.setAccessibleName("Source tempo in BPM")
        self.bpm.setToolTip("The song's own tempo. Drag (Shift for fine steps) or double-click to type.")
        self.bpm.committed.connect(self.set_bpm)
        tempo.addWidget(self.bpm)
        self.use_bpm_btn = button("Use suggested", self.use_timing_bpm, "ghost")
        tempo.addWidget(self.use_bpm_btn)
        tempo.addStretch(1)
        body.addLayout(tempo)
        key = QHBoxLayout()
        key.setSpacing(6)
        key.addWidget(label("Source key", "field"))
        self.key = ComboBox()
        self.key.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.key.setAccessibleName("Source key")
        self.key.addItem("Unknown", "")
        keys = [(code, f"{note} {mode}") for mode in ("minor", "major") for note in djmix.NOTES
                for code in [camelot(f"{note} {mode}")]]
        for code, name in sorted(keys, key=lambda item: (item[0][-1], int(item[0][:-1]))):
            self.key.addItem(f"{code}  {display_key(name)}", name)
        self.key.activated.connect(lambda _: self.set_key())
        key.addWidget(self.key, 1)
        body.addLayout(key)
        self.key_hint = label('', 'secondary', True)
        body.addWidget(self.key_hint)
        key_actions = QHBoxLayout()
        self.check_key_btn = button('Detect key', self.check_selected_key)
        self.check_key_btn.setToolTip('Estimate the source key without changing its current label, BPM, cues or pitch. Your own key choices are kept.')
        self.use_key_btn = button('Restore detected', self.use_estimated_key)
        self.use_key_btn.setToolTip('Accept the displayed key estimate as one undoable label change. Audio pitch does not change. You can choose a different key or Unknown at any time.')
        for control in (self.key, self.check_key_btn, self.use_key_btn):
            control.setProperty('helpTopic', 'keys')
        key_actions.addWidget(self.check_key_btn)
        key_actions.addWidget(self.use_key_btn)
        key_actions.addStretch(1)
        body.addLayout(key_actions)
        from studio_pitch_ui import PitchAmount, PlayKey
        self.play_key = PlayKey(self)
        body.addWidget(self.play_key)
        pitch = QHBoxLayout()
        pitch.setSpacing(8)
        self.transpose_knob = PitchAmount()
        self.transpose_knob.setAccessibleName("Pitch shift in semitones")
        self.transpose_knob.setToolTip("Manual pitch shift: use the arrows or type semitones. Zero restores the original pitch. Listen again to hear changes.")
        self.transpose_knob.valueChanged.connect(self.set_transpose)
        pitch.addWidget(self.transpose_knob)
        self.transpose_toggle = QCheckBox("Apply shift")
        self.transpose_toggle.setToolTip("Apply or bypass the chosen semitone shift. Choose a nonzero amount first. Listen again to hear the change.")
        self.transpose_toggle.toggled.connect(lambda checked: self.set_clip_value("transpose", checked))
        pitch.addWidget(self.transpose_toggle)
        pitch.addStretch(1)
        body.addLayout(pitch)
        self.match_btn = button("Match key", self.match_key)
        self.match_btn.setToolTip("Transpose to the suggested key for a smoother blend with the previous song")
        self.shift_hint = label("", "hint", True)
        body.addWidget(self.shift_hint)
        body.addWidget(self.match_btn)
        timing_header = QHBoxLayout()
        timing_header.setSpacing(2)
        self.timing_result = label("Timing not analyzed", "section")
        timing_header.addWidget(self.timing_result, 1)
        timing_header.addWidget(self.help_button("timing", "Understand this timing result"))
        body.addLayout(timing_header)
        self.timing_hint = label("", "secondary", True)
        body.addWidget(self.timing_hint)
        warp = QHBoxLayout()
        warp.setSpacing(4)
        self.stabilize = toggle("Stabilize")
        self.stabilize.toggled.connect(self.set_stabilize)
        self.timing_btn = button("Analyze", self.analyze_timing)
        self.timing_btn.setToolTip("Check whether the tempo drifts across the whole song")
        self.compare_timing_btn = button("A/B", self.compare_timing)
        self.compare_timing_btn.setToolTip("Compare fixed and stabilized tempo while it plays")
        warp.addWidget(self.stabilize)
        warp.addWidget(self.timing_btn)
        warp.addWidget(self.compare_timing_btn)
        warp.addStretch(1)
        body.addLayout(warp)
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
                                   f"or {FREE_KEY} to place them freely. Ctrl/Cmd+scroll zooms at the pointer; "
                                   "scroll or drag the ruler to pan.")
        self.cue_editor.cuesChanged.connect(self.set_cues)
        body.addWidget(self.cue_editor, 1)
        navigation = QHBoxLayout()
        navigation.setContentsMargins(2, 0, 2, 2)
        navigation.setSpacing(2)
        for text, callback in (("Start", self.cue_editor.focus_start), ("End", self.cue_editor.focus_end)):
            control = QToolButton()
            control.setText(text)
            control.setToolTip(f"Zoom to the {text.lower()} cue")
            control.setAccessibleName(control.toolTip())
            control.setFixedSize(34, 20)
            control.clicked.connect(callback)
            navigation.addWidget(control)
        navigation.addStretch(1)
        self.protect_btn = button('Protect…', lambda: self.review_song_cues(protect=True))
        self.protect_btn.setAccessibleName('Protect a passage of this song')
        self.protect_btn.setToolTip('Mark a vocal, build, drop or favorite passage to keep complete and outside both blends. Opens a reviewable draft; nothing changes until Apply.')
        self.protect_btn.setProperty('helpTopic', 'protection')
        navigation.addWidget(self.protect_btn)
        for icon, hint, callback in (("minus", "Zoom out of the song", self.cue_editor.zoom_out),
                                     ("plus", "Zoom into the song", self.cue_editor.zoom_in),
                                     ("fit", "Fit the whole song", self.cue_editor.fit)):
            control = tool_button(icon, hint, lambda checked=False, action=callback: action(), size=12)
            control.setFixedSize(20, 20)
            navigation.addWidget(control)
        body.addLayout(navigation)
        return frame

    def build_transition_box(self):
        frame, strip, strip_row, body = self.box(width=280)
        self.transition_title = label("Transition", "boxTitle")
        strip_row.addWidget(self.transition_title)
        strip_row.addStretch(1)
        strip_row.addWidget(self.help_button("transitions", "Blending two songs"))
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
        self.verdict.setWordWrap(True)
        details.addWidget(self.verdict)
        blend = QHBoxLayout()
        blend.setSpacing(6)
        blend.addWidget(label("Overlap", "field"))
        self.blend_bars = ValueBox(1, 32, step=1, suffix=" bars", width=62, height=24)
        self.blend_bars.setDecimals(0)
        self.blend_bars.setAccessibleName("Incoming overlap length in bars")
        self.blend_bars.committed.connect(lambda value: self.set_transition_value("in", "blend_bars", int(value)))
        stepper = QHBoxLayout()
        stepper.setSpacing(0)
        self.overlap_decrease = tool_button("minus", "Shorten the overlap by one bar (minimum 1 bar)",
                                            lambda: self.blend_bars.stepBy(-1), 12)
        self.overlap_increase = tool_button("plus", "Lengthen the overlap by one bar (maximum 32 bars)",
                                            lambda: self.blend_bars.stepBy(1), 12)
        for control in (self.overlap_decrease, self.overlap_increase):
            control.setFixedSize(24, 24)
            control.setProperty("role", "transport")
            control.setProperty("helpTopic", "transitions")
            control.setAccessibleDescription(control.toolTip())
        stepper.addWidget(self.overlap_decrease)
        stepper.addWidget(self.blend_bars)
        stepper.addWidget(self.overlap_increase)
        blend.addLayout(stepper)
        blend.addStretch(1)
        self.bass = toggle("Bass swap", "Hand the low end over in one bar at the middle of the blend instead of crossfading it")
        self.bass.clicked.connect(lambda checked: self.set_transition_value("in", "bass_swap", checked))
        blend.addWidget(self.bass)
        details.addLayout(blend)
        self.overlap_duration = label("", "small")
        details.addWidget(self.overlap_duration)
        lengths = QHBoxLayout()
        lengths.setSpacing(4)
        lengths.addWidget(label("Presets", "small"), 1)
        self.overlap_presets = {}
        for bars in (4, 8, 16, 32):
            preset = button(str(bars), lambda checked=False, value=bars: self.set_transition_value("in", "blend_bars", value))
            preset.setFixedSize(32, 24)
            preset.setProperty("helpTopic", "transitions")
            self.overlap_presets[bars] = preset
            lengths.addWidget(preset)
        self.reset_blend = button("Reset", self.reset_transition)
        self.reset_blend.setToolTip("Reset this transition to 8 bars (or the longest available shorter length), bass swap on, "
                                   "level balance, extra effects and both drum-only labels off. Keeps song cues and pitch. You can undo this.")
        lengths.addWidget(self.reset_blend)
        details.addLayout(lengths)
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
        self.advanced_transition_fields = QWidget()
        self.advanced_transition_fields.setLayout(drums)
        drums.setContentsMargins(0, 0, 0, 0)
        details.addWidget(self.advanced_transition_fields)
        body.addWidget(self.transition_details)
        self.transition_empty = label("Add a second song to the arrangement to create a transition.", "hint", True)
        body.addWidget(self.transition_empty)
        body.addStretch(1)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        self.preview_btn = button("Listen to blend", self.preview, "primary", "play")
        self.preview_btn.setToolTip("Render this blend with four bars either side, then play it (P)")
        self.render_btn = button("Play mix", self.toggle_play)
        self.render_btn.setToolTip("Render the whole arrangement so you can play and seek anywhere (Cmd+R)")
        actions.addWidget(self.preview_btn)
        actions.addWidget(self.render_btn)
        actions.addStretch(1)
        body.addLayout(actions)
        self.edit_blend_btn = button("Open blend editor", self.edit_blend)
        self.edit_blend_btn.setToolTip("Try overlap, bass, level balancing, filter/echo and pitch alternatives with both real waveforms. Compare current and draft "
                                       "at matched listening levels without restarting, then apply the version you prefer.")
        self.edit_blend_btn.setProperty("helpTopic", "transitions")
        self.align_beats_btn = button('Align beats…', self.align_selected_blend)
        self.align_beats_btn.setToolTip('Find and line up the kick beats in this blend as a draft. See A and B’s measured '
                                       'kick hits, compare timing while it keeps playing, then Apply changes or discard.')
        self.align_beats_btn.setAccessibleDescription(self.align_beats_btn.toolTip())
        self.align_beats_btn.setProperty('helpTopic', 'beat-alignment')
        editors = QHBoxLayout(); editors.setSpacing(4)
        editors.addWidget(self.align_beats_btn); editors.addWidget(self.edit_blend_btn)
        body.addLayout(editors)
        listening = QHBoxLayout()
        self.loop_blend = toggle("Loop blend", "Repeat the selected transition while listening. This never changes the exported mix.")
        self.loop_blend.toggled.connect(lambda _: self.update_transport_loop())
        self.review_btn = button("Review all", self.review_transitions)
        self.review_btn.setToolTip("Review each blend at your own pace. Keep and next records your listening choice; "
                                   "Change opens its editor; Next leaves it unchecked. Nothing is changed automatically.")
        listening.addWidget(self.loop_blend)
        listening.addWidget(self.review_btn)
        body.addLayout(listening)
        self.listening_notes_btn = button('Listening notes…', self.show_listening_notes)
        self.listening_notes_btn.setToolTip('Optional checklist for beats, harmony and competing voices or melodies. '
                                           'Save your own judgments with the project. Notes never change audio or become detector scores.')
        self.listening_notes_btn.setAccessibleDescription(self.listening_notes_btn.toolTip())
        self.listening_notes_btn.setProperty('helpTopic', 'transitions')
        body.addWidget(self.listening_notes_btn)
        return frame

    def build_review_strip(self):
        frame = QFrame()
        frame.setObjectName('reviewStrip')
        row = QHBoxLayout(frame)
        row.setContentsMargins(10, 6, 10, 6)
        self.review_label = label('', wrap=True)
        self.review_label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.review_label, 1)
        self.review_keep = button('Keep and next', self.keep_review_transition, 'primary')
        self.review_change = button('Change…', self.change_review_transition)
        self.review_next = button('Next', self.next_review_transition)
        self.review_resume = button('Review remaining', self.resume_review)
        self.review_end = button('End review', self.dismiss_review)
        for widget, tip in (
            (self.review_keep, 'I like this blend. Remember this listening choice and hear the next one. '
                              'This does not change audio or certify the mix. Changed sound needs another listen.'),
            (self.review_change, 'Try changes to this blend in the editor, then return here to listen again. '
                                'Apply is undoable; closing a draft keeps the current mix.'),
            (self.review_next, 'Hear the next blend without marking this one as kept. Unchecked blends remain available for review.'),
            (self.review_resume, 'Listen to blends that have not been kept, or whose sound changed after you kept them.'),
            (self.review_end, 'Stop this review. It does not change or lock mix settings. '
                             'An audio job may finish caching its work, but will not start playback.')):
            widget.setMinimumHeight(30)
            widget.setToolTip(tip)
            widget.setAccessibleDescription(tip)
            widget.setProperty('helpTopic', 'transitions')
            row.addWidget(widget)
        frame.hide()
        return frame

    def build_work_banner(self):
        self.job_area = QFrame()
        self.job_area.setObjectName('box')
        row = QHBoxLayout(self.job_area); row.setContentsMargins(12, 8, 12, 8); row.setSpacing(12)
        text = QVBoxLayout(); text.setSpacing(3)
        self.job_title = label('', 'section')
        self.job_title.setTextFormat(Qt.TextFormat.PlainText)
        self.job_reason = label('', 'secondary', True)
        self.job_reason.setTextFormat(Qt.TextFormat.PlainText)
        self.job_text = ElidedLabel('')
        self.job_text.setProperty('role', 'secondary')
        text.addWidget(self.job_title); text.addWidget(self.job_reason); text.addWidget(self.job_text); row.addLayout(text, 1)
        self.job_bar = QProgressBar(); self.job_bar.setRange(0, 0)
        self.job_bar.setFixedWidth(120); self.job_bar.setTextVisible(False)
        self.job_bar.setAccessibleName('Audio task in progress')
        row.addWidget(self.job_bar)
        self.cancel_btn = button('Cancel', self.cancel_job)
        self.cancel_btn.setToolTip('Cancel the active audio task. Completed reusable audio stays cached; unfinished work is cleaned up.')
        row.addWidget(self.cancel_btn)
        self.job_area.hide()
        return self.job_area

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
        self.save_state = label('', 'small')
        self.save_state.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.save_state)
        self.projects_btn = button('Projects…', self.show_projects, 'ghost')
        self.projects_btn.setToolTip('Find recent projects and recovery copies, check save state, or save the current mix.')
        self.projects_btn.setAccessibleDescription(self.projects_btn.toolTip())
        self.projects_btn.setProperty('helpTopic', 'projects')
        row.addWidget(self.projects_btn)
        guide = self.help_button("beginner-course", "BeatStitch guide")
        guide.setText("Help")
        guide.setFixedWidth(42)
        row.addWidget(guide)
        return bar

    def help_button(self, topic, title):
        control = QToolButton()
        control.setText("?")
        control.setFixedSize(20, 20)
        control.setAccessibleName("Help: " + title)
        control.setToolTip(title + ". Open the offline guide.")
        control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        control.clicked.connect(lambda: self.show_help(topic))
        return control

    def show_help(self, topic=None):
        if topic is None:
            widget = QApplication.focusWidget()
            while widget is not None and not topic:
                topic = widget.property("helpTopic")
                widget = widget.parentWidget()
        from studio_help import HelpDialog
        if self.help_window is None:
            self.help_window = HelpDialog(self, listen_examples=self.show_lessons)
        self.help_window.open_topic(topic or "beginner-course")
        self.help_window.show()
        self.help_window.raise_()
        self.help_window.activateWindow()

    def show_about(self):
        from studio_about import AboutDialog
        if self.about_window is None:
            self.about_window = AboutDialog(self)
        self.about_window.show()
        self.about_window.raise_()
        self.about_window.activateWindow()

    def show_downloads(self, required=None, on_ready=None, requester=None):
        from studio_downloads import DownloadsDialog
        self.write_prefs(downloads_intro_seen=True)
        if self.downloads_window is None:
            self.downloads_window = DownloadsDialog(self)
            self.downloads_window.stateChanged.connect(self.download_state_changed)
        if required:
            if not self.downloads_window.busy:
                self.downloads_window.setParent(requester or self, self.downloads_window.windowFlags())
            self.downloads_window.request(required, on_ready)
        else:
            if not self.downloads_window.busy:
                self.downloads_window.setParent(self, self.downloads_window.windowFlags())
                self.downloads_window.on_ready = None
                self.downloads_window.request_note.hide()
            self.downloads_window.refresh()
            self.downloads_window.show()
            self.downloads_window.raise_()
            self.downloads_window.activateWindow()

    def require_models(self, packs, on_ready=None, requester=None):
        from studio_model_store import available
        if all(available(p) for p in packs):
            return True
        def continue_if_open():
            if on_ready and (requester is None or requester.isVisible()):
                on_ready()
        self.show_downloads(packs, continue_if_open if on_ready else None, requester)
        return False

    def download_state_changed(self):
        self.update_job_flags()
        if not self.downloads_window.busy:
            self.refresh()

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
        self.act_guide = add(file, 'Mix guide…', self.start_mix_guide)
        self.act_demo = add(file, 'Try a demo mix', self.open_demo)
        self.act_open = add(file, "Open…", self.open_project, standard.Open)
        self.act_projects = add(file, 'Projects and recovery…', self.show_projects, 'Ctrl+Shift+O')
        self.act_relink = add(file, 'Reconnect source audio…', self.show_relink, 'Ctrl+Shift+L')
        self.act_portable = add(file, 'Create portable project…', self.show_portable, 'Ctrl+Shift+P')
        file.addSeparator()
        self.act_save = add(file, "Save", self.save_project, standard.Save)
        self.act_save_as = add(file, "Save as…", self.save_project_as, standard.SaveAs)
        file.addSeparator()
        self.act_import = add(file, "Import folder…", self.import_folder, "Ctrl+I")
        self.act_files = add(file, "Add files…", self.import_files, "Ctrl+Shift+I")
        self.act_downloads = add(file, 'Downloads && features…', self.show_downloads)
        self.act_downloads.setToolTip('Choose optional local detectors and vocal removal. See download sizes, progress, retry and removal. Mixing and export work without them.')
        file.addSeparator()
        self.act_export = add(file, "Export mix…", self.export_mix, "Ctrl+E")
        add(file, 'Storage and cache…', self.show_storage)
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
        self.act_finishing = add(edit, 'Start && ending…', self.show_finishing)
        self.act_mastering = add(edit, 'Automatic mastering…', self.show_mastering)
        self.act_tempo_curve = add(edit, 'Set tempo curve…', self.show_tempo_curve)
        self.act_loop_extension = add(edit, 'Extend selected drums…', self.show_loop_extension)
        self.act_separation = add(edit, 'Reduce competing parts…', self.show_separation)

        playback = bar.addMenu("Playback")
        self.act_play = add(playback, "Play or pause mix", self.toggle_play, "Space")
        add(playback, "Stop", self.stop, "Ctrl+.")
        add(playback, "Back to start", self.rewind, "Home")
        playback.addSeparator()
        self.act_preview = add(playback, "Listen to transition", self.preview, "P")
        self.act_render = add(playback, "Prepare and play full mix", self.render_full, "Ctrl+R")
        playback.addSeparator()
        self.act_prev = add(playback, "Previous transition", lambda: self.step_transition(-1), "[")
        self.act_next = add(playback, "Next transition", lambda: self.step_transition(1), "]")

        view = bar.addMenu("View")
        self.act_advanced = QAction('Advanced controls', self)
        self.act_advanced.setCheckable(True)
        self.act_advanced.setShortcut(QKeySequence('Ctrl+Shift+A'))
        self.act_advanced.toggled.connect(self.set_advanced_mode)
        view.addAction(self.act_advanced); self.addAction(self.act_advanced)
        view.addSeparator()
        add(view, "Zoom in", lambda: self.timeline.zoom(1.5), standard.ZoomIn, "Ctrl+=")
        add(view, "Zoom out", lambda: self.timeline.zoom(1 / 1.5), standard.ZoomOut)
        add(view, "Fit the whole mix", self.timeline.fit, "Ctrl+0")

        help_menu = bar.addMenu("Help")
        add(help_menu, "Help for this control", self.show_help, "F1")
        add(help_menu, "Beginner course…", lambda: self.show_help("beginner-course"))
        add(help_menu, "BeatStitch guide", lambda: self.show_help("quick-start"))
        self.act_lessons = add(help_menu, 'Listening examples…', self.show_lessons)
        add(help_menu, "Understanding tempo correction", lambda: self.show_help("timing"))
        add(help_menu, "Keyboard and mouse shortcuts", lambda: self.show_help("shortcuts"))
        help_menu.addSeparator()
        self.act_about = add(help_menu, "About BeatStitch…", self.show_about)
        self.act_about.setMenuRole(QAction.MenuRole.AboutRole)

    def install_guidance(self):
        """Short explanations at the control, with F1 leading to the full topic."""
        descriptions = {
            "tempo": ("tempo", "Project BPM: the common tempo for the mix. Every song is stretched to match, "
                       "with pitch preserved unless you choose a shift under Play in key. This does not straighten tempo drift. "
                       "When Tempo curve is on, its points control speed and this fixed-BPM box is disabled. "
                       "Drag up/down; hold Shift for hundredths. Double-click to type; arrows nudge. F1 for help."),
            'tempo_curve_btn': ('tempo-curve', 'Gradually change the mix BPM using points at musical bars. Both songs follow the same curve during a blend. Off uses one fixed BPM and remembers your points. F1 for help.'),
            "bpm": ("tempo", "Source BPM: how fast this original song is. An estimate may be wrong or half/double time. "
                     "Correcting it changes how the song is stretched to project BPM. Drag; Shift for fine steps; "
                     "double-click to type. Analyze offers a separate whole-song estimate."),
            "key": ("keys", "The original song's estimated musical key. Correct this label if needed; changing it "
                     "updates harmonic advice without changing the audio. Restore detected returns to the saved estimate. "
                     "Choose Play in key to change the pitch, then Listen to blend or Play mix to hear it."),
            "transpose_knob": ("keys", "Pitch shift in semitones: +1 raises every note by one piano key; −1 lowers it. "
                                "Use the arrows or type a value. Zero restores original pitch. "
                                "Apply shift bypasses the amount while remembering it. Listen again to hear changes."),
            "transpose_toggle": ("keys", "Apply or bypass this clip's pitch shift. Off keeps the original key while "
                                  "remembering the semitone amount. Listen again to hear changes. Tempo matching still applies."),
            "cue_in": ("arrangement", "Start time in the original audio, before tempo changes. This is where the clip "
                        "enters the mix, including its incoming blend. Drag by bars, Shift by beats; double-click for m:ss.mmm."),
            "cue_out": ("arrangement", "End time in the original audio, before tempo changes. The outgoing blend ends here. "
                         "Leave enough audio for both overlaps. Drag by bars, Shift by beats; double-click for m:ss.mmm."),
            "blend_bars": ("transitions", "Click − or + to shorten or lengthen the overlap by one bar. "
                            "You can also use arrow keys, drag the number up/down, or double-click to type. "
                            "The available-audio limit and duration in seconds appear below; Presets offers common choices. "
                            "This incoming song overlaps the previous song for that many four-beat bars. "
                            "The seconds shown below follow the current tempo, including any tempo curve. Longer blends need enough audio in both clips. "
                            "Listen for matching phrases and competing vocals or bass."),
            "bass": ("transitions", "Pass the bass from the outgoing song to the incoming song over one bar at the "
                      "middle of the blend. This can reduce competing kicks and basslines. Off crossfades the low end too."),
            "library": ("library", "Your source songs. Double-click a row or drag it onto the arrangement to add a clip. "
                         "Select several with Cmd/Ctrl-click or Shift-click; Add selected, Enter or dragging inserts them in list order. "
                         "Click column headings to sort; BPM and key are estimates you can correct. "
                         "Colored squares mark songs in the mix; green dots suggest a compatible key, not a guaranteed good blend."),
            "search": ("library", "Search song names, filenames, key names or Camelot keys. Combine with collection and library filters. This does not remove songs from the mix."),
            "import_btn": ("library", "Import audio from a folder and its subfolders. Analysis runs locally; review its "
                            "BPM, key and cue suggestions before mixing. Your original audio stays unchanged."),
            "files_btn": ("library", "Choose audio files to add to the library. Supported formats: WAV, AIFF, FLAC, MP3, M4A, AAC and OGG."),
            "preview_strip": ("library", "Source waveform for the selected library song. Use Listen below it to hear "
                               "the original song without adding it to your mix. Drag the waveform to add the song."),
            "timeline": ("arrangement", "Songs alternate between Deck A and Deck B. Drag clips to reorder. "
                          "Select an incoming clip or a numbered transition to edit that blend. "
                          "The pair is labeled Out and In. Drag its orange overlap-start grip to change the length. "
                          "Click the ruler to choose where Play mix starts. Audio is prepared automatically. "
                          + Timeline.KEYBOARD_HELP),
            "cue_editor": ("arrangement", "Source waveform and clip Start/End markers. Drag markers to snap to bars; "
                            f"Shift snaps to beats; {FREE_KEY} places freely. Start/End buttons zoom to a cue. "
                            "Ctrl/Cmd+scroll zooms; scroll or drag the ruler to pan. The waveform shows source audio, not the correction result. "
                            + CueEditor.KEYBOARD_HELP),
            "volume": ("playback", "Listening volume for this app and the timing A/B comparison. It does not change the exported WAV's level."),
            "export_btn": ("playback", "Save the whole mix as a WAV master, lossless FLAC, or MP3 for sharing. "
                            "Optionally include a tracklist with timestamps. Save stores the editable project; Export creates audio."),
            "source_label": ("playback", "Shows whether you are listening to the full mix or one transition. "
                              "Play mix always follows the mix playhead; Listen to blend auditions the selected pair. "
                              "Edits are prepared on the next listen. Library Listen plays original source audio."),
            "timing_btn": ("timing", "Analyze reliable rhythm passages across the song for tempo drift and a suggested BPM. "
                            "Your applied BPM and cues stay unchanged. A cached result may be reused; repeating analysis "
                            "does not guarantee a clearer answer."),
            "stabilize": ("timing", "Optional correction for tempo fluctuations within a song. See the timing result above "
                           "for availability. Project BPM matching is separate."),
            "compare_timing_btn": ("timing", "Prepare a loudness-matched timing comparison using this clip's cues, pitch and project BPM. "
                                    "Switch A/B while playback continues. Choosing B here does not enable Stabilize in the mix."),
        }
        for name, (topic, description) in descriptions.items():
            widget = getattr(self, name)
            widget.setProperty("helpTopic", topic)
            widget.setToolTip(description)
            widget.setAccessibleDescription(description)
            widget.setToolTipDuration(20000)
        for name, topic in (("match_btn", "keys"), ("use_bpm_btn", "tempo"), ("order_btn", "planning"),
                            ('guide_btn', 'first-mix'),
                            ("render_btn", "playback"), ("play_btn", "playback"), ("preview_btn", "transitions"),
                            ("outro_drums", "transitions"), ("intro_drums", "transitions"),
                            ("timing_result", "timing"), ("timing_hint", "timing")):
            getattr(self, name).setProperty("helpTopic", topic)

    def set_advanced_mode(self, advanced, *, persist=True):
        """A view preference only: never change the mix or invalidate prepared audio."""
        self.advanced_mode = bool(advanced)
        focused = QApplication.focusWidget()
        hiding = (self.simple_song,) if advanced else (self.advanced_clip, self.advanced_sample, self.advanced_transition_fields)
        move_focus = focused is not None and any(w is focused or w.isAncestorOf(focused) for w in hiding)
        self.simple_song.setVisible(not advanced)
        self.bass.setText('Bass swap' if advanced else 'Bass handoff')
        for widget in (self.advanced_clip, self.advanced_sample, self.advanced_transition_fields,
                       self.time_signature, self.position_label):
            widget.setVisible(advanced)
        for control in (self.advanced_toggle, self.act_advanced):
            with QSignalBlocker(control): control.setChecked(advanced)
        if move_focus: self.advanced_toggle.setFocus()
        QTimer.singleShot(0, self.fit_song_settings)
        if persist: self.write_prefs(advanced_controls=self.advanced_mode)

    def fit_song_settings(self):
        # Wrapped key/timing guidance needs its real height at these fixed widths.
        # A fixed splitter minimum otherwise squeezes labels into the pitch knob.
        required = 276
        boxes = (self.advanced_clip, self.advanced_sample) if self.advanced_mode else (self.simple_song,)
        for box in boxes:
            required = max(required, box.heightForWidth(box.width()) + 12)
        self.detail_panel.setMinimumHeight(required)

    def show_song_settings(self):
        self.set_advanced_mode(True)
        self.bpm.setFocus()
        self.say('Advanced song controls are visible. Turn off Advanced controls to return to the simple view.')

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
                "planning": copy.deepcopy(self.session.planning),
                "collections": copy.deepcopy(self.session.collections),
                'fades': (self.session.fade_in_bars, self.session.fade_out_bars),
                'mastering': self.session.mastering_enabled,
                'listening_notes': copy.deepcopy(self.session.listening_notes),
                'tempo_curve': (self.session.tempo_enabled, copy.deepcopy(self.session.tempo_points)),
                'locations': {k: (t.path, t.duration, t.cue_in, t.cue_out, copy.deepcopy(t.fingerprint),
                                  t.waveform, t.structure, t.content, t.song_sections) for k, t in self.session.tracks.items()},
                "metadata": {k: (t.bpm, t.key, t.status, t.estimated, copy.deepcopy(t.timing), copy.deepcopy(t.beat_grid),
                                  t.key_origin, copy.deepcopy(t.key_analysis))
                             for k, t in self.session.tracks.items()}}

    def restore(self, state):
        self.session.clips = state["clips"]
        self.session.target_bpm = state["tempo"]
        self.session.planning = copy.deepcopy(state.get('planning', {}))
        self.session.collections = copy.deepcopy(state.get('collections', {}))
        self.session.fade_in_bars, self.session.fade_out_bars = state.get('fades', (0, 0))
        self.session.mastering_enabled = state.get('mastering', False)
        self.session.listening_notes = copy.deepcopy(state.get('listening_notes', []))
        self.session.tempo_enabled, self.session.tempo_points = copy.deepcopy(state.get('tempo_curve', (False, [])))
        for identity, values in state["metadata"].items():
            if identity in self.session.tracks:
                track = self.session.tracks[identity]
                track.bpm, track.key, track.status, track.estimated, track.timing, track.beat_grid, track.key_origin, track.key_analysis = values
        for identity, values in state.get('locations', {}).items():
            if identity in self.session.tracks:
                track = self.session.tracks[identity]
                if track.path != values[0]: self.invalidate_source_display(identity)
                track.path, track.duration, track.cue_in, track.cue_out, track.fingerprint, track.waveform, track.structure, track.content, track.song_sections = values

    def edit(self, mutate, force=False, action="Edit mix"):
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
        if state == self.snapshot():
            return False
        state["action"] = action
        self.history.append(state)
        self.history = self.history[-50:]
        self.future.clear()
        self.changed()
        return True

    def undo(self):
        if not self.history or self.rendering:
            return
        previous = self.history.pop()
        action = previous.get("action", "Edit mix")
        self.future.append({**self.snapshot(), "action": action})
        self.restore(previous)
        self.after_history(f"Undid: {action}.")

    def redo(self):
        if not self.future or self.rendering:
            return
        following = self.future.pop()
        action = following.get("action", "Edit mix")
        self.history.append({**self.snapshot(), "action": action})
        self.restore(following)
        self.after_history(f"Redid: {action}.")

    def after_history(self, message):
        if not self.selected_clip():
            self.selected_id = self.session.clips[-1].id if self.session.clips else ""
        self.changed()
        self.load_bands()
        self.say(message)

    def changed(self):
        self.sync_position()
        self.revision += 1
        if not self.refresh_prepared_audio():
            self.player.pause()
            self.pending_playback = None
        invalidated = self.listening_review.reconcile(self.session)
        if invalidated:
            self.listening_review.message = f'{invalidated} previously kept blend(s) changed; listen again.'
        self.dirty = True
        self.autosave()
        self.refresh()
        self.update_storage_pins()

    def update_storage_pins(self):
        paths = [t.path for t in self.session.tracks.values()]
        if not self.player.source().isEmpty():
            paths.append(self.player.source().toLocalFile())
        if self.finishing_dialog is not None and self.finishing_dialog.preview_path:
            paths.append(self.finishing_dialog.preview_path)
        if self.mastering_dialog is not None:
            paths.extend(self.mastering_dialog.preview_paths)
        if self.loop_dialog is not None:
            paths.extend(self.loop_dialog.cache_paths)
        if self.separation_dialog is not None:
            paths.extend(self.separation_dialog.cache_paths)
        self.storage.pin(paths)

    def show_storage(self):
        from studio_storage_ui import StorageDialog
        self.storage_dialog = StorageDialog(self.storage, lambda value: self.write_prefs(cache_gb=value), self)
        self.storage_dialog.open()

    def autosave(self):
        self.autosave_timer.stop()
        if self.autosave_blocked:
            self.refresh_project_status()
            return
        try:
            from studio_projects import write_snapshot
            path = self.data / "autosave.djmix"
            write_snapshot(self.session, path, self.project_path)
            self.autosave_time, self.autosave_error = path.stat().st_mtime, ''
        except OSError as error:
            self.autosave_error = str(error)
            self.notify(f"Autosave failed: {error}", "error")
        self.refresh_project_status()

    def rendered_range(self):
        current = self.audio_revision == self.revision and not self.player.source().isEmpty()
        return self.audio_span if current else None

    def harmony(self, index):
        item = transition_harmony(self.session, index)
        if item["state"] == "percussion":
            return "good", "Beats-only overlap"
        if item["state"] == "unknown":
            return "unknown", "Key unknown"
        if item["state"] == "compatible":
            return "good", RELATIONS.get(item["reason"], item["reason"].capitalize())
        suggestion = pitch_advice(self.session, index, self.mix_review.pitch_limit.value())["suggestion"]
        if suggestion and index in suggestion["improved"]:
            return "bad", f"Possible key clash. Try {suggestion['semitones']:+d} st"
        return "bad", "Possible key clash"

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
            self.refresh_mix_review()
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
        from studio_tempo_curve import session_curve
        duration = float(session_curve(self.session).to_output(self.timeline.duration()))
        self.tempo_curve_btn.setText('Tempo curve: on' if self.session.tempo_enabled else 'Tempo curve…')
        count = len(self.session.clips)
        summary = f"{count} {'song' if count == 1 else 'songs'}, {clock(duration)}" if count else "Empty"
        self.arrangement_summary.setText(f"{name}{state}   {summary}")
        self.refresh_project_status()

    def refresh_library(self):
        model = self.library_model
        current = self.library.current_id()
        selected = self.library.selected_ids()
        scroll = self.library.verticalScrollBar().value()
        blocker = QSignalBlocker(self.library.selectionModel())
        self.library_tools.refresh()
        model.session = self.session
        model.query = self.search.text()
        model.collection = self.library_tools.collection.currentData()
        model.filter_kind = self.library_tools.filter.currentData()
        clip = self.selected_clip()
        if clip:
            model.context = djmix.transpose(parsed_key(self.session.tracks[clip.track_id].key), clip.shift)
            model.context_track = clip.track_id
        else:
            model.context = model.context_track = None
        model.rebuild()
        self.library.select_ids(selected, current)
        del blocker
        self.library.verticalScrollBar().setValue(scroll)
        count = len(self.session.tracks)
        shown = len(model.ids)
        self.library_count.setText(f'{shown} of {count} songs' if shown != count else f"{count} {'song' if count == 1 else 'songs'}" if count else '')
        self.preview_strip.set_track(self.session.tracks.get(self.library.current_id() or ""))
        self.source_audition.set_track(self.session.tracks.get(self.library.current_id() or ""))
        self.library_tools.update_selection()

    def library_selected(self):
        self.preview_strip.set_track(self.session.tracks.get(self.library.current_id() or ""))
        self.source_audition.set_track(self.session.tracks.get(self.library.current_id() or ""))
        self.library_tools.update_selection()
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
        harmony = transition_harmony(self.session, index)
        if harmony['state'] == 'compatible':
            keys = ' ↔ '.join(harmony['keys'])
            self.verdict.setText(f'<span style="color:{dot}">●</span>&nbsp; {text}<br>'
                                 f'{keys}<br>No extra pitch shift needed for this blend.')
        self.verdict.setToolTip('Compatibility uses the keys after any selected pitch shifts. Relative major/minor '
                                'keys can fit without transposition, such as A minor and C major. '
                                'This is key-label advice; listen for conflicting melodies, vocals and bass.')
        limit = overlap_limit(self.session, index)
        self.blend_bars.setMaximum(limit["bars"])
        self.blend_bars.setValue(incoming.blend_bars)
        from studio_tempo_curve import session_curve
        curve = session_curve(self.session)
        overlap_end = layout(self.session)[index - 1]['end'] / djmix.SR
        def overlap_seconds(bars):
            return float(curve.to_output(overlap_end) - curve.to_output(overlap_end - bars * 240 / self.session.target_bpm))
        seconds = overlap_seconds(incoming.blend_bars)
        self.overlap_duration.setText(f'{seconds:.1f} sec · max {limit["bars"]} bars'
                                     + ('\nLevel balance on · edit in blend editor' if incoming.balance_levels else '')
                                     + ('\n' + {'filter': 'Filter out', 'echo': 'Echo out'}[incoming.transition_effect]
                                        + ' · edit in blend editor' if incoming.transition_effect != 'none' else ''))
        reason = ("The app supports up to 32 bars per blend." if limit["bars"] == 32 else
                  f'{limit["song"]} limits this overlap to {limit["bars"]} bars. '
                  + ("Its other blend also needs room. " if limit["neighbor"] else "")
                  + "Widen that clip's Start/End range to allow a longer blend.")
        if limit.get('protected'):
            reason = f'Protected “{limit["protected"]}” in {limit["song"]} limits this blend to {limit["bars"]} bars. Move the cues or review its protection to allow more overlap.'
        self.overlap_duration.setToolTip(reason + ('\nGentle level balancing is on for this blend. Open the blend editor to compare or turn it off.'
                                                  if incoming.balance_levels else ''))
        self.overlap_increase.setToolTip(f'Lengthen the overlap by one bar. {reason}')
        self.overlap_increase.setAccessibleName(f'Lengthen the overlap by one bar (maximum {limit["bars"]} bars)')
        self.overlap_increase.setAccessibleDescription(self.overlap_increase.toolTip())
        for bars, preset in self.overlap_presets.items():
            preset.setEnabled(bars <= limit["bars"])
            description = f'Set overlap to {bars} bars ({overlap_seconds(bars):.1f} seconds). '
            preset.setAccessibleName(f"Set overlap to {bars} bars")
            preset.setToolTip(description + (reason if bars > limit["bars"] else "Listen to check the blend."))
            preset.setAccessibleDescription(preset.toolTip())
        self.overlap_decrease.setEnabled(incoming.blend_bars > self.blend_bars.minimum())
        self.overlap_increase.setEnabled(incoming.blend_bars < self.blend_bars.maximum())
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
        from studio_key import current_analysis as current_key_analysis, guidance as key_guidance
        key_title, key_detail = key_guidance(track)
        self.key_hint.setText(key_title)
        self.key_hint.setToolTip(key_detail)
        self.key.setToolTip('Choose the source key or Unknown. Your choice is saved and protected during reanalysis. This edits the label, not the audio.\n' + key_detail)
        key_result = current_key_analysis(track)
        detected = key_result.get('candidates', [])
        detection_text = ('Detected: ' + detected[0]['key'] if detected else
                          'No saved estimate. Choose Detect key.' if not key_result else 'Detection inconclusive.')
        self.key_hint.setText('Label only; audio unchanged.\n' + detection_text
                             + (' · chosen by you' if track.key_origin == 'manual' and not track.key else ''))
        self.use_key_btn.setToolTip('Restore ' + detected[0]['key'] + ' as the source label. Your manual correction does not erase the estimate. Audio pitch is unchanged.'
                                   if detected else 'No usable saved estimate. Choose Detect key first; then Restore detected to accept its result.')
        self.check_key_btn.setEnabled(not self.jobs and Path(track.path).is_file())
        self.check_key_btn.setToolTip('Estimate the source key without replacing this label.' if self.check_key_btn.isEnabled()
                                     else 'Wait for the current task to finish.' if self.jobs else 'Reconnect this song’s source file before checking its key.')
        self.use_key_btn.setEnabled(not self.jobs and key_result.get('state') == 'estimated'
                                   and (track.key_origin != 'detected' or track.key != key_result['candidates'][0]['key']))
        self.transpose_knob.setValue(clip.semitones)
        self.transpose_toggle.setChecked(bool(clip.shift))
        self.transpose_toggle.setEnabled(bool(clip.semitones))
        self.play_key.refresh(track, clip)
        advice = pitch_advice(self.session, index, self.mix_review.pitch_limit.value())
        self.key_suggestion = advice["suggestion"]
        processed = djmix.transpose(parsed_key(track.key), clip.shift)
        suggestion = self.key_suggestion
        if clip.shift:
            text = (f'Mix: {display_key(processed)} ({clip.shift:+d} st).' if processed else
                    f'Mix: {clip.shift:+d} st; source key unknown.')
        else:
            text = "Mix uses original pitch."
        if not clip.transpose and clip.semitones:
            text = f"Off. {clip.semitones:+d} st remembered. " + text
        self.shift_hint.setText(text)
        self.shift_hint.setToolTip(advice["reason"] + " Based on song key labels; listen to judge melodies and vocals.")
        self.match_btn.setText(("Original key" if suggestion["semitones"] == 0 else f'Try {suggestion["semitones"]:+d} st')
                              if suggestion else "Match key")
        self.match_btn.setEnabled(bool(suggestion) and suggestion["semitones"] != clip.shift)
        self.match_btn.setVisible(self.match_btn.isEnabled())
        self.match_btn.setToolTip(advice["reason"] + " Advice uses both neighboring key labels where available. "
                                 "Apply a suggestion only if you want to try it, then listen. Mix review controls suggestion limits.")
        timing = track.timing
        from studio_timing import current_analysis as current_timing
        bpm_suggestion = current_timing(track).get("bpm")
        idle = not self.jobs
        guidance = timing_guidance(timing, clip.stabilize, not idle, track=track)
        self.timing_result.setText(guidance.title)
        self.timing_hint.setText(guidance.detail)
        for widget in (self.timing_result, self.timing_hint):
            widget.setToolTip(guidance.explanation)
            widget.setAccessibleDescription(guidance.explanation)
        self.stabilize.setChecked(clip.stabilize)
        self.stabilize.setEnabled((guidance.available or clip.stabilize) and idle)
        self.stabilize.setToolTip(("Turn off correction. " if clip.stabilize else "Turn on correction. " if guidance.available
                                  else "Correction unavailable. ") + guidance.explanation)
        self.stabilize.setAccessibleDescription(self.stabilize.toolTip())
        source_available = Path(track.path).is_file()
        self.timing_btn.setEnabled(idle and source_available)
        self.timing_btn.setToolTip('Check whether the tempo drifts across the whole song' if source_available else
                                   'Reconnect this song’s source audio before analyzing timing.')
        self.timing_btn.setAccessibleDescription(self.timing_btn.toolTip())
        can_compare = guidance.available and any(
            min(clip.cue_out, region["end"]) - max(clip.cue_in, region["start"]) >= 8
            for region in timing.get("regions", []))
        self.compare_timing_btn.setEnabled(can_compare)
        comparison_help = ("Compare up to 90 seconds at matched loudness: A uses fixed tempo; B adds stabilization. "
                           "Switch while it plays. This does not change the mix's Stabilize switch.")
        if not guidance.available:
            comparison_help = "Timing A/B is unavailable. " + guidance.explanation
        elif not can_compare:
            comparison_help = ("A/B needs at least 8 seconds of reliably detected drift within the selected cues. "
                               "Widen Start/End to include a supported passage, then compare.")
            self.timing_hint.setText(guidance.detail.split("\n")[0] + "\nA/B needs more drift inside these cues.")
        self.compare_timing_btn.setToolTip(comparison_help)
        self.compare_timing_btn.setAccessibleDescription(comparison_help)
        show_bpm = bpm_suggestion is not None and abs(bpm_suggestion - (track.bpm or 0)) > 1e-6
        self.use_bpm_btn.setVisible(show_bpm)
        self.use_bpm_btn.setEnabled(idle)
        if show_bpm:
            self.use_bpm_btn.setText(f"Use {bpm_suggestion:.3f}")
            self.use_bpm_btn.setToolTip("Apply the BPM measured across the whole song. Your cues stay as they are.")
        self.earlier.setEnabled(index > 0)
        self.later.setEnabled(index < len(clips) - 1)
        self.simple_song.update_song(track, clip, index)
        QTimer.singleShot(0, self.fit_song_settings)

    def refresh_actions(self):
        clips = len(self.session.clips)
        busy, rendering = bool(self.jobs), self.rendering
        clip = self.selected_clip()
        index = self.selected_index()
        for widget in (self.export_btn, self.render_btn, self.act_export, self.act_render):
            widget.setEnabled(clips > 0 and not rendering)
        for widget in (self.preview_btn, self.act_preview):
            widget.setEnabled(clips > 1 and not rendering)
        self.review_btn.setEnabled(clips > 1 and not rendering)
        self.edit_blend_btn.setEnabled(clips > 1 and not busy)
        self.align_beats_btn.setEnabled(clips > 1 and not busy)
        self.song_cues_btn.setEnabled(clip is not None and not busy)
        self.loop_btn.setEnabled(clip is not None and not busy)
        self.act_loop_extension.setEnabled(clip is not None and not busy)
        self.separation_btn.setEnabled(clip is not None and clips > 1 and not busy)
        self.act_separation.setEnabled(clip is not None and clips > 1 and not busy)
        self.protect_btn.setEnabled(clip is not None and not busy)
        self.refresh_review_strip()
        self.source_audition.setEnabled(not rendering)
        self.library_tools.update_selection()
        for widget in (self.import_btn, self.files_btn, self.act_import, self.act_files):
            widget.setEnabled(not self.importing and not rendering)
        for widget in (self.act_open, self.act_new, self.act_projects, self.projects_btn, self.act_relink, self.act_portable,
                       self.act_demo, self.demo_btn):
            widget.setEnabled(not busy)
        for widget in (self.guide_btn, self.act_guide):
            widget.setEnabled(not busy)
        self.tempo.setEnabled(not rendering and not self.session.tempo_enabled)
        self.tempo_curve_btn.setEnabled(not self.jobs)
        self.act_tempo_curve.setEnabled(not self.jobs)
        self.order_btn.setEnabled(clips > 1 and not busy)
        self.order_btn.setToolTip("Plan an order using key labels, nearby tempos, audio energy estimates and your own ratings. "
                                 "Choose an opener, closer, fixed positions or a shorter set; hear draft blends before applying.")
        self.order_btn.setAccessibleDescription(self.order_btn.toolTip())
        self.act_order.setEnabled(clips > 1 and not busy)
        self.act_finishing.setEnabled(clips > 0 and not busy)
        self.act_mastering.setEnabled(clips > 0 and not busy)
        self.mastering_btn.setEnabled(clips > 0 and not busy)
        self.mastering_btn.setText('Mastering: ' + ('On…' if self.session.mastering_enabled else 'Off…'))
        self.act_lessons.setEnabled(not busy)
        self.act_undo.setEnabled(bool(self.history) and not rendering)
        self.act_redo.setEnabled(bool(self.future) and not rendering)
        self.act_undo.setText("Undo " + self.history[-1].get("action", "Edit mix") if self.history else "Undo")
        self.act_redo.setText("Redo " + self.future[-1].get("action", "Edit mix") if self.future else "Redo")
        self.act_remove.setEnabled(clip is not None and not rendering)
        self.act_earlier.setEnabled(index > 0 and not rendering)
        self.act_later.setEnabled(0 <= index < clips - 1 and not rendering)
        self.act_prev.setEnabled(self.transition_index() > 1)
        self.act_next.setEnabled(0 < self.transition_index() < clips - 1)
        self.timeline.setEnabled(not rendering)
        self.play_btn.setEnabled(clips > 0 and not rendering)
        self.preview_btn.setToolTip("Select the incoming song or its numbered transition, then preview this pair with four bars "
                                    "on either side. Add at least two clips. After edits, preview again to hear the changes.")
        self.render_btn.setToolTip("Play the whole mix from the playhead. Audio is prepared automatically when needed. "
                                   "Export creates a shareable audio file; Save stores the editable project.")
        self.update_play_icon()

    def refresh_mix_review(self):
        report = review_mix(self.session, self.mix_review.tempo_limit.value(), self.mix_review.pitch_limit.value())
        self.mix_review.update_report(report, bool(self.jobs))
        self.library_tabs.setTabText(1, f'Mix review · {report["count"]}' if report["count"] else "Mix review")

    def change_suggestion_limits(self, percent, semitones):
        self.write_prefs(preferred_tempo_change=percent, suggested_pitch_limit=semitones)
        self.refresh()

    def review_action(self, action, identity, value):
        if self.jobs or self.updating:
            return
        if action == "tempo":
            advice = tempo_advice(self.session, self.mix_review.tempo_limit.value())
            if advice.get("can_apply") and abs(advice["target"] - value) < 1e-6:
                self.change_tempo(value)
            else:
                self.refresh_mix_review()
                self.say("The tempo advice changed. Review the current suggestion.")
            return
        index = next((i for i, clip in enumerate(self.session.clips) if clip.id == identity), None)
        if index is None:
            self.refresh_mix_review()
            return
        if action == "pitch":
            advice = pitch_advice(self.session, index, self.mix_review.pitch_limit.value())["suggestion"]
            if not advice or advice["semitones"] != value:
                self.refresh_mix_review()
                self.say("The pitch advice changed. Review the current suggestion.")
                return
        self.select_clip(identity)
        self.timeline.center_on(self.timeline.positions[index]["start"] / djmix.SR)
        if action == "pitch":
            self.set_transpose(int(value))
        elif action == "listen":
            self.player.pause()
            self.preview()
        elif action == "timing":
            self.compare_timing()
        elif action == 'relink':
            self.show_relink(self.session.clips[index].track_id)
        elif action == 'voices' and index > 0:
            self.edit_blend()
            if self.transition_editor is not None:
                editor = self.transition_editor
                editor.details_scroll.ensureWidgetVisible(editor.voices)
                from studio_overlap_content import overlap_voices
                if overlap_voices(self.session, index)['state'] == 'not_checked':
                    editor.check_voices()

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
            self.source_audition.stop()
            self.source_audition.output.setMuted(True)
            self.output_button.setIcon(T.icon("speaker", T.BAD, 16))
            self.output_button.setToolTip("Audio output unavailable")
            self.say("Audio output unavailable. Connect it or choose another output.")
            return
        self.audio.setMuted(False)
        self.source_audition.output.setMuted(False)
        if self.audio.device().id() != chosen.id():
            self.audio.setDevice(chosen)
        if self.source_audition.output.device().id() != chosen.id():
            self.source_audition.output.setDevice(chosen)
        self.output_button.setIcon(T.icon("speaker", T.TEXT_2, 16))
        self.output_button.setToolTip("Playing through " + chosen.description())
        self.output_button.setText(self.output_button.fontMetrics().elidedText(chosen.description(), Qt.TextElideMode.ElideRight, 145))
        self.output_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

    def change_audio_output(self, value):
        self.output_preference = value
        self.write_prefs(audio_output=value)
        self.refresh_audio_outputs()
        self.say(self.output_button.toolTip())

    # Background work -----------------------------------------------------------------------

    def job(self, function, finished, *, importing=False, rendering=False, cancellable=False, item=None,
            failed_callback=None, progress_callback=None, title=None, reason=None):
        cancel = threading.Event()
        worker = Worker(function, cancel, self.storage.work_lock(), hold_cache=True)
        worker.ui_title = title or ('Preparing audio' if rendering else 'Analyzing audio' if importing else 'Audio task')
        worker.ui_reason = reason or ''
        worker.ui_progress = 'Starting…'
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
            if failed_callback:
                failed_callback(message)
            elif message == "Operation cancelled.":
                self.say("Stopped.")
            else:
                self.notify(message, "error")

        def done():
            settle()
            worker.cache_lock.release()
            self.update_storage_pins()
            self.storage.prune()
            self.refresh()
            if not self.jobs and self.closing:
                self.close()

        worker.signals.progress.connect(lambda message: self.job_progress(worker, message))
        if progress_callback:
            worker.signals.progress.connect(progress_callback)
        worker.signals.item.connect(item or self.receive_track)
        worker.signals.result.connect(deliver)
        worker.signals.error.connect(failed)
        worker.signals.finished.connect(done)
        self.refresh()
        self.pool.start(worker)
        return cancel

    def update_job_flags(self):
        jobs = list(self.jobs.values())
        self.importing = any(j[1] for j in jobs)
        self.rendering = any(j[2] for j in jobs)
        downloading = bool(self.downloads_window and self.downloads_window.busy)
        self.job_area.setVisible(bool(jobs or self.band_workers or downloading))
        self.cancel_btn.setVisible(any(j[3] for j in jobs) or downloading)
        self.update_job_banner()

    def job_progress(self, worker, message):
        if worker in self.jobs:
            worker.ui_progress = plain(message)
            self.update_job_banner()

    def update_job_banner(self):
        if self.jobs:
            worker = next((w for w, flags in self.jobs.items() if flags[2]), next(iter(self.jobs)))
            stopping = self.jobs[worker][0].is_set()
            count = f' · {len(self.jobs)} tasks' if len(self.jobs) > 1 else ''
            self.job_title.setText(worker.ui_title + count)
            self.job_reason.setText(worker.ui_reason)
            self.job_reason.setVisible(bool(worker.ui_reason))
            self.job_text.set_text('Stopping safely; keeping completed cached audio…' if stopping else worker.ui_progress)
        elif self.band_workers:
            self.job_title.setText('Loading waveforms')
            self.job_reason.hide()
            self.job_text.set_text(f'Preparing visual waveforms for {len(self.band_pending)} song(s). Mix audio is unchanged.')
        elif self.downloads_window and self.downloads_window.busy:
            self.job_title.setText('Downloading optional features')
            self.job_reason.setText('Mix audio is unchanged. File → Downloads & features shows details.')
            self.job_reason.show()
            self.job_text.set_text(self.downloads_window.banner)

    def cancel_job(self):
        for cancel, _, _, cancellable in self.jobs.values():
            if cancellable:
                cancel.set()
        if self.downloads_window and self.downloads_window.busy:
            self.downloads_window.request_cancel()
        self.update_job_banner()

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
                    emit((identity, path, band_envelope(Path(path), cache, identity)))
                except Exception:
                    emit((identity, path, None))

        worker = Worker(work, cancel, self.storage.work_lock())
        self.band_workers.add(worker)
        self.update_job_flags()
        worker.signals.item.connect(self.receive_bands)
        def finished():
            self.band_workers.discard(worker)
            self.update_job_flags()
            self.storage.prune()
            if self.closing and not self.jobs and not self.band_workers:
                self.close()
        worker.signals.finished.connect(finished)
        self.pool.start(worker)

    def receive_bands(self, payload):
        identity, path, bands = payload
        current = self.session.tracks.get(identity)
        if current is None or current.path != path:
            return
        self.band_pending.discard(identity)
        self.update_job_banner()
        if bands is None or not len(bands):
            self.band_failed.add(identity)
            return
        self.bands[identity] = bands
        self.timeline.invalidate()
        self.overview.invalidate()
        self.cue_editor.update()
        self.preview_strip.update()
        if self.transition_editor is not None and self.transition_editor.isVisible():
            self.transition_editor.view.update()
        if self.cue_review is not None and self.cue_review.isVisible():
            self.cue_review.wave.update()

    # Library -------------------------------------------------------------------------------

    def receive_track(self, track):
        if track.id in self.touched and track.id in self.session.tracks:
            self.session.tracks[track.id].waveform = track.waveform
            if self.session.tracks[track.id].path == track.path:
                self.session.tracks[track.id].fingerprint = track.fingerprint
        else:
            self.session.tracks[track.id] = track
        self.dirty = True
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

    def import_paths(self, paths, *, finished_callback=None, failed_callback=None,
                     progress_callback=None, cancellable=False):
        if not paths:
            self.notify("No audio files found there. BeatStitch reads WAV, AIFF, FLAC, MP3, M4A, AAC and OGG.", "error")
            return
        if self.importing or self.rendering:
            self.say("Wait for the current import or render to finish.")
            return
        existing = copy.deepcopy(self.session.tracks)
        cache, timing_cache = self.data / "library-cache", self.data / "timing-cache"
        duplicate_names = []

        def work(progress, emit):
            from studio_cache import source_signature
            from studio_library import duplicate_fingerprint, duplicate_track
            from studio_source_identity import fingerprint, valid_fingerprint
            pending, failures = [], []
            known = dict(existing)
            seen_paths = set()
            imported = 0
            for path in paths:
                progress(f"Importing {path.name}")
                try:
                    resolved = str(path.resolve())
                    if resolved in seen_paths:
                        duplicate_names.append(path.name)
                        continue
                    seen_paths.add(resolved)
                    prior = next((t for t in known.values() if str(Path(t.path).resolve()) == resolved), None)
                    if prior and valid_fingerprint(prior.fingerprint) and source_signature(path) == prior.fingerprint['source']:
                        duplicate_names.append(path.name)
                        continue
                    verified = None
                    if any(valid_fingerprint(t.fingerprint) and t.fingerprint['source'][1] == path.stat().st_size for t in known.values()):
                        verified = fingerprint(path, progress)
                        if duplicate_fingerprint(verified, known):
                            duplicate_names.append(path.name)
                            continue
                    track = load_track(path, cache, progress=progress, verified=verified)
                    same = duplicate_track(track, known)
                    if same:
                        duplicate_names.append(path.name)
                        continue
                    if prior and valid_fingerprint(prior.fingerprint):
                        raise djmix.MixError('This source file has changed. Use Reconnect source audio to review the replacement before changing its mix clips.')
                    if prior and prior.bpm:
                        prior.waveform, prior.fingerprint = track.waveform, track.fingerprint
                        track = prior
                    known[track.id] = track
                    imported += 1
                    emit(copy.deepcopy(track))
                    if not track.bpm:
                        pending.append(track)
                    else:
                        cache_track(track, cache)
                except Exception as error:
                    if str(error) == 'Operation cancelled.':
                        raise
                    failures.append(f"{path.name}: {error}")
            for index, track in enumerate(pending):
                progress(f"Estimating BPM and key for {track.name} ({index + 1} of {len(pending)})")
                try:
                    track = estimate_track(track, timing_cache, progress)
                    cache_track(track, cache)
                except Exception as error:
                    if str(error) == 'Operation cancelled.':
                        raise
                    track.status = f"Needs manual analysis: {error}"
                emit(copy.deepcopy(track))
            return imported, failures

        def finished(result):
            imported, failures = result
            summary = f"Added {imported} {'song' if imported == 1 else 'songs'}."
            if duplicate_names:
                summary += f' Skipped {len(duplicate_names)} already listed or identical file(s); existing songs and your edits were kept.'
            if failures:
                self.notify(summary + ' Some files could not be imported. ' + '; '.join(failures), 'error')
            else:
                self.say(summary + ' Select songs, then Add selected to mix or drag them onto the arrangement.')
            self.load_bands()
            if finished_callback:
                finished_callback(result)

        return self.job(work, finished, importing=True, cancellable=cancellable,
                        failed_callback=failed_callback, progress_callback=progress_callback)

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
        if not self.library.selectionModel().isRowSelected(index.row()):
            self.library.selectRow(index.row())
        identity = self.library.current_id()
        track = self.session.tracks[identity]
        single = len(self.library.selected_ids()) == 1
        arranged = any(c.track_id == identity for c in self.session.clips)
        menu = QMenu(self)
        add = menu.addAction(f'Add {len(self.library.selected_ids())} selected song(s) to the end of the mix')
        add.setEnabled(not self.rendering and not self.importing)
        analyze = menu.addAction("Analyze BPM and key again")
        analyze.setEnabled(single and not arranged and not self.importing and not self.rendering)
        check_key = menu.addAction('Check key (keep current label)')
        check_key.setEnabled(single and not self.jobs and Path(track.path).is_file())
        use_key = menu.addAction('Use estimated key')
        from studio_key import current_analysis as current_key_analysis
        key_result = current_key_analysis(track)
        use_key.setEnabled(single and not self.jobs and key_result.get('state') == 'estimated'
                           and (track.key_origin != 'detected' or track.key != key_result['candidates'][0]['key']))
        relink = menu.addAction('Reconnect this source audio…')
        relink.setEnabled(single and not self.jobs)
        menu.addSeparator()
        reveal = menu.addAction(REVEAL)
        reveal.setEnabled(single)
        chosen = menu.exec(self.library.viewport().mapToGlobal(point))
        if chosen is add:
            self.add_tracks(self.library.selected_ids(), len(self.session.clips))
        elif chosen is analyze:
            self.analyze_selected()
        elif chosen is check_key:
            self.check_selected_key(identity)
        elif chosen is use_key:
            self.use_estimated_key(identity)
        elif chosen is relink:
            self.show_relink(identity)
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
        return self.add_tracks([identity], index)

    def add_tracks(self, identities, index):
        if self.rendering or self.importing:
            return
        identities = list(dict.fromkeys(identities))
        if not identities or any(identity not in self.session.tracks for identity in identities):
            return
        tracks = [self.session.tracks[identity] for identity in identities]
        missing = [track for track in tracks if not track.bpm]
        manual_bpm = None
        if missing and len(tracks) > 1:
            self.notify('No songs added. Enter a BPM for ' + ', '.join(t.name for t in missing) +
                        ' first: add it alone, or use Mix guide to enter the missing tempos.', 'error')
            return
        if missing:
            track = missing[0]
            value, ok = QInputDialog.getDouble(self, "Song tempo", f"Enter the BPM of {track.name} to add it to the mix:",
                                               128, 20, 300, 3)
            if not ok:
                return
            manual_bpm = value
        clips = [Clip(t.id, t.cue_in, t.cue_out or t.duration) for t in tracks]
        def mutate():
            if manual_bpm is not None:
                tracks[0].bpm = manual_bpm
                tracks[0].status = 'BPM entered manually'
            position = max(0, min(index, len(self.session.clips)))
            self.session.clips[position:position] = clips
        action = f'Add {tracks[0].name}' if len(tracks) == 1 else f'Add {len(tracks)} songs to mix'
        if self.edit(mutate, action=action):
            if manual_bpm is not None: self.touched.add(tracks[0].id)
            self.selected_id = clips[-1].id
            self.refresh()
            self.timeline.fit()
            self.load_bands()

    def select_clip(self, identity):
        self.selected_id = identity
        if self.reviewing and self.transition_index() != self.listening_review.index(self.session):
            self.end_review()
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

        if self.edit(mutate, action="Move song in mix"):
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
        if self.edit(lambda: self.session.clips.remove(clip), action=f"Remove {name}"):
            clips = self.session.clips
            self.selected_id = clips[min(index, len(clips) - 1)].id if clips else ""
            self.refresh()
            self.notify(f"Removed {name} from the mix.", "info", [("Undo", self.undo)])

    def set_cues(self, cue_in, cue_out):
        clip = self.selected_clip()
        if clip:
            self.edit(lambda: (setattr(clip, "cue_in", cue_in), setattr(clip, "cue_out", cue_out)), action="Change song cues")

    def set_clip_value(self, name, value):
        clip = self.selected_clip()
        if clip and not self.updating and getattr(clip, name) != value:
            if name == 'stabilize' and value:
                track = self.session.tracks[clip.track_id]
                guidance = timing_guidance(track.timing, track=track)
                if not guidance.available:
                    self.say(guidance.explanation); self.refresh(); return
            action = {"cue_in": "Change song start", "cue_out": "Change song end", "transpose": "Toggle pitch shift",
                      "stabilize": "Toggle tempo correction"}.get(name, "Change song setting")
            self.edit(lambda: setattr(clip, name, value), action=action)

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

        if self.edit(mutate, action=f"Change {track.name} source BPM"):
            self.touched.add(track.id)
            self.say("BPM changed. Preview again to hear it.")

    def set_key(self):
        clip = self.selected_clip()
        if self.updating or not clip:
            return
        track = self.session.tracks[clip.track_id]
        key = self.key.currentData() or ""
        if key == track.key and track.key_origin == 'manual':
            return

        def mutate():
            from studio_key import set_manual_key
            set_manual_key(track, key)
            track.status, track.estimated = "Key edited manually", False

        if self.edit(mutate, action=f"Change {track.name} key label"):
            self.touched.add(track.id)
            self.say('Source key label corrected; audio pitch is unchanged. Choose Play in key to change what you hear.')

    def check_selected_key(self, identity=None):
        from studio_key import analyze_key, remember_analysis
        from studio_cache import source_signature
        clip = self.selected_clip()
        if not isinstance(identity, str):
            identity = clip.track_id if clip else self.library.current_id()
        if self.jobs or identity not in self.session.tracks:
            return
        if not self.require_models(['key'], lambda: self.check_selected_key(identity)):
            return
        track = copy.deepcopy(self.session.tracks[identity])

        def work(progress, emit):
            return analyze_key(Path(track.path), self.data / 'library-cache' / 'keys', progress)

        def finished(result):
            current = self.session.tracks.get(identity)
            try:
                if not current or result['source'] != source_signature(current.path):
                    raise djmix.MixError('The source changed. Check its key again before accepting an estimate.')
                if self.edit(lambda: remember_analysis(current, result), force=True,
                             action=f'Check {current.name} key'):
                    self.touched.add(identity)
                if result['state'] == 'estimated':
                    self.say(f'Detected {result["candidates"][0]["key"]}. Current label kept. Choose Restore detected to accept it.')
                else:
                    self.say(result['reason'])
            except (djmix.MixError, OSError) as error:
                self.notify(str(error), 'error')

        self.job(work, finished, importing=True, cancellable=True, title='Detecting the source key',
                 reason='Checking the original recording. Your source label and pitch choice stay unchanged.')

    def use_estimated_key(self, identity=None):
        from studio_key import accept_estimate
        clip = self.selected_clip()
        if not isinstance(identity, str):
            identity = clip.track_id if clip else self.library.current_id()
        if self.jobs or identity not in self.session.tracks:
            return
        track = self.session.tracks[identity]
        if self.edit(lambda: accept_estimate(track), action=f'Use {track.name} estimated key'):
            self.touched.add(identity)
            self.say('Estimated key label accepted. Audio pitch is unchanged; preview your blends to check it.')

    def set_transpose(self, semitones):
        clip = self.selected_clip()
        if self.updating or not clip or (clip.semitones == semitones and clip.transpose == (semitones != 0)):
            return
        if self.edit(lambda: (setattr(clip, "semitones", int(semitones)), setattr(clip, "transpose", semitones != 0)),
                     action="Change song pitch"):
            self.say('Pitch choice saved. Click Listen to blend or Play mix to prepare and hear it. Current playback and the original recording are unchanged.')

    def listen_pitch(self):
        if len(self.session.clips) > 1:
            self.preview()
        elif self.selected_clip():
            self.mix_cursor = 0
            self.render_full()

    def show_compatible_songs(self):
        # Make the complete set visible; an old search or collection must not hide matches.
        self.library_tabs.setCurrentIndex(0)
        self.search.clear()
        self.library_tools.collection.setCurrentIndex(0)
        self.library_tools.filter.setCurrentIndex(self.library_tools.filter.findData('key'))
        self.refresh_library()
        self.library.setFocus()
        self.say('Showing key-compatible library songs at their original pitch. Select a song to see why it fits; use All songs to clear the filter.')

    def match_key(self):
        if self.key_suggestion:
            self.set_transpose(self.key_suggestion["semitones"])

    def set_stabilize(self, checked):
        if self.updating:
            return
        self.set_clip_value("stabilize", bool(checked))
        self.refresh()

    def set_transition_value(self, side, name, value):
        index = self.transition_index()
        if self.updating or not index:
            return
        if name == "blend_bars":
            limit = overlap_limit(self.session, index)
            if not 1 <= value <= limit["bars"]:
                detail = (f'Keep protected “{limit["protected"]}” in {limit["song"]} clear of the blend.' if limit.get('protected') else
                          f'Widen {limit["song"]} or choose a shorter overlap.')
                self.notify(f'This transition supports 1–{limit["bars"]} bars with the current cues. ' + detail, "error")
                self.refresh()
                return
        clip = self.session.clips[index if side == "in" else index - 1]
        if getattr(clip, name) != value:
            field = {"blend_bars": "overlap length", "bass_swap": "bass swap",
                     "outro_percussive": "drum outro label", "intro_percussive": "drum intro label"}.get(name, name)
            self.edit(lambda: setattr(clip, name, value), action=f"Change {index} → {index + 1} {field}")
        self.refresh()

    def drag_overlap(self, index, bars):
        if not self.rendering and 0 < index < len(self.session.clips):
            self.select_clip(self.session.clips[index].id)
            self.set_transition_value("in", "blend_bars", bars)

    def reset_transition(self):
        index = self.transition_index()
        if self.rendering or not index:
            return
        outgoing, incoming = self.session.clips[index - 1:index + 1]
        bars = min(8, overlap_limit(self.session, index)["bars"])

        def mutate():
            incoming.blend_bars, incoming.bass_swap = bars, True
            incoming.balance_levels = False
            incoming.transition_effect = 'none'
            outgoing.outro_percussive = incoming.intro_percussive = False

        self.edit(mutate, action=f"Reset transition {index} → {index + 1}")

    def change_tempo(self, value=None):
        value = self.tempo.value() if value is None else value
        if self.session.tempo_enabled:
            self.say('The tempo curve controls playback speed. Open Tempo curve to edit it or switch it off first.')
            return
        if self.updating or self.rendering or value == self.session.target_bpm:
            return
        self.edit(lambda: setattr(self.session, "target_bpm", value), action="Change mix tempo")

    def show_tempo_curve(self):
        if self.jobs: return
        from studio_tempo_ui import TempoDialog
        self.sync_position(); self.player.pause(); self.source_audition.stop(); self.end_review()
        dialog = TempoDialog(self.session, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            enabled, points = dialog.draft.tempo_enabled, copy.deepcopy(dialog.draft.tempo_points)
            def mutate():
                self.session.tempo_enabled, self.session.tempo_points = enabled, points
            self.edit(mutate, action='Change set tempo curve')
        dialog.deleteLater()

    def start_mix_guide(self):
        if self.jobs:
            self.say('Wait for the current job to finish before starting the Mix guide.')
            return
        if self.guide_dialog is not None:
            self.guide_dialog.raise_()
            return
        from studio_first_mix import FirstMixGuide
        self.stop()
        self.source_audition.stop()
        self.guide_dialog = FirstMixGuide(self)
        self.guide_dialog.finished.connect(lambda _: setattr(self, 'guide_dialog', None))
        self.guide_dialog.open()

    def propose_order(self):
        if self.jobs or len(self.session.clips) < 2:
            return
        from studio_order_dialog import OrderDialog
        self.stop()
        revision = self.revision
        def apply_draft(draft):
            if revision != self.revision:
                self.order_dialog.status.setText('The mix changed. Close and reopen the planner before applying an order.')
                return False
            def mutate():
                self.session.clips = copy.deepcopy(draft.clips)
                self.session.planning = copy.deepcopy(draft.planning)
            already_matches = self.session.clips == draft.clips and self.session.planning == draft.planning
            changed = self.edit(mutate, action='Apply song order plan')
            if not changed and not already_matches:
                return False
            if not self.selected_clip():
                self.selected_id = self.session.clips[0].id
                self.refresh()
            self.timeline.fit()
            self.say('Plan applied. Review the new blends before exporting.' if changed else 'The mix already matches this plan.')
            return True
        self.order_dialog = OrderDialog(self.session, self.bands, self.data / 'audio-cache', self.output_preference,
                                        self.volume.value() / 100, self.job, apply_draft, self)
        self.order_dialog.finished.connect(lambda _: setattr(self, 'order_dialog', None))
        self.order_dialog.open()

    # Timing --------------------------------------------------------------------------------

    def analyze_timing(self):
        if self.jobs:
            return
        clip = self.selected_clip()
        if not clip:
            return
        track = copy.deepcopy(self.session.tracks[clip.track_id])
        from studio_timing import analyze, source_matches
        if not Path(track.path).is_file():
            self.say('Reconnect this song’s source audio before analyzing timing.'); self.refresh(); return

        def work(progress, emit):
            return analyze(Path(track.path), track.bpm, track.duration, self.data / "timing-cache", progress)

        def finished(timing):
            current = self.session.tracks.get(track.id)
            if current is None:
                return
            if (current.path != track.path or current.bpm != track.bpm or current.duration != track.duration
                    or not source_matches(timing, current)):
                self.say('The song changed while timing was being checked. Analyze again; no timing result was applied.')
                self.refresh()
                return

            def mutate():
                current.timing = timing
                if timing["state"] != "drift":
                    for item in self.session.clips:
                        if item.track_id == track.id:
                            item.stabilize = False

            if self.edit(mutate, force=True, action=f"Analyze {track.name} timing"):
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
        from studio_timing import current_analysis as current_timing
        suggestion = current_timing(track).get("bpm")
        if suggestion is None:
            self.say('No current timing BPM suggestion. Reconnect missing audio or Analyze timing again.')
            self.refresh()
            return

        def mutate():
            track.bpm = suggestion
            track.status = "BPM from whole-song timing analysis, cues kept"

        if self.edit(mutate, action=f"Use suggested BPM for {track.name}"):
            self.touched.add(track.id)
            cache_track(track, self.data / "library-cache")

    def compare_timing(self):
        if self.jobs:
            return
        clip = self.selected_clip()
        if not clip:
            return
        track = self.session.tracks[clip.track_id]
        guidance = timing_guidance(track.timing, track=track)
        if not guidance.available:
            self.say(guidance.explanation); self.refresh(); return
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

    def review_song_cues(self, protect=False):
        clip = self.selected_clip()
        if clip is None or self.jobs:
            return
        from studio_cache import source_signature
        from studio_cue_review import CueReview
        self.sync_position()
        self.player.pause()
        self.source_audition.stop()
        self.pending_playback = None
        self.end_review()
        revision, identity = self.revision, clip.id
        track_id, source_path = clip.track_id, self.session.tracks[clip.track_id].path
        index = self.session.clips.index(clip)

        def save_analysis(result):
            current = self.session.tracks.get(track_id)
            if (current is None or current.path != source_path or current.bpm != result['source_bpm']
                    or source_signature(current.path) != result['source']):
                raise djmix.MixError('The song changed. Close and analyze the current version.')
            current.structure = result
            cache_track(current, self.data / 'library-cache')
            # Computed annotations do not change any audio ingredients.
            self.dirty = True
            self.autosave_timer.start()
            self.cue_editor.update()

        try:
            original_source = source_signature(source_path)
        except OSError:
            self.notify('Cannot find this source song. Relink or re-import its audio before checking beats and cues.', 'error')
            return

        def apply_cues(start, end, bpm, beat_grid, structure, protection):
            if revision != self.revision or index >= len(self.session.clips) or self.session.clips[index].id != identity:
                self.cue_review.status.setText('The mix changed. Reopen this song to edit its current cues.')
                return False
            current = self.session.clips[index]
            try:
                if source_signature(source_path) != original_source:
                    raise djmix.MixError('The source changed on disk. Close and reopen this song before applying.')
            except (OSError, djmix.MixError) as error:
                self.cue_review.status.setText(str(error))
                return False
            track = self.session.tracks[track_id]
            grid_changed = track.bpm != bpm or track.beat_grid != beat_grid
            protection_changed = current.protected_moments != protection['moments']

            def mutate():
                current.cue_in, current.cue_out = start, end
                current.protected_moments = copy.deepcopy(protection['moments'])
                current.intro_percussive, current.outro_percussive = protection.get('drums', (current.intro_percussive, current.outro_percussive))
                for other in self.session.clips[max(0, index):index + 2]:
                    if other.id in protection['overlaps']:
                        other.blend_bars = protection['overlaps'][other.id]
                if track.bpm != bpm:
                    track.bpm = bpm
                    track.status, track.estimated = 'BPM edited manually', False
                track.beat_grid = copy.deepcopy(beat_grid)

            changed = self.edit(mutate, action=f'Apply {"protected passages and cues" if protection_changed else "beats and cues" if grid_changed else "cues and overlaps" if protection["overlaps"] else "musical cues"} for {track.name}')
            if changed and structure.get('source_bpm') == bpm and structure.get('source') == original_source:
                track.structure = copy.deepcopy(structure)
                self.autosave()
            return changed

        def save_content(result):
            current = self.session.tracks.get(track_id)
            if (current is None or current.path != source_path or result['source'] != original_source
                    or source_signature(current.path) != original_source):
                raise djmix.MixError('The source song changed. Close and analyze the current version.')
            current.content = copy.deepcopy(result)
            cache_track(current, self.data / 'library-cache')
            self.dirty = True
            self.autosave_timer.start()

        def save_sections(result):
            from studio_song_sections import valid_result
            current = self.session.tracks.get(track_id)
            if (current is None or current.path != source_path or result.get('source') != original_source
                    or source_signature(current.path) != original_source or not valid_result(result, current.duration)):
                raise djmix.MixError('The source song changed. Close and analyze the current version.')
            current.song_sections = copy.deepcopy(result)
            cache_track(current, self.data / 'library-cache')
            self.dirty = True
            self.autosave_timer.start()

        try:
            self.cue_review = CueReview(self.session, index, self.bands, self.data / 'structure-cache',
                                       self.output_preference, self.volume.value() / 100,
                                       self.job, save_analysis, apply_cues, self, protect=protect, save_content=save_content,
                                       save_sections=save_sections)
        except Exception as error:
            self.notify(f'Could not open song cues: {error}', 'error')
            return
        self.cue_review.finished.connect(lambda _: setattr(self, 'cue_review', None))
        self.cue_review.open()

    def show_lessons(self):
        if self.jobs: return
        if self.lessons_dialog is not None:
            self.lessons_dialog.raise_(); return
        from studio_lessons_ui import ListeningLessons
        self.sync_position(); self.player.pause(); self.source_audition.stop(); self.end_review()
        self.pending_playback = None
        if self.help_window is not None: self.help_window.hide()
        self.lessons_dialog = ListeningLessons(self)
        self.lessons_dialog.finished.connect(lambda _: setattr(self, 'lessons_dialog', None))
        self.lessons_dialog.open()

    def show_loop_extension(self):
        if self.jobs or self.selected_index() < 0: return
        if self.loop_dialog is not None:
            self.loop_dialog.raise_(); return
        from studio_loop_ui import LoopExtensionDialog
        self.sync_position(); self.player.pause(); self.source_audition.stop(); self.end_review()
        self.pending_playback = None
        self.loop_dialog = LoopExtensionDialog(self)
        self.loop_dialog.finished.connect(lambda _: setattr(self, 'loop_dialog', None))
        self.loop_dialog.open()

    def apply_loop_extension(self, baseline, extension, replacement):
        from studio_cache import source_signature
        from studio_loop_extension import draft_with_copy
        if self.session.to_dict() != baseline.to_dict():
            raise djmix.MixError('The mix changed. Close and reopen Extend drums before applying.')
        index = extension['index']; original = baseline.tracks[baseline.clips[index].track_id]
        if source_signature(original.path) != extension['source']:
            raise djmix.MixError('The original song changed. Reopen Extend drums and prepare it again.')
        replacement = copy.deepcopy(replacement)
        if replacement.id in self.session.tracks and self.session.tracks[replacement.id] != replacement:
            # The same stored audio may already have user-edited metadata or
            # appear elsewhere in the mix. Give this copy its own track entry.
            import uuid
            replacement.id = uuid.uuid4().hex
        draft = draft_with_copy(self.session, index, extension['plan'], replacement)
        def mutate():
            self.session.tracks[replacement.id] = replacement
            self.session.clips = draft.clips
            self.session.collections = draft.collections
        if not self.edit(mutate, action='Extend drum passage'): return False
        # The project now owns the durable source. A disposable library cache
        # failure must not turn a successful Apply into an apparent failure.
        try: cache_track(replacement, self.data / 'library-cache')
        except OSError: pass
        self.load_bands(); self.timeline.fit()
        self.say('Extended copy applied. Undo restores the original clip; both recordings remain in the library. Listen to both neighboring blends before export.')
        return True

    def show_separation(self):
        if self.jobs or self.selected_index() < 0 or len(self.session.clips) < 2: return
        if not self.require_models(['separation'], self.show_separation): return
        if self.separation_dialog is not None:
            self.separation_dialog.raise_(); return
        from studio_separation_ui import SeparationDialog
        self.sync_position(); self.player.pause(); self.source_audition.stop(); self.end_review()
        self.pending_playback = None
        self.separation_dialog = SeparationDialog(self)
        self.separation_dialog.finished.connect(lambda _: setattr(self, 'separation_dialog', None))
        self.separation_dialog.open()

    def apply_separation(self, baseline, result, replacement):
        from studio_cache import source_signature
        from studio_separation import draft_with_copy
        if self.session.to_dict() != baseline.to_dict():
            raise djmix.MixError('The mix changed. Close and reopen Reduce competing parts before applying.')
        original = baseline.tracks[baseline.clips[result['index']].track_id]
        if source_signature(original.path) != result['source']:
            raise djmix.MixError('The original song changed. Prepare its separated parts again.')
        replacement = copy.deepcopy(replacement)
        if replacement.id in self.session.tracks and self.session.tracks[replacement.id] != replacement:
            import uuid
            replacement.id = uuid.uuid4().hex
        draft = draft_with_copy(self.session, result['index'], replacement)
        def mutate():
            self.session.tracks[replacement.id] = replacement
            self.session.clips = draft.clips; self.session.collections = draft.collections
        if not self.edit(mutate, action='Reduce competing musical parts'): return False
        try: cache_track(replacement, self.data / 'library-cache')
        except OSError: pass
        self.load_bands()
        self.say('Separated copy applied. Undo restores the original clip; both recordings remain in the library. Check the full song and its neighboring blends before export.')
        return True

    def show_finishing(self):
        if self.jobs or not self.session.clips: return
        if self.finishing_dialog is not None:
            self.finishing_dialog.raise_(); return
        from studio_finishing_ui import FinishingDialog
        self.sync_position(); self.player.pause(); self.source_audition.stop(); self.end_review()
        self.finishing_dialog = FinishingDialog(self)
        self.finishing_dialog.finished.connect(lambda _: setattr(self, 'finishing_dialog', None))
        self.finishing_dialog.open()

    def show_mastering(self):
        if self.jobs or not self.session.clips: return
        if self.mastering_dialog is not None:
            self.mastering_dialog.raise_(); return
        from studio_mastering_ui import MasteringDialog
        self.stop(); self.source_audition.stop()
        self.mastering_dialog = MasteringDialog(self)
        self.mastering_dialog.finished.connect(lambda _: setattr(self, 'mastering_dialog', None))
        self.mastering_dialog.open()

    def align_selected_blend(self):
        self.edit_blend()
        dialog = self.transition_editor
        if dialog is not None:
            dialog.focus_alignment()
            QTimer.singleShot(0, dialog.alignment.align)

    def edit_blend(self):
        index = self.transition_index()
        if not index or self.jobs:
            return
        from studio_transition_editor import TransitionEditor
        from studio_cache import source_signature
        self.sync_position()
        self.player.pause()
        self.source_audition.stop()
        self.pending_playback = None
        review_pair = self.listening_review.current if self.reviewing else None
        revision, identity = self.revision, self.session.clips[index].id

        sources = {}
        for item in self.session.clips[index - 1:index + 1]:
            track = self.session.tracks[item.track_id]
            try: sources[track.id] = source_signature(track.path)
            except OSError: sources[track.id] = None

        def apply_draft(draft):
            if revision != self.revision or self.session.clips[index].id != identity:
                self.transition_editor.status.setText('The mix changed. Close and reopen this blend to edit the current version.')
                return False
            clip = self.session.clips[index]
            if draft.cue_in != clip.cue_in:
                try:
                    for track_id, signature in sources.items():
                        track = self.session.tracks[track_id]
                        if signature is None or source_signature(track.path) != signature:
                            raise djmix.MixError('A source changed on disk. Reopen this blend before applying its new cue.')
                except (OSError, djmix.MixError) as error:
                    self.transition_editor.status.setText(str(error)); return False
            from studio_blend_choices import DRAFT_FIELDS
            return self.edit(lambda: [setattr(clip, field, getattr(draft, field)) for field in DRAFT_FIELDS],
                             action=f'Edit {index} → {index + 1} blend')

        def save_content(results):
            for track_id, result in results.items():
                track = self.session.tracks.get(track_id)
                if track is None or result['source'] != sources.get(track_id) or source_signature(track.path) != result['source']:
                    raise djmix.MixError('A source song changed. Reopen this blend to check its current version.')
            for track_id, result in results.items():
                track = self.session.tracks[track_id]; track.content = copy.deepcopy(result)
                cache_track(track, self.data / 'library-cache')
            if results:
                self.dirty = True; self.autosave_timer.start()

        self.transition_editor = TransitionEditor(self.session, index, self.bands, self.data / 'audio-cache',
                                                  self.output_preference, self.volume.value() / 100,
                                                  self.job, apply_draft, self, max_shift=int(self.mix_review.pitch_limit.value()),
                                                  save_content=save_content)
        self.transition_editor.finished.connect(lambda _: setattr(self, 'transition_editor', None))
        if review_pair is not None:
            def return_to_review(_):
                if not self.closing and self.reviewing and self.listening_review.current == review_pair:
                    # The editor applies through edit(), which invalidates changed listening choices.
                    # Prepare the current sound again before Keep can be used.
                    QTimer.singleShot(0, lambda: self.preview(review=True) if self.reviewing and not self.closing else None)
            self.transition_editor.finished.connect(return_to_review)
        self.transition_editor.open()

    # Projects ------------------------------------------------------------------------------

    def invalidate_source_display(self, identity):
        self.bands.pop(identity, None)
        self.band_pending.discard(identity); self.band_failed.discard(identity)
        self.source_audition.stop(); self.source_audition.set_track(None)

    def show_relink(self, identity=None):
        if self.jobs: return
        from studio_relink_ui import RelinkDialog
        self.sync_position(); self.player.pause(); self.source_audition.stop()
        self.pending_playback = None; self.end_review()
        self.relink_dialog = RelinkDialog(self, identity)
        self.relink_dialog.finished.connect(lambda _: setattr(self, 'relink_dialog', None))
        self.relink_dialog.open()

    def show_portable(self):
        if self.jobs: return
        from studio_portable_ui import PortableDialog
        self.portable_dialog = PortableDialog(self)
        self.portable_dialog.finished.connect(lambda _: setattr(self, 'portable_dialog', None))
        self.portable_dialog.open()

    def apply_relink(self, result, revision):
        from studio_cache import source_signature
        self.relink_error = ''
        identity = result['identity']
        current = self.session.tracks.get(identity)
        if self.jobs or self.revision != revision or current is None or current.path != result['original_path']:
            self.relink_error = 'The mix changed. Reopen the source reconnection dialog to review its current settings.'
            return False
        try:
            if source_signature(result['draft'].tracks[identity].path) != result['fingerprint']['source']:
                raise djmix.MixError('The chosen audio changed after verification. Choose it again.')
        except (OSError, djmix.MixError) as error:
            self.relink_error = str(error); return False
        def mutate():
            self.session.tracks[identity] = copy.deepcopy(result['draft'].tracks[identity])
            for clip in self.session.clips:
                if clip.id in result['reset_correction']: clip.stabilize = False
        changed = self.edit(mutate, action='Reconnect audio for ' + current.name)
        if changed:
            self.invalidate_source_display(identity); self.touched.add(identity)
            self.load_bands(); self.refresh()
            self.say('Source audio reconnected. Clip edits are preserved; Undo restores the previous location.')
        else:
            self.relink_error = 'No valid change was applied. Check the current mix message and choose a different location if needed.'
        return changed

    def project_status_text(self):
        location = str(self.project_path) if self.project_path else 'No project file yet. Use Save to choose a location.'
        state = 'Changes not saved to the project file.' if self.project_path and self.dirty else 'Project file is saved.' if self.project_path else 'This mix has not been saved as a project.'
        safety = f'Autosave failed: {self.autosave_error}' if self.autosave_error else (
            'Autosaved ' + time.strftime('%d %b %Y, %H:%M:%S', time.localtime(self.autosave_time)) if self.autosave_time else 'No autosave yet.')
        return f'{state}\n{safety}\n{location}'

    def refresh_project_status(self):
        if not hasattr(self, 'save_state'): return
        state = 'Edited' if self.project_path and self.dirty else 'Saved' if self.project_path else 'Not saved'
        safety = 'Autosave failed' if self.autosave_error else (
            'Autosaved ' + time.strftime('%H:%M', time.localtime(self.autosave_time)) if self.autosave_time else 'No autosave yet')
        self.save_state.setText(f'{state} · {safety}')
        self.save_state.setToolTip(self.project_status_text())
        self.save_state.setAccessibleName(f'Project save state: {state}. {safety}')
        self.save_state.setAccessibleDescription(self.project_status_text())

    def remember_project(self, path):
        from studio_projects import remember_path
        self.write_prefs(last_project=str(path.resolve()), recent_projects=remember_path(self.prefs, path))

    def show_projects(self):
        if self.jobs: return
        if self.projects_dialog is not None:
            self.projects_dialog.raise_(); return
        from studio_projects_ui import ProjectsDialog
        self.projects_dialog = ProjectsDialog(self)
        self.projects_dialog.finished.connect(lambda _: setattr(self, 'projects_dialog', None))
        self.projects_dialog.open()

    def keep_recovery_copy(self, reason='Before switching projects'):
        if self.dirty and (self.session.clips or self.session.tracks or self.project_path):
            from studio_projects import write_snapshot
            path = self.data / f"recovery-{time.time_ns()}.djmix"
            write_snapshot(self.session, path, self.project_path, reason)
            return path
        return None

    def new_project(self):
        if self.jobs:
            return
        try:
            kept = self.keep_recovery_copy('Before starting a new mix')
        except OSError as error:
            self.notify(f'Could not keep your unsaved mix: {error}. Save it to another location before starting a new mix.', 'error')
            return
        self.reset_listening()
        self.session = Session(tracks=self.session.tracks)
        self.project_path, self.selected_id = None, ""
        self.write_prefs(last_project=None)
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
        path, _ = QFileDialog.getOpenFileName(self, "Open a mix", start, "BeatStitch project (*.djmix)")
        if not path:
            return
        self.open_project_async(Path(path))

    def open_demo(self):
        if self.jobs: return
        from studio_demo import prepare_demo
        cancel = None
        def ready(result):
            if cancel is not None and cancel.is_set():
                self.say('Opening cancelled. Your current mix has not changed.')
                return
            path, loaded = result
            if self.open_project_path(path, loaded=loaded, demo=True):
                self.select_clip(self.session.clips[1].id)
                self.show_help('demo')
        cancel = self.job(lambda progress, emit: prepare_demo(self.data, progress), ready, cancellable=True)
        return cancel

    def open_project_async(self, path, recovery=None, finished=None):
        if self.jobs: return None
        from studio_projects import read_project
        cancel = None
        def ready(loaded):
            if cancel is not None and cancel.is_set():
                self.project_error = 'Opening cancelled. The current mix has not changed.'
                if finished: finished(False)
                return
            success = self.open_project_path(path, recovery, loaded=loaded)
            if finished: finished(success)
        def failed(message):
            self.project_error = f'Could not open {Path(path).name}: {message}'
            self.notify(self.project_error, 'error')
            if finished: finished(False)
        def work(progress, emit):
            progress('Reading the project')
            loaded, _ = read_project(path, progress)
            progress('Project and source checks complete')
            return loaded
        cancel = self.job(work, ready, cancellable=True, failed_callback=failed)
        return cancel

    def open_project_path(self, path, recovery=None, *, loaded=None, demo=False):
        if self.jobs: return False
        from studio_projects import is_recovery, read_project
        self.project_error = ''
        path = Path(path).resolve()
        recovery = is_recovery(path, self.data) if recovery is None else recovery
        try:
            # Validate before changing playback, history, ownership or the current autosave.
            if loaded is None: loaded, _ = read_project(path)
            kept = self.keep_recovery_copy('Before trying the demo mix' if demo else
                                           'Before opening a recovery copy' if recovery else 'Before opening another project')
            self.load_project(path, remember=not (recovery or demo), loaded=loaded)
            if recovery or demo:
                self.dirty = True
                self.write_prefs(last_project=None)
                self.autosave(); self.refresh()
        except Exception as error:
            self.project_error = f'Could not open {path.name}: {error}'
            self.notify(self.project_error, 'error')
            return False
        message = ('Demo opened as an editable copy. Press Play mix to hear it; Save chooses your own project file.' if demo else
                   'Recovery opened as an unsaved copy. Use Save to choose a project file.' if recovery else f'Opened {path.name}.')
        self.say(message + (' Your previous unsaved mix is in Projects → Recovery copies.' if kept else ''))
        return True

    def load_project(self, path, remember=True, loaded=None):
        if loaded is None:
            from studio_projects import read_project
            loaded, _ = read_project(path)
        self.session = loaded
        self.project_path = path if remember else None
        self.selected_id = loaded.clips[-1].id if loaded.clips else ""
        self.history, self.future, self.touched = [], [], set()
        self.reset_listening()
        self.revision += 1
        self.dirty = False
        if remember:
            self.remember_project(path)
            self.autosave()
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
        path, _ = QFileDialog.getSaveFileName(self, "Save mix", str(start), "BeatStitch project (*.djmix)")
        if path:
            self.write_project(Path(path))

    def write_project(self, path):
        path = Path(path).resolve()
        try:
            saved = copy.deepcopy(self.session)
            saved.name = path.stem
            from studio_media import portable_value
            value = portable_value(saved, path)
            if value.get('_portable_paths') == 1:
                temporary = path.with_name(path.name + '.tmp')
                temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
                temporary.replace(path)
            else:
                saved.save(path)
        except Exception as error:
            self.notify(f"Could not save: {error}", "error")
            return False
        self.session.name = saved.name
        self.project_path, self.dirty = path, False
        self.remember_project(path)
        self.autosave()
        self.refresh()
        self.say(f"Saved {path.name}.")
        return True

    # Rendering and playback ----------------------------------------------------------------

    def reset_listening(self):
        if self.listening_notes_dialog is not None:
            self.listening_notes_dialog.reject()
        from studio_review_walk import ReviewWalk
        self.listening_review = ReviewWalk()
        self.loaded_record = None
        self.audio_revision, self.audio_span, self.levels, self.loaded_label = -1, None, None, ""
        self.pending_playback = None
        self.player.stop()
        self.player.setSource(QUrl())
        self.update_storage_pins()
        self.source_audition.stop()
        self.listen_kind, self.listen_transition, self.mix_cursor = "mix", None, 0.0
        self.rendered_assets.clear()
        self.end_review()

    def set_listening_volume(self, value):
        self.audio.setVolume(value / 100)
        if hasattr(self, "source_audition"):
            self.source_audition.output.setVolume(value / 100)

    def pause_for_source(self):
        self.sync_position()
        self.player.pause()
        self.pending_playback = None
        self.end_review()
        self.refresh_audio_outputs()

    def end_review(self):
        if self.reviewing:
            self.listening_review.stop()
        self.refresh_review_strip()
        self.update_transport_loop()

    @property
    def reviewing(self):
        return self.listening_review.active

    def refresh_review_strip(self):
        review = self.listening_review
        total = max(0, len(self.session.clips) - 1)
        self.review_strip.setVisible(review.started and total > 0)
        index = review.index(self.session)
        pending = total - len(review.kept)
        counts = f'{len(review.kept)} of {total} kept · {pending} still to check'
        if review.active and index:
            names = [self.session.tracks[c.track_id].name for c in self.session.clips[index - 1:index + 1]]
            prompt = 'Listen, then Keep, Change or Next.' if review.queue else 'Last blend: keep it, try a change, or finish unchecked.'
            text = f'Review {index} → {index + 1}: {names[0]} → {names[1]}\n{counts}. {review.message or prompt}'
        else:
            text = f'{review.message or "Review paused."} {counts}.\nKept means you liked it; it is not a quality score.'
        self.review_label.setText(text)
        self.review_label.setToolTip(text + '\nAudio edits clear affected choices. Review all starts a new pass; choices are not saved in the project.')
        for widget in (self.review_keep, self.review_change, self.review_next):
            widget.setVisible(review.active)
        self.review_resume.setVisible(not review.active)
        self.review_resume.setText('Review remaining' if pending else 'Review again')
        self.review_resume.setEnabled(not self.jobs)
        self.review_next.setEnabled(not self.jobs)
        self.review_change.setEnabled(not self.jobs)
        self.review_keep.setText('Keep and next' if review.queue else 'Keep and finish')
        self.review_next.setText('Next' if review.queue else 'Finish')
        next_tip = ('Hear the next blend without marking this one as kept. Unchecked blends remain available for review.' if review.queue
                    else 'Finish this pass without keeping the last blend. Review remaining revisits unchecked blends.')
        self.review_next.setToolTip(next_tip)
        self.review_next.setAccessibleDescription(next_tip)
        fresh = (index is not None and self.listen_kind == 'transition' and self.listen_transition == index
                 and self.audio_revision == self.revision and not self.player.source().isEmpty()
                 and self.pending_playback is None)
        self.review_keep.setEnabled(fresh and not self.jobs)
        outcome = 'hear the next one' if review.queue else 'finish this pass'
        tip = (f'I like this blend. Remember this listening choice and {outcome}. This does not change audio or certify the mix.'
               if fresh else 'Listen to blend first. Its current audio must be prepared and loaded before you can keep it.')
        self.review_keep.setToolTip(tip)
        self.review_keep.setAccessibleDescription(tip)
        self.review_end.setText('End review' if review.active else 'Close review')
        self.review_btn.setEnabled(total > 0 and not self.jobs and not review.active)
        self.listening_notes_btn.setEnabled(total > 0 and not self.jobs)
        if self.listening_notes_dialog is not None:
            self.listening_notes_dialog.refresh_available()

    def notes_audio_ready(self, index):
        return (not self.jobs and self.listen_kind == 'transition' and self.listen_transition == index
                and self.audio_revision == self.revision and self.loaded_record is not None
                and not self.player.source().isEmpty() and self.pending_playback is None)

    def show_listening_notes(self):
        index = self.transition_index()
        if not index or len(self.session.clips) < 2 or self.jobs:
            return
        if self.listening_notes_dialog is not None:
            self.listening_notes_dialog.raise_(); self.listening_notes_dialog.activateWindow()
            return
        from studio_listening_notes_ui import ListeningNotesDialog
        dialog = ListeningNotesDialog(self, index)
        self.listening_notes_dialog = dialog
        dialog.finished.connect(lambda _: setattr(self, 'listening_notes_dialog', None))
        dialog.show()

    def save_listening_note(self, index, judgments, comment):
        if not self.notes_audio_ready(index) or not self.refresh_prepared_audio():
            raise djmix.MixError('This blend changed. Listen to its current audio before saving notes.')
        from studio_listening_notes import capture_note, validate_notes
        note = capture_note(self.session, index, self.loaded_record['fingerprint'], judgments, comment)
        validate_notes([*self.session.listening_notes, note])
        saved = self.edit(lambda: self.session.listening_notes.append(note), action='Save listening notes')
        if saved:
            self.say('Listening notes saved with this project. The audio is unchanged.')
        return saved

    def dismiss_review(self):
        active = self.reviewing
        self.pending_playback = None
        self.player.pause()
        self.end_review()
        if not active:
            self.listening_review.started = False
        self.refresh_review_strip()

    def resume_review(self):
        self.listening_review.reconcile(self.session)
        self.review_transitions(remaining=bool(len(self.session.clips) - 1 - len(self.listening_review.kept)))

    def update_transport_loop(self):
        loop = self.listen_kind == "transition" and self.loop_blend.isChecked()
        self.player.setLoops(QMediaPlayer.Loops.Infinite if loop else QMediaPlayer.Loops.Once)

    def review_transitions(self, checked=False, *, remaining=False):
        if len(self.session.clips) < 2 or self.jobs:
            return
        self.listening_review.start(self.session, remaining=remaining)
        self.next_review_transition()

    def next_review_transition(self):
        if self.jobs or not self.reviewing:
            return
        self.listening_review.reconcile(self.session)
        pair = self.listening_review.advance()
        if pair is None:
            self.pending_playback = None
            self.player.pause()
            self.end_review()
            self.say('Review finished. Kept blends record your choice; unchecked blends can be reviewed again.')
            return
        index = self.listening_review.index(self.session)
        self.select_transition(index)
        self.preview(review=True)

    def keep_review_transition(self):
        if not self.reviewing or self.jobs or not self.review_keep.isEnabled():
            return
        if not self.refresh_prepared_audio():
            self.refresh_review_strip()
            self.say('This blend changed. Listen to blend again before keeping it.')
            return
        self.listening_review.kept[self.listening_review.current] = self.loaded_record['fingerprint']
        self.next_review_transition()

    def change_review_transition(self):
        if self.reviewing and not self.jobs:
            self.select_transition(self.listening_review.index(self.session))
            self.edit_blend()

    def preview(self, review=False):
        index = self.transition_index()
        if len(self.session.clips) < 2 or self.rendering or not index:
            return
        self.source_audition.stop()
        self.refresh_audio_outputs()
        self.refresh_prepared_audio()
        if not review and not (self.reviewing and index == self.listening_review.index(self.session)):
            self.end_review()
        if (not review and self.listen_kind == "transition" and self.listen_transition == index
                and self.audio_revision == self.revision
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState):
            self.player.pause()
            return
        try:
            region = preview_range(self.session, index)
        except djmix.MixError as error:
            self.notify(str(error), "error")
            return
        cached = self.cached_audio("transition", index)
        if cached:
            self.activate_audio(cached, region[0] / djmix.SR, True)
            return
        self.start_render(self.data / "renders" / f"preview-{time.time_ns()}.wav", region, True,
                          f"Transition {index} → {index + 1}", kind="transition", transition=index)

    def render_full(self):
        if self.session.clips and not self.rendering:
            self.source_audition.stop()
            self.end_review()
            cached = self.cached_audio("mix")
            if cached:
                self.activate_audio(cached, self.mix_cursor, True)
            else:
                self.start_render(self.data / "renders" / f"mix-{time.time_ns()}.wav", None, True, "Full mix")

    def cached_audio(self, kind, transition=None):
        with self.storage.gate():
            record = self.find_cached_audio(kind, transition)
            if record:
                paths = [t.path for t in self.session.tracks.values()] + [record['path']]
                if not self.player.source().isEmpty():
                    paths.append(self.player.source().toLocalFile())
                self.storage.pin(paths, gate_held=True)
            return record

    def find_cached_audio(self, kind, transition=None):
        from studio_render_cache import listening_recipe, RenderCache
        try:
            recipe = listening_recipe(self.session, kind, transition)
        except (OSError, djmix.MixError):
            return None
        record = self.rendered_assets.get((kind, transition))
        if record and Path(record['path']).is_file():
            # Untagged records from this running revision can be adopted once.
            if 'fingerprint' not in record and record['revision'] == self.revision:
                record['fingerprint'] = recipe['fingerprint']
            if record.get('fingerprint') != recipe['fingerprint']:
                record = None
        else:
            record = None
        if record is None:
            record = RenderCache(self.data / 'renders').get(recipe)
        if record is None:
            return None
        record = {**record, 'start': recipe['start'], 'reference_end': recipe['reference_end'], 'revision': self.revision, 'kind': kind, 'transition': transition,
                  'title': f'Transition {transition} → {transition + 1}' if kind == 'transition' else 'Full mix'}
        if kind == 'transition':
            record['clip_ids'] = [c.id for c in self.session.clips[transition - 1:transition + 1]]
        self.rendered_assets[(kind, transition)] = record
        return record

    def refresh_prepared_audio(self):
        """Rebase an unchanged pair after upstream edits; invalidate only changed sound."""
        from studio_render_cache import listening_recipe
        record = self.loaded_record
        if record is None or self.player.source().isEmpty():
            return False
        index = record.get('transition')
        if record['kind'] == 'transition' and record.get('clip_ids'):
            index = next((i for i in range(1, len(self.session.clips))
                          if [c.id for c in self.session.clips[i - 1:i + 1]] == record['clip_ids']), None)
            if index is None:
                self.audio_revision = -1
                return False
        try:
            recipe = listening_recipe(self.session, record['kind'], index)
            valid = recipe['fingerprint'] == record.get('fingerprint') and Path(record['path']).is_file()
        except (OSError, djmix.MixError):
            valid = False
        if not valid:
            self.audio_revision = -1
            return False
        self.audio_revision = self.revision
        self.audio_origin = recipe['start']
        from studio_tempo_curve import session_curve
        self.audio_curve = session_curve(self.session)
        self.audio_span = self.audio_origin, recipe['reference_end']
        self.listen_transition = index
        record.update(start=self.audio_origin, reference_end=recipe['reference_end'], revision=self.revision, transition=index)
        if record['kind'] == 'transition':
            record['title'] = f'Transition {index} → {index + 1}'
            self.loaded_label = record['title']
        return True

    def activate_audio(self, record, position, play):
        from studio_render_cache import listening_recipe
        record = dict(record)
        if 'fingerprint' not in record and record['revision'] == self.revision:
            record.update(fingerprint=listening_recipe(self.session, record['kind'], record['transition'])['fingerprint'])
        if record['kind'] == 'transition' and 'clip_ids' not in record:
            index = record['transition']
            record['clip_ids'] = [c.id for c in self.session.clips[index - 1:index + 1]]
        self.loaded_record = record
        self.sync_position()
        self.player.pause()
        self.source_audition.stop()
        self.listen_kind, self.listen_transition = record["kind"], record["transition"]
        self.audio_origin, self.audio_revision = record["start"], record["revision"]
        from studio_tempo_curve import session_curve
        self.audio_curve = session_curve(self.session)
        self.audio_span = (record['start'], record.get('reference_end', float(self.audio_curve.to_reference(
            self.audio_curve.to_output(record['start']) + record['duration']))))
        self.levels, self.loaded_label = record.get("levels"), record["title"]
        position = max(self.audio_span[0], min(position, self.audio_span[1]))
        if play and position >= self.audio_span[1] - .01:
            position = self.audio_span[0]
        if self.listen_kind == "mix":
            self.mix_cursor = position
        url = QUrl.fromLocalFile(str(Path(record["path"]).resolve()))
        self.storage.pin([t.path for t in self.session.tracks.values()] + [url.toLocalFile()])
        elapsed = float(self.audio_curve.to_output(position) - self.audio_curve.to_output(self.audio_origin))
        self.pending_playback = (url, round(elapsed * 1000), play)
        self.update_transport_loop()
        if self.player.source() != url:
            self.player.setSource(url)
        else:
            self.media_ready(self.player.mediaStatus())
        self.refresh()

    def media_ready(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) or (
                status == QMediaPlayer.MediaStatus.EndOfMedia and self.pending_playback is not None):
            if self.pending_playback and self.pending_playback[0] == self.player.source():
                _, position, play = self.pending_playback
                self.pending_playback = None
                self.player.setPosition(position)
                if play and not self.closing:
                    self.player.play()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia and self.listen_kind == "transition" and self.reviewing:
            self.say('Blend finished. Keep it, try a Change, or choose Next to leave it unchecked.')
        self.refresh_review_strip()

    def export_mix(self):
        if not self.session.clips or self.rendering:
            return
        from studio_export import PRESETS
        from studio_export_ui import ExportDialog
        from studio_tempo_curve import session_curve
        chooser = ExportDialog(float(session_curve(self.session).to_output(self.timeline.duration())), self.prefs.get("export_preset", "wav"),
                               self.prefs.get("export_tracklist", False), self,
                               mastering_enabled=self.session.mastering_enabled)
        if not chooser.exec():
            return
        preset, tracklist = chooser.preset(), chooser.tracklist.isChecked()
        extension = PRESETS[preset]["extension"]
        folder = self.prefs.get("export_folder") or QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.MusicLocation) or str(self.data)
        path, _ = QFileDialog.getSaveFileName(self, "Export mix", str(Path(folder) / f"{self.session.name}{extension}"),
                                              f"{preset.upper()} audio (*{extension})")
        if path:
            destination = Path(path)
            if not destination.suffix:
                destination = destination.with_suffix(extension)
            if destination.suffix.lower() != extension:
                self.notify(f"Choose a filename ending in {extension} for this format.", "error")
                return
            self.write_prefs(export_folder=str(destination.parent), export_preset=preset, export_tracklist=tracklist)
            self.start_export(destination, preset, tracklist)

    def start_export(self, destination, preset, include_tracklist):
        from studio_export import export_audio, write_tracklist, same_target
        if self.rendering:
            return
        protected = [Path(track.path) for track in self.session.tracks.values()]
        if any(same_target(destination, source) for source in protected):
            self.notify("Choose an export filename different from your original songs.", "error")
            return
        snapshot, revision = copy.deepcopy(self.session), self.revision
        cached = self.cached_audio("mix")
        self.stop()

        def work(progress, emit):
            if cached:
                result = copy.copy(cached)
                progress("Reusing the prepared mix")
            else:
                from studio_render_cache import prepare_listening
                result = prepare_listening(snapshot, self.data / 'renders', progress=progress,
                                           audio_cache=self.data / 'audio-cache')
            exported = export_audio(Path(result['path']), destination, preset, title=snapshot.name,
                                    protected_sources=protected, progress=progress)
            try:
                exported["levels"] = level_envelope(destination)
            except Exception:
                exported["levels"] = None
            sidecar, warning = None, None
            if include_tracklist:
                candidate = destination.with_suffix(".tracklist.csv")
                number = 2
                while candidate.exists():
                    candidate = destination.with_name(f"{destination.stem}.tracklist-{number}.csv")
                    number += 1
                try:
                    sidecar = write_tracklist(snapshot, candidate)
                except Exception as error:
                    warning = f"The audio was saved, but the tracklist could not be written: {error}"
            return result, exported, sidecar, warning

        def finished(payload):
            result, exported, sidecar, warning = payload
            record = {**result, "kind": "mix", "transition": None,
                      "revision": revision, "title": "Full mix"}
            self.rendered_assets[("mix", None)] = record
            listening = {**record, **exported, "title": f"Exported {preset.upper()} mix"}
            text = f"Saved {destination.name} · {clock(exported['duration'])}\n{destination.parent}"
            if sidecar:
                text += f"\nTracklist: {sidecar.name}"
            if warning:
                text += f"\n{warning}"
            actions = [("Play exported mix", lambda: self.activate_audio(listening, 0, True)),
                       (REVEAL, lambda: self.reveal(destination))]
            if sidecar:
                actions.append(("Show tracklist", lambda: self.reveal(sidecar)))
            self.notify(text, "info", actions)
            self.refresh()

        self.job(work, finished, rendering=True, cancellable=True, title=f'Exporting {preset.upper()} audio',
                 reason='Using the prepared mix.' if cached else 'Preparing the mix for export; unchanged song audio is reused.')

    def start_render(self, destination, region, play, title, export=False, kind="mix", transition=None):
        if self.rendering:
            return
        snapshot, revision = copy.deepcopy(self.session), self.revision
        review_run = self.listening_review if self.reviewing else None
        review_pair = review_run.current if review_run else None
        self.sync_position()
        self.player.pause()
        self.source_audition.stop()
        self.pending_playback = None
        desired_position = self.mix_cursor if kind == "mix" else region[0] / djmix.SR

        def work(progress, emit):
            from studio_render_cache import prepare_listening, audio_recipe
            if not export:
                return prepare_listening(snapshot, self.data / 'renders', region, progress, self.data / 'audio-cache')
            from studio_export import export_audio
            prepared = prepare_listening(snapshot, self.data / 'renders', region, progress, self.data / 'audio-cache')
            exported = export_audio(Path(prepared['path']), destination, 'wav', title=snapshot.name,
                                    protected_sources=[Path(t.path) for t in snapshot.tracks.values()], progress=progress)
            return {**prepared, **exported, 'levels': level_envelope(destination)}

        def finished(result):
            record = {**result, "kind": kind, "transition": transition,
                      "revision": revision, "title": title}
            self.rendered_assets[(kind, transition)] = record
            reused, processed = result["reused_tracks"], result["processed_tracks"]
            detail = (f"{reused} {'song' if reused == 1 else 'songs'} from cache, {processed} processed, "
                      f"peak {result['true_peak_db']:.1f} dBTP")
            frames = result.get('reused_mix_frames', 0) + result.get('mixed_mix_frames', 0)
            if result.get('cached_render'):
                detail = 'Reused the prepared audio'
            elif frames:
                detail += f", {result.get('reused_mix_frames', 0) / frames:.0%} of assembled audio reused"
            if export:
                self.notify(f"Exported {destination.name}, {clock(result['duration'])} long. {detail}.", "info",
                            [("Play exported mix", lambda: self.activate_audio(record, 0, True)),
                             (REVEAL, lambda: self.reveal(destination))])
            else:
                self.say(f"{title} is ready to play. {detail}.")
            review_still_active = (review_run is None or
                                   (self.listening_review is review_run and review_run.active and review_run.current == review_pair))
            if not export and revision == self.revision and not self.closing and review_still_active:
                self.activate_audio(record, desired_position, play)
            else:
                self.refresh()

        self.job(work, finished, rendering=True, cancellable=True, title=f'Preparing {title.lower()}',
                 reason=('Checking the cached full mix, then finishing it with automatic mastering. Unchanged songs are reused.' if snapshot.mastering_enabled else
                         'The full mix is not prepared for these settings. Checking cached audio first.' if kind == 'mix' else
                         'Preparing this blend and its listening context. Unchanged song audio is reused.'))

    def toggle_play(self):
        if self.rendering:
            return
        self.refresh_audio_outputs()
        self.refresh_prepared_audio()
        self.source_audition.stop()
        self.end_review()
        if self.listen_kind == "mix" and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif self.listen_kind == "mix" and self.audio_revision == self.revision and not self.player.source().isEmpty():
            if self.player.duration() > 0 and self.player.position() >= self.player.duration() - 10:
                self.player.setPosition(0)
            self.player.play()
        elif self.session.clips:
            self.render_full()

    def stop(self):
        self.pending_playback = None
        self.player.pause()
        self.source_audition.stop()
        self.end_review()
        self.sync_position()

    def rewind(self):
        self.seek_timeline(0)

    def navigate(self, seconds):
        self.timeline.center_on(seconds)
        self.seek_timeline(seconds)

    def seek_timeline(self, seconds):
        if self.rendering:
            return
        self.end_review()
        self.mix_cursor = max(0, min(seconds, self.timeline.duration()))
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        cached = self.cached_audio("mix")
        if cached:
            self.activate_audio(cached, self.mix_cursor, playing)
        else:
            # A prepared blend remains useful for seeking within its audible span.
            # Moving the cursor must never implicitly render the entire arrangement.
            if self.refresh_prepared_audio() and self.audio_span and self.audio_span[0] <= self.mix_cursor < self.audio_span[1]:
                self.activate_audio(self.loaded_record, self.mix_cursor, playing)
                self.say('Using the prepared blend. Play mix prepares the whole arrangement when you need it.')
                return
            self.pending_playback = None
            self.player.pause()
            self.player.setSource(QUrl())
            self.loaded_record = None
            self.audio_span, self.levels = None, None
            self.listen_kind = "mix"
            self.sync_position()
            self.say('Playhead moved. This point has no prepared mix audio. Press Play mix to prepare it; unchanged audio will be reused.')

    def playback_changed(self, state):
        self.update_play_icon()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.meter_timer.start()

    def update_play_icon(self):
        playing = self.listen_kind == "mix" and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        # The transport lights green while playing, as in a DAW.
        self.play_btn.setIcon(T.icon("pause" if playing else "play", T.PLAYING if playing else T.TEXT, 14,
                                     T.PLAYING if playing else T.TEXT))
        self.play_btn.setToolTip("Pause mix (Space)" if playing else
                                "Play mix from the playhead (Space). Audio is prepared automatically when needed.")
        self.play_btn.setAccessibleName("Pause mix" if playing else "Play mix")
        self.render_btn.setText("Pause mix" if playing else "Play mix")
        auditioning = (self.listen_kind == "transition" and self.listen_transition == self.transition_index()
                       and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        self.preview_btn.setText("Pause blend" if auditioning else "Listen to blend")

    def update_time(self):
        loaded = not self.player.source().isEmpty()
        from studio_tempo_curve import session_curve
        curve = self.audio_curve if loaded else session_curve(self.session)
        seconds = self.playback_reference() if loaded else self.mix_cursor
        self.position_label.setText(bar_position(seconds, self.session.target_bpm))
        self.time_label.setText(clock(float(curve.to_output(seconds)), True))
        duration = self.player.duration() / 1000
        if self.rendering:
            self.source_label.set_text(self.job_title.text())
        elif not loaded:
            self.source_label.set_text("Press Play mix to listen")
        else:
            length = f", {clock(duration)}" if duration > 0 else ""
            changed = " · edits need preparation" if self.audio_revision != self.revision else ""
            finishing = ' · mastered' if self.loaded_record and self.loaded_record.get('mastering_applied') else ''
            self.source_label.set_text(f"{self.loaded_label}{finishing}{length}{changed}")

    def playback_reference(self):
        return float(self.audio_curve.to_reference(self.audio_curve.to_output(self.audio_origin) + self.player.position() / 1000))

    def sync_position(self):
        if (self.listen_kind == "mix" and self.audio_revision == self.revision
                and not self.player.source().isEmpty() and self.pending_playback is None):
            self.mix_cursor = self.playback_reference()
        self.update_time()
        playhead = self.mix_cursor if self.player.source().isEmpty() else self.playback_reference()
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
        if self.listening_notes_dialog is not None:
            self.listening_notes_dialog.reject()
        if self.lessons_dialog is not None:
            self.lessons_dialog.reject()
        if self.finishing_dialog is not None:
            self.finishing_dialog.reject()
        if self.mastering_dialog is not None:
            self.mastering_dialog.reject()
        if self.loop_dialog is not None:
            self.loop_dialog.reject()
        if self.separation_dialog is not None:
            self.separation_dialog.reject()
        self.player.stop()
        self.source_audition.stop()
        self.pending_playback = None
        self.autosave()
        self.write_prefs(volume=self.volume.value())
        self.band_cancel.set()
        downloading = bool(self.downloads_window and self.downloads_window.busy)
        if self.jobs or self.band_workers or downloading:
            if downloading:
                self.downloads_window.request_cancel()
            for cancel, *_ in self.jobs.values():
                cancel.set()
            self.closing = True
            self.say("Stopping processing before closing.")
            event.ignore()
        else:
            self.storage.close()
            event.accept()


def configure_application_identity(app):
    """Return the existing data folder while applying the current visible name."""
    app.setApplicationName("DJ Studio")
    app.setOrganizationName("DJ Studio")
    data = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    app.setApplicationName("BeatStitch")
    app.setApplicationDisplayName("BeatStitch")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    # Resolve the established data location before changing the visible identity.
    # Retain it on every platform; a rebrand must not create an empty new library.
    legacy_data = configure_application_identity(app)
    app.setWindowIcon(QIcon(str(ROOT / 'assets/about/beatstitch-icon.png')))
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
        data = legacy_data
    window = Studio(data, args.project)
    window.show()
    if args.library:
        QTimer.singleShot(250, lambda: window.import_paths(window.audio_files([args.library])))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
