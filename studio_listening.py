"""Native source audition, independent of the arrangement transport."""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout

from studio_widgets import clock


class SourceAudition(QFrame):
    aboutToPlay = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.track = None
        self.pending_position = None
        self.pending_play = False
        self.output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        self.description = QLabel("Select a song to listen before adding it.")
        self.description.setWordWrap(True)
        self.description.setProperty("role", "small")
        box.addWidget(self.description)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.play_button = QPushButton("Listen")
        self.play_button.setToolTip("Listen to the selected source song at its original tempo and pitch. This does not add it to the mix.")
        self.play_button.clicked.connect(self.toggle)
        self.begin_button = QPushButton("Start")
        self.begin_button.setToolTip("Listen from the beginning of this source song.")
        self.begin_button.clicked.connect(lambda: self.listen_at(0))
        self.end_button = QPushButton("Ending")
        self.end_button.setToolTip("Listen to the last 30 seconds of this source song.")
        self.end_button.clicked.connect(lambda: self.listen_at(max(0, self.track.duration - 30)) if self.track else None)
        for widget in (self.play_button, self.begin_button, self.end_button):
            widget.setMinimumHeight(28)
            widget.setProperty("helpTopic", "library")
            row.addWidget(widget)
        box.addLayout(row)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("Position in the original song")
        self.seek.setToolTip("Seek in the original source audio. This does not change your mix or cues.")
        self.seek.sliderMoved.connect(self.seek_to)
        box.addWidget(self.seek)
        self.position = QLabel("0:00 / 0:00 · Original audio")
        self.position.setProperty("role", "small")
        box.addWidget(self.position)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playbackStateChanged.connect(self.update_controls)
        self.player.mediaStatusChanged.connect(self.media_ready)
        self.player.errorOccurred.connect(lambda *_: self.failed.emit("Could not play this song: " + self.player.errorString()))
        self.update_controls()

    def set_track(self, track):
        signature = (track.id, track.path) if track else None
        old = (self.track.id, self.track.path) if self.track else None
        self.track = track
        if signature != old:
            self.stop()
            self.player.setSource(QUrl())
        self.description.setText(track.name if track else "Select a song to listen before adding it.")
        self.description.setToolTip(track.name if track else "")
        self.seek.setRange(0, round(track.duration * 1000) if track else 0)
        self.position_changed(self.player.position())
        self.update_controls()

    def update_controls(self, *_):
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("Pause song" if playing else "Listen")
        for widget in (self.play_button, self.begin_button, self.end_button, self.seek):
            widget.setEnabled(self.track is not None)

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.listen_at(self.player.position() / 1000)

    def listen_at(self, seconds):
        if not self.track:
            return
        if not Path(self.track.path).is_file():
            self.failed.emit(f"Cannot find {self.track.name}. Locate its audio file before listening.")
            return
        self.aboutToPlay.emit()
        url = QUrl.fromLocalFile(str(Path(self.track.path).resolve()))
        self.pending_position = max(0, min(round(seconds * 1000), round(self.track.duration * 1000)))
        self.pending_play = True
        if self.player.source() != url:
            self.player.setSource(url)
        else:
            self.media_ready(self.player.mediaStatus())

    def media_ready(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
                      QMediaPlayer.MediaStatus.EndOfMedia):
            if self.pending_position is not None:
                position, self.pending_position = self.pending_position, None
                self.player.setPosition(position)
            if self.pending_play:
                self.pending_play = False
                self.player.play()

    def seek_to(self, milliseconds):
        if self.player.source().isEmpty():
            self.listen_at(milliseconds / 1000)
        else:
            self.pending_position = None
            self.player.setPosition(milliseconds)

    def position_changed(self, milliseconds):
        if not self.seek.isSliderDown():
            self.seek.setValue(milliseconds)
        duration = self.track.duration if self.track else 0
        self.position.setText(f"{clock(milliseconds / 1000)} / {clock(duration)} · Original audio")

    def stop(self):
        self.pending_play = False
        self.pending_position = None
        self.player.pause()
