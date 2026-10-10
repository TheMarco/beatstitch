#!/usr/bin/env python3
"""Small native Qt A/B player for the isolated tempo experiment."""
import json
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QLabel,
    QPushButton, QSlider, QVBoxLayout, QWidget,
)

ROOT = Path(__file__).resolve().parent


class TempoLab(QWidget):
    def __init__(self, folders, gentle=False):
        super().__init__()
        self.folders = folders
        self.reports = [json.loads((p / "report.json").read_text()) for p in folders]
        self.setWindowTitle("Tempo Lab — " + ("Gentle correction" if gentle else "Stronger correction"))
        self.resize(780, 520)
        self.setMinimumSize(700, 500)
        self.pending = None
        self.output_preference = "system"
        self.variant = "fixed"
        self.devices = QMediaDevices(self)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(.65)
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self.loaded)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playbackStateChanged.connect(self.play_state)
        self.player.errorOccurred.connect(lambda error, message: self.status.setText(message))
        self.devices.audioOutputsChanged.connect(self.refresh_outputs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(17)
        eyebrow = QLabel("DJ STUDIO  /  TEMPO LAB")
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)
        title = QLabel("Does a steadier beat sound better?")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QLabel("Compare the same passage and average tempo, without key transposition.\n"
                          "Loudness is matched. Your original recordings are unchanged.")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)

        self.track = QComboBox()
        self.track.setAccessibleName("Audition track")
        for report in self.reports:
            self.track.addItem(report["name"])
        self.track.currentIndexChanged.connect(self.change_track)
        layout.addWidget(self.track)
        self.detail = QLabel()
        self.detail.setObjectName("muted")
        layout.addWidget(self.detail)

        choices = QHBoxLayout()
        group = QButtonGroup(self)
        self.a = QPushButton("A · Current processing")
        self.b = QPushButton("B · Gentle correction" if gentle else "B · Stronger correction")
        for button in (self.a, self.b):
            button.setCheckable(True)
            button.setMinimumHeight(62)
            group.addButton(button)
            choices.addWidget(button)
        self.a.setChecked(True)
        self.a.clicked.connect(lambda: self.choose("fixed"))
        self.b.clicked.connect(lambda: self.choose("straightened"))
        layout.addLayout(choices)
        self.click = QCheckBox("Add a steady reference click")
        self.click.toggled.connect(lambda _: self.reload(self.player.position(), self.is_playing()))
        layout.addWidget(self.click)

        transport = QHBoxLayout()
        self.play = QPushButton("Play")
        self.play.setMinimumWidth(86)
        self.play.clicked.connect(self.toggle_play)
        transport.addWidget(self.play)
        restart = QPushButton("Restart")
        restart.clicked.connect(lambda: self.player.setPosition(0))
        transport.addWidget(restart)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("Playback position")
        self.seek.sliderReleased.connect(lambda: self.player.setPosition(self.seek.value()))
        transport.addWidget(self.seek, 1)
        self.time = QLabel("0:00 / 0:00")
        self.time.setMinimumWidth(90)
        transport.addWidget(self.time)
        layout.addLayout(transport)
        self.status = QLabel("Press Play, then switch A/B at any point. Switching keeps your position.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Output"))
        self.output = QComboBox()
        self.output.setAccessibleName("Audio output device")
        self.output.currentIndexChanged.connect(self.choose_output)
        bottom.addWidget(self.output, 1)
        bottom.addWidget(QLabel("Volume"))
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setAccessibleName("Volume")
        volume.setRange(0, 100)
        volume.setValue(65)
        volume.setMaximumWidth(100)
        volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        bottom.addWidget(volume)
        layout.addLayout(bottom)
        footnote = QLabel("Listen for tighter kicks, but also any smeared drums, flutter or changed texture.\n"
                          "Experimental correction of selected passages; the main DJ app is unchanged.")
        footnote.setObjectName("muted")
        footnote.setWordWrap(True)
        layout.addWidget(footnote)
        self.setStyleSheet("""
            QWidget { background: #111a19; color: #e4eeea; font-size: 14px; }
            QLabel#title { font-size: 27px; font-weight: 650; }
            QLabel#eyebrow { color: #93c6b2; font-size: 12px; font-weight: 650; }
            QLabel#muted { color: #9bb0a8; }
            QPushButton, QComboBox { background: #1e2b27; border: 1px solid #3c5149;
                border-radius: 7px; min-height: 38px; padding: 0 12px; }
            QPushButton:hover { border-color: #9cddbf; }
            QPushButton:checked { background: #b5edcd; color: #112d20; border-color: #b5edcd; }
            QComboBox QAbstractItemView { background: #1e2b27; selection-background-color: #40614e; }
            QSlider::groove:horizontal { background: #33453d; height: 5px; border-radius: 2px; }
            QSlider::handle:horizontal { background: #b5edcd; width: 13px; margin: -5px 0; border-radius: 6px; }
            QCheckBox { spacing: 10px; min-height: 25px; }
        """)
        self.refresh_outputs()
        self.change_track()

    def is_playing(self):
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def refresh_outputs(self):
        devices = QMediaDevices.audioOutputs()
        default = QMediaDevices.defaultAudioOutput()
        self.output.blockSignals(True)
        self.output.clear()
        self.output.addItem("System default · " + (default.description() or "Unavailable"), "system")
        for device in devices:
            self.output.addItem(device.description(), bytes(device.id()).hex())
        index = self.output.findData(self.output_preference)
        if index < 0:
            self.output.addItem("Selected output disconnected", self.output_preference)
            index = self.output.count() - 1
        self.output.setCurrentIndex(index)
        self.output.blockSignals(False)
        chosen = default if self.output_preference == "system" else next(
            (d for d in devices if bytes(d.id()).hex() == self.output_preference), None)
        if chosen is None or chosen.isNull():
            self.player.pause()
            self.audio.setMuted(True)
            self.status.setText("The selected audio output is unavailable.")
        else:
            self.audio.setDevice(chosen)
            self.audio.setMuted(False)

    def choose_output(self, index):
        self.output_preference = self.output.itemData(index)
        self.refresh_outputs()

    def change_track(self, *_):
        report = self.reports[self.track.currentIndex()]
        start, end = report["source_start"], report["source_end"]
        self.detail.setText(f"{report['target_bpm']} BPM  ·  {report['duration']:.1f} seconds  ·  "
                            f"Source passage {int(start)//60}:{int(start)%60:02d}–{int(end)//60}:{int(end)%60:02d}")
        self.seek.setRange(0, round(report["duration"] * 1000))
        self.reload(0, False)

    def choose(self, variant):
        if variant != self.variant:
            self.variant = variant
            self.reload(self.player.position(), self.is_playing())

    def reload(self, position, play):
        self.player.pause()
        self.pending = (position, play)
        suffix = "-click" if self.click.isChecked() else ""
        path = self.folders[self.track.currentIndex()] / f"{self.variant}{suffix}.wav"
        self.player.setSource(QUrl.fromLocalFile(str(path)))

    def loaded(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            if self.pending is not None:
                position, play = self.pending
                self.pending = None
                self.player.setPosition(position)
                if play:
                    self.player.play()

    def toggle_play(self):
        if self.is_playing():
            self.player.pause()
        else:
            self.refresh_outputs()
            if not self.audio.isMuted():
                self.player.play()

    def play_state(self, *_):
        self.play.setText("Pause" if self.is_playing() else "Play")

    def position_changed(self, value):
        if not self.seek.isSliderDown():
            self.seek.setValue(value)
        duration = round(self.reports[self.track.currentIndex()]["duration"])
        seconds = value // 1000
        self.time.setText(f"{seconds//60}:{seconds%60:02d} / {duration//60}:{duration%60:02d}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Tempo Lab")
    app.setFont(QFont("Arial", 13))
    gentle = "--gentle" in sys.argv
    suffix = "gentle" if gentle else "r2"
    folders = [ROOT / "outputs/tempo-prototype" / f"{name}-{suffix}" for name in ("voyager", "another-realm")]
    window = TempoLab(folders, gentle)
    window.show()
    sys.exit(app.exec())
