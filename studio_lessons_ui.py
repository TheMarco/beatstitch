"""Short, self-contained A/B lessons with no project mutations."""
import numpy as np
from PySide6.QtCore import QEvent, QPointF, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QCheckBox, QComboBox, QDialog, QHBoxLayout,
                              QLabel, QPushButton, QSlider, QVBoxLayout, QWidget)

import djmix
import studio_theme as T
from studio_blend_choices import ChoiceTransport
from studio_lessons import LESSONS, lesson_audio
from studio_widgets import clock


class LessonWaves(QWidget):
    seekRequested = Signal(int)

    def __init__(self):
        super().__init__()
        self.peaks = []; self.seconds = 0.; self.position = 0.; self.variant = 'current'
        self.setMinimumHeight(150)
        self.setAccessibleName('Real waveforms of both listening examples')
        self.setToolTip('A and B show their real audio on the same time axis. Click to seek both versions. The keyboard-accessible position slider does the same thing.')

    def set_audio(self, mixer):
        self.seconds = mixer.length / djmix.SR; self.position = 0
        self.peaks = []
        for audio in (mixer.outgoing, mixer.incoming):
            mono = np.max(np.abs(audio), axis=1); size = max(1, int(np.ceil(len(mono) / 1000)))
            self.peaks.append(np.pad(mono, (0, (-len(mono)) % size)).reshape(-1, size).max(axis=1))
        self.scale = max(float(max(p.max() for p in self.peaks)), .001); self.update()

    def paintEvent(self, event):
        painter = QPainter(self); painter.fillRect(self.rect(), QColor(T.FIELD))
        if not self.peaks: return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right = 28, self.width() - 10
        height = (self.height() - 24) / 2
        for row, peaks in enumerate(self.peaks):
            center = 10 + (row + .5) * height
            selected = self.variant == ('current' if row == 0 else 'draft')
            color = QColor(T.ACCENT if selected else T.TEXT_2)
            painter.setPen(color); painter.drawText(8, round(center + 4), 'A' if row == 0 else 'B')
            path = QPainterPath(); path.moveTo(left, center)
            for i, peak in enumerate(peaks):
                path.lineTo(left + i * (right - left) / max(1, len(peaks) - 1), center - peak / self.scale * height * .4)
            for i in range(len(peaks) - 1, -1, -1):
                path.lineTo(left + i * (right - left) / max(1, len(peaks) - 1), center + peaks[i] / self.scale * height * .4)
            path.closeSubpath(); painter.fillPath(path, color)
        painter.setPen(QColor(T.TEXT_2)); painter.drawText(left, self.height() - 3, '0:00')
        painter.drawText(max(left, right - 40), self.height() - 3, clock(self.seconds))
        x = left + self.position / max(.001, self.seconds) * (right - left)
        painter.setPen(QPen(QColor(T.TEXT), 1)); painter.drawLine(QPointF(x, 4), QPointF(x, self.height() - 20))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.peaks:
            fraction = min(1, max(0, (event.position().x() - 28) / max(1, self.width() - 38)))
            self.seekRequested.emit(round(fraction * self.seconds * 1000)); event.accept()
        else: super().mousePressEvent(event)


class ListeningLessons(QDialog):
    def __init__(self, studio):
        super().__init__(studio)
        self.studio = studio; self.help_window = None; self.ready = False; self.duration = 0
        self.setWindowTitle('Hear what makes a blend work')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(660, 620); self.resize(800, 740)
        self.player = ChoiceTransport(self); self.devices = QMediaDevices(self)
        box = QVBoxLayout(self); box.setContentsMargins(22, 20, 22, 20); box.setSpacing(10)
        def label(value, role=None):
            result = QLabel(value); result.setWordWrap(True); result.setTextFormat(Qt.TextFormat.PlainText)
            if role: result.setProperty('role', role)
            box.addWidget(result); return result
        label('Hear what makes a blend work', 'title')
        label('Three short examples. Play, then switch A/B while the music keeps going. Your mix stays unchanged.', 'secondary')
        self.lesson = QComboBox(); self.lesson.setAccessibleName('Listening lesson')
        self.lesson.setToolTip('Choose a 16-second example. Changing lessons stops playback; press Listen when ready.')
        for entry in LESSONS: self.lesson.addItem(entry['title'], entry['id'])
        box.addWidget(self.lesson)
        self.listen_for = label('')
        row = QHBoxLayout(); self.choices = QButtonGroup(self)
        for variant in ('current', 'draft'):
            button = QPushButton(); button.setCheckable(True); button.setMinimumHeight(38)
            button.setToolTip('Switch at the current listening position without restarting. A and B use matched overall loudness.')
            button.toggled.connect(lambda checked, v=variant: self.select(v) if checked else None)
            self.choices.addButton(button); row.addWidget(button)
        box.addLayout(row)
        self.waves = LessonWaves(); box.addWidget(self.waves, 1)
        self.waves.seekRequested.connect(self.player.seek)
        row = QHBoxLayout()
        self.play = QPushButton('Listen'); self.play.setToolTip('Play or pause this lesson (Space). No audio plays until you choose Listen.')
        self.play.clicked.connect(self.toggle); row.addWidget(self.play)
        self.restart = QPushButton('Start again'); self.restart.setToolTip('Move both examples back to the start (Home), keeping the current playing/paused state.')
        self.restart.clicked.connect(lambda: self.player.seek(0)); row.addWidget(self.restart)
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.setAccessibleName('Listening lesson position')
        self.seek.setToolTip('Seek both versions together. Left/Right moves by a quarter second; Page Up/Down by two seconds.')
        self.seek.setSingleStep(250); self.seek.setPageStep(2000)
        self.seek.sliderMoved.connect(self.player.seek)
        self.seek.valueChanged.connect(lambda ms: self.player.seek(ms) if not self.seek.isSliderDown() else None)
        row.addWidget(self.seek, 1); self.time = QLabel('0:00 / 0:16'); row.addWidget(self.time)
        self.loop = QCheckBox('Repeat'); self.loop.setChecked(True); self.loop.toggled.connect(self.player.set_loop)
        self.loop.setToolTip('Repeat this example on the same audio clock. It does not loop anything in your mix.')
        row.addWidget(self.loop); box.addLayout(row)
        self.change = label('', 'secondary')
        self.try_hint = label('')
        self.status = label('')
        row = QHBoxLayout(); row.addWidget(QLabel('Listening volume'))
        self.volume = QSlider(Qt.Orientation.Horizontal); self.volume.setRange(0, 100)
        self.volume.setValue(studio.volume.value()); self.volume.setAccessibleName('Lesson listening volume')
        self.volume.setToolTip('Volume for these examples only. Your mix playback volume and export are unchanged.')
        self.volume.valueChanged.connect(lambda n: self.player.set_volume(n / 100)); row.addWidget(self.volume, 1)
        self.instructions = QPushButton('Show me the steps'); self.instructions.setToolTip('Open the offline guide for the selected problem. Pause the example while reading.')
        self.instructions.clicked.connect(self.show_help); row.addWidget(self.instructions)
        self.close_button = QPushButton('Back to my mix'); self.close_button.setToolTip('Stop the lesson and return to your unchanged arrangement and playhead.')
        self.close_button.clicked.connect(self.reject); row.addWidget(self.close_button); box.addLayout(row)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playingChanged.connect(lambda playing: self.play.setText('Pause' if playing else 'Listen'))
        self.player.errorOccurred.connect(self.failed)
        self.devices.audioOutputsChanged.connect(self.refresh_output)
        self.lesson.currentIndexChanged.connect(self.load_lesson)
        self.finished.connect(self.cleanup)
        self.shortcuts = []
        for key, action in [('Space', self.toggle), ('Home', lambda: self.player.seek(0)),
                            ('A', lambda: self.choices.buttons()[0].click()),
                            ('B', lambda: self.choices.buttons()[1].click()), ('F1', self.show_help)]:
            shortcut = QShortcut(QKeySequence(key), self); shortcut.activated.connect(action); self.shortcuts.append(shortcut)
        for control in self.findChildren(QWidget):
            control.installEventFilter(self)
            if control.toolTip(): control.setAccessibleDescription(control.toolTip())
            if isinstance(control, (QPushButton, QComboBox, QCheckBox, QSlider)): control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            if isinstance(control, QPushButton): control.setAutoDefault(False)
        self.player.set_volume(self.volume.value() / 100); self.player.set_loop(True)
        self.load_lesson()

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Space
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and isinstance(watched, (QAbstractButton, QComboBox))):
            event.accept(); return True
        return super().eventFilter(watched, event)

    def load_lesson(self, *_):
        self.player.pause(); self.ready = False
        entry = LESSONS[self.lesson.currentIndex()]
        self.listen_for.setText(entry['listen']); self.change.setText(entry['change']); self.try_hint.setText(entry['try'])
        for button, key in zip(self.choices.buttons(), ('a', 'b')): button.setText(entry[key])
        try:
            report = lesson_audio(entry['id']); self.duration = report['duration']
            self.player.load(report['folder']); self.waves.set_audio(self.player.mixer)
            with QSignalBlocker(self.seek): self.seek.setRange(0, round(self.duration * 1000))
            self.choices.buttons()[0].setChecked(True); self.select('current')
            self.ready = True; self.position_changed(0); self.refresh_output()
        except (djmix.MixError, ValueError, OSError, RuntimeError) as error:
            self.player.close(); self.waves.peaks = []; self.waves.update(); self.status.setText(str(error))
        self.update_controls()

    def update_controls(self):
        self.play.setEnabled(self.ready and self.player.sink is not None)
        for control in (self.seek, self.restart, self.loop, *self.choices.buttons()): control.setEnabled(self.ready)

    def select(self, variant):
        self.player.select(variant); self.waves.variant = variant; self.waves.update()

    def toggle(self):
        if self.ready and self.player.sink is not None:
            self.player.pause() if self.player.playing else self.player.play()

    def refresh_output(self):
        preference = self.studio.output_preference
        device = QMediaDevices.defaultAudioOutput() if preference == 'system' else next(
            (d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == preference), None)
        self.player.set_device(device)
        if self.ready:
            self.status.setText('Output: ' + device.description() if self.player.sink is not None else
                                'Output unavailable. Reconnect it, or return to the mix and choose another output. Playback is paused.')
        self.update_controls()

    def position_changed(self, ms):
        if not self.seek.isSliderDown():
            with QSignalBlocker(self.seek): self.seek.setValue(ms)
        self.time.setText(f'{clock(ms / 1000)} / {clock(self.duration)}')
        self.waves.position = ms / 1000; self.waves.update()

    def failed(self, message):
        self.status.setText('Could not play this example: ' + message)

    def show_help(self):
        from studio_help import HelpDialog
        self.player.pause()
        if self.help_window is None: self.help_window = HelpDialog(self)
        self.help_window.open_topic(LESSONS[self.lesson.currentIndex()]['topic'])
        self.help_window.show(); self.help_window.raise_(); self.help_window.activateWindow()

    def cleanup(self, *_):
        self.player.close()
        if self.help_window is not None: self.help_window.close()
