"""Native, isolated listening trial for repeating complete drum bars.

Run with a saved .djmix project; it only reads song/grid metadata from that file.
Prepared excerpts live under --cache. No mix, cues, or source files are changed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QStandardPaths, QThread, Qt, Signal, QUrl
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog, QHBoxLayout,
                              QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from studio_blend_choices import ChoiceTransport
from studio_drum_loops import loop_choices, prepare_loop
from studio_engine import Session
from studio_process import cancel_on
from studio_storage import StorageManager, cache_root
from studio_widgets import clock
import studio_theme as T


class LoopJob(QThread):
    progress = Signal(str)

    def __init__(self, track, choice, repeats, cache, parent):
        super().__init__(parent)
        self.arguments = track, choice, repeats, cache
        self.cancel = threading.Event()
        self.result, self.error = None, None

    def run(self):
        try:
            with cancel_on(self.cancel):
                self.result = prepare_loop(*self.arguments, progress=self.progress.emit)
        except Exception as error:
            self.error = str(error)


class LoopWave(QWidget):
    seek = Signal(float)

    def __init__(self):
        super().__init__()
        self.report, self.position, self.source = None, 0., False
        self.setMinimumHeight(140)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Loop trial waveform')
        self.setToolTip('Actual audio amplitude with repeat boundaries marked. Click to seek. Left/Right moves one second; Home returns to the start. The slider below offers the same keyboard controls.')

    def duration(self):
        return ((self.report['source_frames'] / 48000 if self.source else self.report['duration'])
                if self.report else 0)

    def paintEvent(self, event):
        p = QPainter(self); p.fillRect(self.rect(), QColor(T.FIELD))
        if not self.report: return
        samples = self.report['source_wave' if self.source else 'wave']
        middle, height = self.height() / 2, (self.height() - 36) / 2
        scale = max(.001, max(samples))
        p.setPen(QPen(QColor('#5CCBEA'), 1))
        for i, value in enumerate(samples):
            x, amplitude = round(i / max(1, len(samples) - 1) * self.width()), value / scale * height
            p.drawLine(x, round(middle - amplitude), x, round(middle + amplitude))
        if not self.source:
            for i, seconds in enumerate(self.report['join_times'], 1):
                x = round(seconds / self.duration() * self.width())
                p.setPen(QColor(T.ACCENT)); p.drawLine(x, 0, x, self.height())
                p.drawText(x + 4, 15, f'Repeat {i}')
        p.setPen(QPen(QColor(T.TEXT), 2))
        x = round(self.position / self.duration() * self.width())
        p.drawLine(x, 0, x, self.height())

    def mousePressEvent(self, event):
        if self.report and event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(); self.seek.emit(self.duration() * event.position().x() / max(1, self.width()))

    def keyPressEvent(self, event):
        if self.report and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home):
            seconds = self.position + (-1 if event.key() == Qt.Key.Key_Left else 1)
            if event.key() == Qt.Key.Key_Home: seconds = 0
            self.seek.emit(max(0, min(self.duration(), seconds))); event.accept()
        else: super().keyPressEvent(event)


class LoopTrial(QDialog):
    def __init__(self, session, cache, example=None):
        super().__init__()
        self.session, self.cache, self.job = session, Path(cache), None
        self.storage = StorageManager(cache_root(self.cache))
        self.source_paths = [t.path for t in session.tracks.values()]
        self.storage.pin(self.source_paths)
        self.report, self.closing, self.busy = None, False, False
        self.source_mode = False
        self.last_variant = 'draft'
        self.setWindowTitle('DJ Studio · Drum-loop listening trial')
        self.resize(930, 690); self.setMinimumSize(740, 620)
        self.player = ChoiceTransport(self)
        self.source_player = QMediaPlayer(self); self.source_output = QAudioOutput(self)
        self.source_player.setAudioOutput(self.source_output)
        self.devices = QMediaDevices(self)
        box = QVBoxLayout(self); box.setContentsMargins(22, 20, 22, 20); box.setSpacing(12)
        title = QLabel('Extend a drum passage'); title.setProperty('role', 'title'); box.addWidget(title)
        note = QLabel('Listening prototype · Repeat complete bars to make a longer intro or outro. '
                      'Choose material without prominent voices or melodies. Your songs and working mix stay unchanged.')
        note.setWordWrap(True); box.addWidget(note)
        self.song, self.side, self.bars, self.passage = (QComboBox() for _ in range(4))
        self.song.setAccessibleName('Song for the drum-loop trial')
        self.song.setToolTip('Choose a source song from the supplied project. Songs need detected or manually checked bars; this prototype does not invent a grid.')
        for track in session.tracks.values(): self.song.addItem(track.name, track.id)
        self.side.addItem('Near the beginning', 'intro'); self.side.addItem('Near the ending', 'outro')
        self.side.setAccessibleName('Intro or outro passage')
        self.side.setToolTip('Offer complete loops within the first or last 30% of the song, capped at 90 seconds. Changing this does not trim the source.')
        for bars in (1, 2, 4, 8): self.bars.addItem(f'{bars} bars', bars)
        self.bars.setCurrentIndex(2); self.bars.setAccessibleName('Bars in each repeated loop')
        self.bars.setToolTip('Repeat one complete musical unit. Four or eight bars can preserve a longer drum pattern; listen for fills that become repetitive.')
        self.repeats = QSpinBox(); self.repeats.setRange(1, 8); self.repeats.setValue(2)
        self.repeats.setAccessibleName('Extra copies of the loop')
        self.repeats.setToolTip('Number of extra copies inserted after the original passage. Two extra copies of four bars add eight bars. Preview is limited to three minutes.')
        for text, controls in [('Song', [self.song]), ('Passage', [self.side, self.bars]), ('Start', [self.passage]), ('Extra copies', [self.repeats])]:
            row = QHBoxLayout(); row.addWidget(QLabel(text))
            for control in controls: row.addWidget(control, 1)
            box.addLayout(row)
        self.passage.setAccessibleName('First bar of the source loop')
        self.passage.setToolTip('Select the exact source bars to repeat. Drum-led labels are tentative observations, not proof of a drum-only passage. Listen to the original and the return to the song.')
        self.detail = QLabel(); self.detail.setWordWrap(True); box.addWidget(self.detail)
        self.wave = LoopWave(); box.addWidget(self.wave, 1)
        modes = QHBoxLayout(); self.group = QButtonGroup(self)
        self.raw, self.smooth = QPushButton('A · Direct repeats'), QPushButton('B · Smoothed joins')
        for button, mode in [(self.raw, 'current'), (self.smooth, 'draft')]:
            button.setCheckable(True); self.group.addButton(button); modes.addWidget(button)
            button.setToolTip('Switch repeats while playback keeps running on the same clock. Both have identical duration and gain. B blends five milliseconds before each repeat into its natural lead-in; attacks stay in place.')
            button.toggled.connect(lambda checked, m=mode: self.select(m) if checked else None)
        self.smooth.setChecked(True); box.addLayout(modes)
        row = QHBoxLayout()
        self.play = QPushButton('Prepare and listen'); self.play.clicked.connect(self.toggle)
        self.play.setToolTip('Prepare only this excerpt, then play the selected repeated version. Completed trials are reused. No BPM change or tempo stabilization is applied in this prototype.')
        self.original = QPushButton('Hear original passage')
        self.original.setCheckable(True); self.group.addButton(self.original)
        self.original.toggled.connect(lambda checked: self.hear_original() if checked else None)
        self.original.setToolTip('Hear the unmodified source passage with two bars of surrounding context. This pauses the repeated version and starts the original excerpt from its beginning.')
        self.join = QPushButton('Hear first repeat'); self.join.clicked.connect(self.hear_join)
        self.join.setToolTip('Start one second before the first repeat boundary. Switch A/B without restarting to compare the joins.')
        for button in (self.play, self.original, self.join): row.addWidget(button)
        box.addLayout(row)
        row = QHBoxLayout(); self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName('Loop trial playback position'); self.seek.setToolTip('Seek within the currently playing original or repeated excerpt.')
        self.seek.valueChanged.connect(self.seek_to); row.addWidget(self.seek, 1)
        self.time = QLabel('0:00 / 0:00'); row.addWidget(self.time); box.addLayout(row)
        row = QHBoxLayout(); self.output = QLabel(); row.addWidget(self.output, 1)
        self.volume = QSlider(Qt.Orientation.Horizontal); self.volume.setRange(0, 100); self.volume.setValue(60)
        self.volume.setMaximumWidth(160); self.volume.setAccessibleName('Loop trial volume')
        self.volume.setToolTip('Listening volume for both versions and the original. Does not change files or the main app’s volume.')
        row.addWidget(QLabel('Volume')); row.addWidget(self.volume); box.addLayout(row)
        self.status = QLabel('Choose a passage, then listen. No mix has been changed.'); self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(self.status)
        row = QHBoxLayout(); self.stop = QPushButton('Stop preparing'); self.stop.clicked.connect(self.stop_job)
        self.stop.setToolTip('Cancel this preparation. Completed trials remain cached; no incomplete result is published.')
        row.addWidget(self.stop); row.addStretch(); self.close_button = QPushButton('Close trial')
        self.close_button.setToolTip('Close this separate listening window. Your DJ project and source music remain unchanged.')
        self.close_button.clicked.connect(self.close); row.addWidget(self.close_button); box.addLayout(row)
        for control in (self.song, self.side, self.bars): control.currentIndexChanged.connect(self.refresh_choices)
        self.passage.currentIndexChanged.connect(self.invalidate); self.repeats.valueChanged.connect(self.invalidate)
        self.player.playingChanged.connect(self.play_state)
        self.source_player.playbackStateChanged.connect(lambda _: self.play_state())
        self.player.positionChanged.connect(lambda ms: self.position(ms) if not self.source_mode else None)
        self.source_player.positionChanged.connect(lambda ms: self.position(ms) if self.source_mode else None)
        self.player.errorOccurred.connect(self.status.setText)
        self.source_player.errorOccurred.connect(lambda error, message: self.status.setText(message))
        self.wave.seek.connect(lambda seconds: self.seek_to(round(seconds * 1000)))
        self.volume.valueChanged.connect(self.set_volume); self.devices.audioOutputsChanged.connect(self.refresh_output)
        # Start with a song for which a real grid is available, never fabricate one.
        for i in range(self.song.count()):
            if loop_choices(session.tracks[self.song.itemData(i)]): self.song.setCurrentIndex(i); break
        if example:
            for i in range(self.song.count()):
                if session.tracks[self.song.itemData(i)].path == example['source'][0]: self.song.setCurrentIndex(i); break
            self.side.setCurrentIndex(self.side.findData(example['choice']['side']))
            self.bars.setCurrentIndex(self.bars.findData(example['choice']['bars']))
            self.repeats.setValue(example['repeats'])
        self.refresh_choices(); self.set_volume(self.volume.value()); self.refresh_output()
        if example:
            for i in range(self.passage.count()):
                if self.passage.itemData(i)['start'] == example['choice']['start']: self.passage.setCurrentIndex(i); break
        for widget in self.findChildren(QWidget):
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton): widget.setAutoDefault(False)

    def refresh_choices(self, *_):
        self.passage.blockSignals(True); self.passage.clear()
        track = self.session.tracks.get(self.song.currentData())
        if track:
            for choice in loop_choices(track, self.bars.currentData(), self.side.currentData()):
                choice = {**choice, 'side': self.side.currentData()}
                self.passage.addItem(f'{clock(choice["start"], True)}–{clock(choice["end"], True)} · {choice["evidence"]}', choice)
        self.passage.blockSignals(False); self.invalidate()

    def invalidate(self, *_):
        self.player.close(); self.player.mixer = None
        self.source_player.stop(); self.source_player.setSource(QUrl())
        self.storage.pin(self.source_paths)
        self.report = None; self.source_mode = False
        self.raw.setChecked(self.last_variant == 'current'); self.smooth.setChecked(self.last_variant == 'draft')
        self.wave.report = None; self.wave.update(); self.position(0)
        choice = self.passage.currentData()
        if choice:
            extra = self.repeats.value(); seconds = (choice['end'] - choice['start']) * extra
            self.detail.setText(f'Add {choice["bars"] * extra} bars ({seconds:.1f} seconds) at original speed. '
                                f'{"Manual reference" if choice["manual"] else "Detected bar grid"}; listen to verify beat alignment. '
                                + ('Bar spacing varies noticeably; repeating does not straighten it. ' if choice['uneven'] else '')
                                + 'The original continuation follows the last repeat.')
        else:
            self.detail.setText('No complete bars available here. In DJ Studio, use Song cues → Analyze song or Check beats and BPM, save the project, then reopen this trial. Try a shorter loop or the other end if only a few bars are available.')
        self.status.setText('Choose a passage, then listen. Drum-only content is not guaranteed.')
        self.update_controls()

    def update_controls(self):
        for control in (self.song, self.side, self.bars, self.passage, self.repeats): control.setEnabled(not self.busy)
        ready = self.report is not None and not self.busy
        self.raw.setEnabled(ready); self.smooth.setEnabled(ready)
        self.original.setEnabled(ready); self.join.setEnabled(ready); self.seek.setEnabled(ready)
        self.play.setEnabled(not self.busy and self.passage.currentData() is not None and self.device_available)
        self.play_state(); self.stop.setVisible(self.busy)

    @property
    def device_available(self):
        return not QMediaDevices.defaultAudioOutput().isNull()

    def toggle(self):
        if self.busy: return
        if self.report is None:
            track = self.session.tracks[self.song.currentData()]
            self.busy = True; self.update_controls()
            self.job = LoopJob(track, self.passage.currentData(), self.repeats.value(), self.cache, self)
            self.job.progress.connect(self.status.setText); self.job.finished.connect(self.prepared); self.job.start()
        elif self.source_mode:
            if self.source_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState: self.source_player.pause()
            else: self.source_player.play()
        else:
            self.player.pause() if self.player.playing else self.player.play()

    def prepared(self):
        job = self.job; self.job = None; self.busy = False
        try:
            if self.closing: self.close(); return
            if job.error or job.cancel.is_set():
                self.status.setText(job.error or 'Preparation cancelled.'); return
            self.report = job.result; self.wave.report = self.report
            with self.storage.gate():
                self.storage.pin([*self.source_paths, self.report['folder']], gate_held=True)
                self.player.load(self.report['folder']); self.player.set_loop(False)
                self.source_player.setSource(QUrl.fromLocalFile(str(Path(self.report['folder']) / 'source.wav')))
            self.select(self.last_variant)
            self.refresh_output(); self.position(0); self.player.play()
            self.status.setText('A/B switches on one clock. Listen for clicks, awkward fills, repeated words and the return to the original song. No files or cues in your mix have changed.')
            self.storage.prune()
        except Exception as error:
            self.report = None; self.status.setText(str(error))
        finally:
            job.deleteLater(); self.update_controls()

    def select(self, variant):
        if not self.report: return
        self.last_variant = variant
        was_source = self.source_mode
        self.source_player.stop(); self.source_mode = False; self.wave.source = False
        self.raw.setChecked(variant == 'current'); self.smooth.setChecked(variant == 'draft')
        self.player.select(variant); self.position(self.player.position()); self.wave.update()
        if was_source: self.player.play()

    def hear_original(self):
        if not self.report: return
        self.original.setChecked(True)
        self.player.pause(); self.source_mode = True; self.wave.source = True
        self.source_player.setPosition(0); self.position(0); self.source_player.play()

    def hear_join(self):
        if not self.report: return
        self.select(self.last_variant)
        self.player.seek(round(max(0, self.report['join_times'][0] - 1) * 1000)); self.player.play()

    def seek_to(self, milliseconds):
        if self.report:
            self.source_player.setPosition(milliseconds) if self.source_mode else self.player.seek(milliseconds)

    def position(self, milliseconds):
        duration = self.wave.duration()
        self.seek.blockSignals(True); self.seek.setRange(0, round(duration * 1000)); self.seek.setValue(milliseconds); self.seek.blockSignals(False)
        self.time.setText(f'{clock(milliseconds / 1000)} / {clock(duration)}')
        self.wave.position = milliseconds / 1000; self.wave.update()
        self.wave.setAccessibleDescription(f'Position {clock(milliseconds / 1000)} of {clock(duration)}. ' + self.wave.toolTip())

    def play_state(self, *_):
        playing = self.source_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState if self.source_mode else self.player.playing
        self.play.setText('Pause' if playing else 'Listen' if self.report else 'Prepare and listen')

    def set_volume(self, value):
        self.player.set_volume(value / 100); self.source_output.setVolume(value / 100)

    def refresh_output(self):
        device = QMediaDevices.defaultAudioOutput()
        # Pause on a device change so disconnecting headphones cannot send music
        # to newly selected speakers without another explicit Listen action.
        self.player.pause(); self.source_player.pause(); self.player.set_device(device)
        if not device.isNull(): self.source_output.setDevice(device)
        self.output.setText('Output: ' + device.description() if not device.isNull() else 'No audio output. Connect headphones or speakers to listen.')
        self.update_controls()

    def stop_job(self):
        if self.job:
            self.job.cancel.set(); self.status.setText('Stopping preparation…')

    def closeEvent(self, event):
        if self.busy:
            self.closing = True; self.stop_job(); event.ignore(); return
        self.player.close(); self.source_player.stop(); self.source_player.setSource(QUrl())
        self.storage.close(); self.storage.prune(); event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    bundled = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'example'
    parser.add_argument('project', type=Path, nargs='?', default=bundled / 'Trial source.djmix')
    parser.add_argument('--example', type=Path, default=bundled / 'example-report.json')
    parser.add_argument('--cache', type=Path)
    arguments = parser.parse_args()
    session = Session.from_dict(json.loads(arguments.project.read_text()))
    app = QApplication(sys.argv[:1]); app.setApplicationName('DJ Drum Loop Trial')
    app.setStyle('Fusion'); app.setFont(T.font()); app.setStyleSheet(T.stylesheet())
    cache = arguments.cache or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / 'audio-cache'
    example = json.loads(arguments.example.read_text()) if arguments.example.is_file() else None
    window = LoopTrial(session, cache, example); window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
