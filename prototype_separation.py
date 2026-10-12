"""Native listening trial for precomputed, synchronized local separation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import soundfile as sf
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QDialog, QHBoxLayout,
                              QLabel, QPushButton, QSlider, QVBoxLayout, QWidget)

from studio_separation_audio import SeparationTransport, VARIANTS
import studio_theme as T

LABELS = ('Original', 'Without vocals', 'Drums only', 'Vocals only', 'Recombined')
EXPLANATIONS = (
    'The original passage, before separation. Start here, then switch while it keeps playing.',
    'Estimated drums, bass and accompaniment together. Listen for leftover voice, hollow sounds or missing instruments.',
    'Estimated drums. Listen for clear kick and snare attacks, and any melody or voice leaking through.',
    'Estimated vocals. Listen for words or sung notes, and any drums or instruments leaking through.',
    'All four estimated parts added back together. Compare with Original to hear what the separation changed.',
)


def clock(seconds):
    seconds = max(0, round(seconds))
    return f'{seconds // 60}:{seconds % 60:02}'


class SeparationWave(QWidget):
    seek = Signal(int)

    def __init__(self, report):
        super().__init__()
        self.report, self.variant, self.position = report, 'original', 0
        self.setMinimumHeight(135)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Separation trial waveform')
        self.setToolTip('Actual amplitude of the selected version, using the same scale for every version. Click to seek; Left/Right moves one second and Home returns to the beginning.')

    def paintEvent(self, event):
        p = QPainter(self); p.fillRect(self.rect(), QColor(T.FIELD))
        waveforms = self.report['waveforms']
        samples = waveforms[self.variant]
        scale = max(.001, *(max(values) for values in waveforms.values()))
        middle = self.height() / 2
        p.setPen(QPen(QColor('#5CCBEA'), 1))
        for i, sample in enumerate(samples):
            x = round(i / max(1, len(samples) - 1) * self.width())
            height = sample / scale * (middle - 12)
            p.drawLine(x, round(middle - height), x, round(middle + height))
        p.setPen(QPen(QColor(T.ACCENT), 2))
        x = round(self.position / (self.report['duration'] * 1000) * self.width())
        p.drawLine(x, 0, x, self.height())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self.seek.emit(round(self.report['duration'] * 1000 * event.position().x() / max(1, self.width())))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home):
            value = self.position + (-1000 if event.key() == Qt.Key.Key_Left else 1000)
            if event.key() == Qt.Key.Key_Home: value = 0
            self.seek.emit(max(0, min(round(self.report['duration'] * 1000), value))); event.accept()
        else: super().keyPressEvent(event)


class SeparationTrial(QDialog):
    def __init__(self, report, folder):
        super().__init__()
        self.report = report
        self.setWindowTitle('DJ Studio · Separation listening trial')
        self.resize(880, 610); self.setMinimumSize(760, 580)
        self.player = SeparationTransport(self)
        self.devices = QMediaDevices(self)
        box = QVBoxLayout(self); box.setContentsMargins(24, 22, 24, 22); box.setSpacing(14)
        title = QLabel('Hear the musical parts'); title.setProperty('role', 'title'); box.addWidget(title)
        song = QLabel(f'{report["title"]} · {clock(report["start"])}–{clock(report["start"] + report["duration"])} in the original song')
        song.setTextFormat(Qt.TextFormat.PlainText); song.setWordWrap(True); box.addWidget(song)
        note = QLabel('Separation estimates what belongs to each part. It can leave voices behind or remove some instruments. '
                      'Switch while playing to judge the sound; quieter isolated parts are expected.')
        note.setWordWrap(True); box.addWidget(note)
        self.wave = SeparationWave(report); box.addWidget(self.wave, 1)
        self.buttons, self.group, self.shortcuts = [], QButtonGroup(self), []
        row = QHBoxLayout()
        for i, (name, label, explanation) in enumerate(zip(VARIANTS, LABELS, EXPLANATIONS), 1):
            button = QPushButton(f'{i} · {label}'); button.setCheckable(True)
            button.setToolTip(explanation + f' Press {i} to select. Switching preserves the playback position and relative level.')
            button.toggled.connect(lambda checked, n=name: self.select(n) if checked else None)
            self.group.addButton(button); self.buttons.append(button); row.addWidget(button)
            shortcut = QShortcut(QKeySequence(str(i)), self); shortcut.activated.connect(button.click)
            self.shortcuts.append(shortcut)
        box.addLayout(row)
        self.explanation = QLabel(EXPLANATIONS[0]); self.explanation.setWordWrap(True)
        self.explanation.setMinimumHeight(44); box.addWidget(self.explanation)
        row = QHBoxLayout()
        self.play = QPushButton('Play'); self.play.clicked.connect(self.toggle)
        self.play.setToolTip('Play or pause the selected version. Space does the same. Playback never starts automatically.')
        row.addWidget(self.play)
        self.restart = QPushButton('Start again'); self.restart.clicked.connect(lambda: self.player.seek(0))
        self.restart.setToolTip('Return to the start of this excerpt, keeping the selected version and playback state.')
        row.addWidget(self.restart)
        self.loop = QCheckBox('Repeat passage'); self.loop.setChecked(True)
        self.loop.setToolTip('Repeat this short passage on the same playback clock so you can keep comparing versions. The end-to-start return is not a DJ blend.')
        self.loop.toggled.connect(self.player.set_loop); row.addWidget(self.loop); row.addStretch()
        self.time = QLabel(); row.addWidget(self.time); box.addLayout(row)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, round(report['duration'] * 1000)); self.seek.setSingleStep(1000); self.seek.setPageStep(5000)
        self.seek.setAccessibleName('Separation playback position')
        self.seek.setToolTip('Seek all versions together. Left/Right moves one second; Page Up/Down moves five seconds.')
        self.seek.valueChanged.connect(self.player.seek); box.addWidget(self.seek)
        row = QHBoxLayout(); self.output = QLabel(); row.addWidget(self.output, 1)
        self.refresh = QPushButton('Refresh output'); self.refresh.clicked.connect(self.refresh_output)
        self.refresh.setToolTip('Follow the current system audio output. Changing output pauses playback; press Play when ready.')
        row.addWidget(self.refresh)
        self.volume = QSlider(Qt.Orientation.Horizontal); self.volume.setRange(0, 100); self.volume.setValue(60)
        self.volume.setMaximumWidth(140); self.volume.setAccessibleName('Separation listening volume')
        self.volume.setToolTip('One listening volume for every version. The parts are not boosted individually.')
        self.volume.valueChanged.connect(lambda value: self.player.set_volume(value / 100))
        row.addWidget(QLabel('Volume')); row.addWidget(self.volume); box.addLayout(row)
        self.status = QLabel('Prepared locally · Same timing and common gain · Original tempo and pitch')
        self.status.setTextFormat(Qt.TextFormat.PlainText); self.status.setWordWrap(True); box.addWidget(self.status)
        close = QPushButton('Close trial'); close.clicked.connect(self.close)
        close.setToolTip('Close this listening trial. No song, cue, or working mix is changed.')
        row = QHBoxLayout(); row.addStretch(); row.addWidget(close); box.addLayout(row)
        self.wave.seek.connect(self.player.seek)
        self.player.positionChanged.connect(self.position)
        self.player.playingChanged.connect(lambda playing: self.play.setText('Pause' if playing else 'Play'))
        self.player.errorOccurred.connect(self.status.setText)
        self.devices.audioOutputsChanged.connect(self.refresh_output)
        self.space = QShortcut(QKeySequence('Space'), self); self.space.activated.connect(self.toggle)
        for widget in self.findChildren(QWidget):
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton): widget.setAutoDefault(False)
        self.player.set_loop(True)
        # Bound load size and reject a partially prepared example before playback.
        for name in VARIANTS:
            info = sf.info(Path(folder) / f'{name}.wav')
            if not 8 <= report['duration'] <= 60 or (info.frames, info.samplerate, info.channels) != (report['frames'], 48000, 2):
                raise ValueError('The listening example is incomplete. Prepare it again.')
        self.player.load(folder)
        self.player.set_volume(.6); self.buttons[0].setChecked(True)
        self.position(0); self.refresh_output()

    def select(self, name):
        self.player.select(name); self.wave.variant = name; self.wave.update()
        self.explanation.setText(EXPLANATIONS[VARIANTS.index(name)])

    def toggle(self):
        if self.player.playing: self.player.pause()
        else: self.player.play()

    def position(self, value):
        self.seek.blockSignals(True); self.seek.setValue(value); self.seek.blockSignals(False)
        self.wave.position = value; self.wave.update()
        self.time.setText(f'{clock(value / 1000)} / {clock(self.report["duration"])}')

    def refresh_output(self):
        device = QMediaDevices.defaultAudioOutput()
        identity = None if device.isNull() else bytes(device.id())
        if identity != self.player.device_id:
            self.player.pause(); self.player.set_device(device)
        self.output.setText('Output: ' + device.description() if not device.isNull() else 'No audio output available')
        self.play.setEnabled(self.player.sink is not None)

    def closeEvent(self, event):
        self.player.close(); event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    bundled = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'example'
    parser.add_argument('report', nargs='?', type=Path, default=bundled / 'report.json')
    arguments = parser.parse_args()
    report = json.loads(arguments.report.read_text())
    local = arguments.report.parent
    folder = local if (local / 'original.wav').is_file() else Path(report['folder'])
    app = QApplication(sys.argv[:1]); app.setApplicationName('DJ Separation Trial')
    app.setStyle('Fusion'); app.setFont(T.font()); app.setStyleSheet(T.stylesheet())
    window = SeparationTrial(report, folder); window.show()
    return app.exec()


if __name__ == '__main__': raise SystemExit(main())
