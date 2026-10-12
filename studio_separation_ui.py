"""Draft, audition and apply musical-part reduction in one existing blend."""
import copy

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                              QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

import djmix
from studio_blend_choices import ChoiceTransport
from studio_loop_ui import ExtensionWave
from studio_separation import MODES, available, plan_separation, prepare_separation, publish_separation
from studio_separation_comparison import prepare_comparison
from studio_widgets import clock


class SeparationDialog(QDialog):
    def __init__(self, studio):
        super().__init__(studio)
        self.studio, self.index = studio, studio.selected_index()
        self.baseline = copy.deepcopy(studio.session)
        self.track = self.baseline.tracks[self.baseline.clips[self.index].track_id]
        self.busy = self.closing = False
        self.cancel = self.result = self.report = self.plan = self.help_window = None
        self.cache_paths = []
        self.side = 'intro' if self.index > 0 else 'outro'; self.mode = 'instrumental'
        self.setWindowTitle('Reduce competing musical parts')
        self.setWindowModality(Qt.WindowModality.WindowModal); self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(880, 710); self.setMinimumSize(760, 650)
        self.player = ChoiceTransport(self); self.player.set_loop(True); self.player.set_volume(studio.volume.value())
        self.devices = QMediaDevices(self)
        box = QVBoxLayout(self); box.setContentsMargins(22, 18, 22, 18); box.setSpacing(11)
        title = QLabel('Make more room in this blend'); title.setProperty('role', 'title'); box.addWidget(title)
        song = QLabel(self.track.name); song.setTextFormat(Qt.TextFormat.PlainText); song.setWordWrap(True); box.addWidget(song)
        note = QLabel('Try reducing this song’s voice or keeping mostly its drums while another song overlaps it. '
                      'Separation can leave unwanted sounds or remove wanted ones. Compare the actual blend before applying.')
        note.setWordWrap(True); box.addWidget(note)
        self.settings = QWidget(); settings = QVBoxLayout(self.settings); settings.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout(); self.sides = QButtonGroup(self)
        for side, pair in (('intro', self.index), ('outro', self.index + 1)):
            exists = 0 < pair < len(self.baseline.clips)
            button = QPushButton(f'Blend {pair} → {pair + 1} · {"beginning" if side == "intro" else "ending"}' if exists else f'No {"previous" if side == "intro" else "next"} blend')
            button.setCheckable(True); button.setChecked(self.side == side); button.setEnabled(exists)
            button.setToolTip('Change only this selected song inside the overlap on this side. The other song is unchanged. Changing overlap length later does not move this saved audio treatment.')
            button.toggled.connect(lambda checked, s=side: self.choose(side=s) if checked else None)
            self.sides.addButton(button); row.addWidget(button)
        settings.addLayout(row)
        row = QHBoxLayout(); self.modes = QButtonGroup(self)
        for mode, label in MODES.items():
            button = QPushButton(label); button.setCheckable(True); button.setChecked(mode == self.mode)
            button.setToolTip('Reduce vocals keeps estimated drums, bass and accompaniment. Keep mostly drums reduces everything except estimated drums. Neither result is guaranteed clean.')
            button.toggled.connect(lambda checked, m=mode: self.choose(mode=m) if checked else None)
            self.modes.addButton(button); row.addWidget(button)
        self.amount = QSpinBox(); self.amount.setRange(25, 100); self.amount.setSingleStep(25); self.amount.setValue(75); self.amount.setSuffix('% reduction')
        self.amount.setAccessibleName('Musical part reduction')
        self.amount.setToolTip('25% keeps more original sound; 100% uses only the estimated kept parts at the center of the overlap. This is the blend amount, not a guarantee of removing that percentage of voices.')
        self.amount.valueChanged.connect(self.changed); row.addWidget(self.amount); settings.addLayout(row); box.addWidget(self.settings)
        self.details = QLabel(); self.details.setTextFormat(Qt.TextFormat.PlainText); self.details.setWordWrap(True); box.addWidget(self.details)
        self.prepare = QPushButton('Prepare blend comparison'); self.prepare.clicked.connect(self.prepare_audio)
        self.prepare.setToolTip('Separate this source passage locally and prepare matched-level versions of the actual mix. Changing mode or amount reuses prepared parts. No mix change until Apply.')
        box.addWidget(self.prepare)
        self.wave = ExtensionWave(('A · Current mix', 'B · Reduced parts'))
        self.wave.setAccessibleName('Current and reduced-parts blend waveforms')
        self.wave.setToolTip('Real waveforms of the actual blend, with both songs playing. Click to seek; Left/Right moves one second and Home returns to the beginning.')
        self.wave.seekRequested.connect(lambda sec: self.player.seek(round(sec * 1000))); box.addWidget(self.wave, 1)
        row = QHBoxLayout(); self.choices = QButtonGroup(self)
        for name, label in (('current', 'A · Current mix'), ('draft', 'B · Reduced parts')):
            button = QPushButton(label); button.setCheckable(True); button.setChecked(name == 'current')
            button.setToolTip('Switch without restarting. Listening levels are matched so a louder version does not seem better. This selection only changes what you hear.')
            button.toggled.connect(lambda checked, n=name: self.select(n) if checked else None)
            self.choices.addButton(button); row.addWidget(button)
        self.play = QPushButton('Listen'); self.play.clicked.connect(self.toggle)
        self.play.setToolTip('Play or pause the selected blend. It repeats for listening; no repeats are added to the mix.')
        row.addWidget(self.play); box.addLayout(row)
        row = QHBoxLayout(); self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.setRange(0, 0)
        self.seek.setSingleStep(1000); self.seek.setPageStep(5000); self.seek.setAccessibleName('Separated blend comparison position')
        self.seek.setToolTip('Seek both versions together. Left/Right moves one second; Page Up/Down moves five seconds.')
        self.seek.valueChanged.connect(self.player.seek); row.addWidget(self.seek, 1)
        self.time = QLabel('0:00'); row.addWidget(self.time); box.addLayout(row)
        self.output = QLabel(); self.output.setWordWrap(True); box.addWidget(self.output)
        self.status = QLabel(); self.status.setTextFormat(Qt.TextFormat.PlainText); self.status.setWordWrap(True); box.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close | QDialogButtonBox.StandardButton.Help)
        self.apply = buttons.button(QDialogButtonBox.StandardButton.Apply); self.apply.setText('Apply reduced-parts copy')
        self.apply.setToolTip('Save a separate recording and use it for this clip only. The original remains in the library. Undo restores the original clip and its settings.')
        self.apply.clicked.connect(self.apply_copy); buttons.rejected.connect(self.reject); buttons.helpRequested.connect(self.show_help)
        buttons.button(QDialogButtonBox.StandardButton.Close).setToolTip('Discard this draft; cancel preparation first if it is running.')
        buttons.button(QDialogButtonBox.StandardButton.Help).setToolTip('Explain separation, artifacts, saved copies and Undo in the offline guide.')
        box.addWidget(buttons)
        self.help_shortcut = QShortcut(QKeySequence('F1'), self); self.help_shortcut.activated.connect(self.show_help)
        self.player.positionChanged.connect(self.position)
        self.player.playingChanged.connect(lambda playing: self.play.setText('Pause' if playing else 'Listen'))
        self.player.errorOccurred.connect(self.status.setText)
        self.devices.audioOutputsChanged.connect(self.refresh_output); self.finished.connect(self.cleanup)
        for widget in self.findChildren(QWidget):
            widget.setProperty('helpTopic', 'separation')
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton): widget.setAutoDefault(False)
        self.changed(); self.refresh_output()

    def choose(self, *, side=None, mode=None):
        if side: self.side = side
        if mode: self.mode = mode
        self.changed()

    def changed(self, *_):
        self.player.close(); self.player.mixer = None
        self.result = self.report = self.plan = None; self.cache_paths = []
        self.wave.report = None; self.wave.update(); self.studio.update_storage_pins()
        with QSignalBlocker(self.seek): self.seek.setRange(0, 0)
        try:
            self.plan = plan_separation(self.baseline, self.index, self.side, self.mode, self.amount.value())
            self.details.setText(f'Changes only {clock(self.plan["start"] / djmix.SR)}–{clock(self.plan["end"] / djmix.SR)} in this source song, '
                                 f'with smooth edges. Cues, tempo, pitch and song positions stay as set. The original remains in the library.')
            self.status.setText('Prepare and listen for leftover voice, missing melody, softened drums or a watery sound.' if available() else
                                'Local separation is not installed in this edition. The offline guide explains the optional separation build.')
        except (djmix.MixError, OSError) as error: self.details.setText(str(error)); self.status.setText('Choose another blend or adjust its settings first.')
        self.update_controls()

    def update_controls(self):
        self.settings.setEnabled(not self.busy)
        self.prepare.setEnabled(not self.busy and self.plan is not None and available())
        ready = self.report is not None and not self.busy
        self.apply.setEnabled(ready); self.play.setEnabled(ready and self.player.sink is not None); self.seek.setEnabled(ready)
        for button in self.choices.buttons(): button.setEnabled(ready)

    def prepare_audio(self):
        if self.busy or self.plan is None: return
        plan = copy.deepcopy(self.plan); self.busy = True; self.player.pause(); self.update_controls()
        def work(progress, emit):
            result = prepare_separation(self.baseline, self.index, plan['side'], plan['mode'], plan['amount'], self.studio.data / 'audio-cache', progress)
            return result, prepare_comparison(self.baseline, result, self.studio.data / 'audio-cache', progress)
        def finished(value):
            self.busy = False
            if self.closing or (self.cancel and self.cancel.is_set()): self.failed('Operation cancelled.'); return
            self.result, self.report = value
            self.cache_paths = [self.result['folder'], self.result['parts_folder'], self.report['folder']]; self.studio.update_storage_pins()
            try: self.player.load(self.report['folder'])
            except (OSError, ValueError, RuntimeError) as error: self.report = None; self.failed(str(error)); return
            self.wave.report = self.report; self.wave.update()
            with QSignalBlocker(self.seek): self.seek.setRange(0, round(self.report['duration'] * 1000))
            self.position(0); self.status.setText('Ready. Switch A/B while listening. Both songs and the normal mix processing are included. Apply only if this improves the blend.')
            self.refresh_output()
        self.cancel = self.studio.job(work, finished, rendering=True, cancellable=True, failed_callback=self.failed, progress_callback=self.status.setText)

    def apply_copy(self):
        if self.busy or self.result is None or self.report is None: return
        if self.studio.session.to_dict() != self.baseline.to_dict(): self.failed('The mix changed. Close and reopen this tool before applying.'); return
        self.player.pause(); self.busy = True; self.update_controls()
        def work(progress, emit): return publish_separation(self.result, self.studio.data / 'media' / 'separation', progress)
        def finished(replacement):
            self.busy = False
            if self.closing or (self.cancel and self.cancel.is_set()): self.failed('Operation cancelled.'); return
            try:
                if self.studio.apply_separation(self.baseline, self.result, replacement): self.accept()
                else: self.failed('The mix could not accept the copy. Reopen this tool and try again.')
            except (djmix.MixError, OSError) as error: self.failed(str(error))
        self.cancel = self.studio.job(work, finished, rendering=True, cancellable=True, failed_callback=self.failed, progress_callback=self.status.setText)

    def select(self, name): self.player.select(name); self.wave.variant = name; self.wave.update()

    def toggle(self): self.player.pause() if self.player.playing else self.player.play()

    def position(self, milliseconds):
        with QSignalBlocker(self.seek): self.seek.setValue(milliseconds)
        self.time.setText(f'{clock(milliseconds / 1000)} / {clock(self.seek.maximum() / 1000)}')
        self.wave.position = milliseconds / 1000; self.wave.update()

    def refresh_output(self):
        preferred = self.studio.output_preference
        device = QMediaDevices.defaultAudioOutput() if preferred == 'system' else next((d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == preferred), None)
        identity = None if device is None or device.isNull() else bytes(device.id())
        if identity != self.player.device_id: self.player.pause(); self.player.set_device(device)
        self.output.setText('Output: ' + device.description() if self.player.sink is not None else 'Output unavailable. Reconnect it or choose another output in the main window.')
        self.update_controls()

    def failed(self, message):
        self.busy = False; self.status.setText(message); self.update_controls()
        if self.closing: super().reject()

    def show_help(self):
        from studio_help import HelpDialog
        if self.help_window is None: self.help_window = HelpDialog(self)
        self.help_window.open_topic('separation'); self.help_window.show(); self.help_window.raise_()

    def reject(self):
        self.player.close()
        if self.busy:
            self.closing = True
            if self.cancel: self.cancel.set()
            self.status.setText('Cancelling before closing…'); return
        super().reject()

    def cleanup(self, *_):
        self.player.close(); self.player.mixer = None; self.cache_paths = []; self.studio.update_storage_pins()
        if self.help_window is not None: self.help_window.close()
