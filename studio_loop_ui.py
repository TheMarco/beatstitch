"""Native draft/compare/apply workflow for extending a selected drum passage."""
import copy

from PySide6.QtCore import QPoint, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox,
                              QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QSlider, QSpinBox,
                              QVBoxLayout, QWidget)

import djmix
import studio_theme as T
from studio_blend_choices import ChoiceTransport
from studio_engine import Track, layout
from studio_loop_comparison import prepare_comparison
from studio_loop_extension import choices_for_clip, draft_with_copy, extension_plan, prepare_extension, publish_extension
from studio_widgets import clock


class LoopPicker(QPushButton):
    """Native menu opened by a real button press, including accessibility presses."""
    currentIndexChanged = Signal(int)

    def __init__(self, accessible_label):
        super().__init__()
        self.accessible_label = accessible_label
        self.items, self.index = [], -1
        self.popup = QMenu(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.clicked.connect(lambda: self.popup.popup(self.mapToGlobal(QPoint(0, self.height()))))

    def addItem(self, title, value):
        index = len(self.items); action = self.popup.addAction(title); action.setCheckable(True)
        action.triggered.connect(lambda checked=False, i=index: self.setCurrentIndex(i))
        self.items.append((title, value, action))
        if self.index < 0: self.setCurrentIndex(0)

    def clear(self):
        self.popup.clear(); self.items = []; self.index = -1; self.setText('No passages available')
        self.setAccessibleName(self.accessible_label + ': no passages available')

    def currentData(self): return self.items[self.index][1] if self.index >= 0 else None

    def setCurrentIndex(self, index):
        if not 0 <= index < len(self.items) or not self.items[index][2].isEnabled(): return
        changed = index != self.index
        self.index = index; self.setText(self.items[index][0] + '  ▾')
        self.setAccessibleName(self.accessible_label + ': ' + self.items[index][0])
        for i, (_, _, action) in enumerate(self.items): action.setChecked(i == index)
        if changed: self.currentIndexChanged.emit(index)

    def setItemEnabled(self, index, enabled): self.items[index][2].setEnabled(enabled)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = -1 if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up) else 1
            index = self.index + step
            while 0 <= index < len(self.items) and not self.items[index][2].isEnabled(): index += step
            self.setCurrentIndex(index); event.accept()
        else: super().keyPressEvent(event)


class ExtensionWave(QWidget):
    seekRequested = Signal(float)

    def __init__(self, labels=('A · Current', 'B · Extended')):
        super().__init__()
        self.labels = labels
        self.report, self.position, self.variant = None, 0., 'current'
        self.setMinimumHeight(130)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Current and extended mix waveforms')
        self.setToolTip('Real audio amplitude for both versions. Gold lines mark repeats in the extended version. Click to seek both. Left/Right moves one second; Home returns to the start. The slider offers the same keyboard seeking.')

    def paintEvent(self, event):
        p = QPainter(self); p.fillRect(self.rect(), QColor(T.FIELD))
        if not self.report:
            p.setPen(QColor(T.TEXT_2)); p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Prepare a comparison to see the audio'); return
        duration = self.report['duration']; height = self.height() / 2
        scale = max(.001, *self.report['current_wave'], *self.report['draft_wave'])
        for lane, name, label in ((0, 'current', self.labels[0]), (1, 'draft', self.labels[1])):
            samples = self.report[name + '_wave']; middle = height * (lane + .5)
            p.setPen(QColor('#5CCBEA' if self.variant == name else T.TEXT_3))
            for i, value in enumerate(samples):
                x = round(i / max(1, len(samples) - 1) * self.width())
                amplitude = value / scale * (height / 2 - 16)
                p.drawLine(x, round(middle - amplitude), x, round(middle + amplitude))
            p.setPen(QColor(T.TEXT)); p.drawText(7, round(lane * height + 15), label)
        p.setPen(QPen(QColor(T.ACCENT), 1))
        for t in self.report['join_times']:
            x = round(t / duration * self.width()); p.drawLine(x, round(height), x, self.height())
        p.setPen(QPen(QColor(T.TEXT), 2)); x = round(self.position / duration * self.width())
        p.drawLine(x, 0, x, self.height())

    def mousePressEvent(self, event):
        if self.report and event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self.seekRequested.emit(self.report['duration'] * event.position().x() / max(1, self.width()))

    def keyPressEvent(self, event):
        if self.report and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home):
            seconds = 0 if event.key() == Qt.Key.Key_Home else self.position + (-1 if event.key() == Qt.Key.Key_Left else 1)
            self.seekRequested.emit(max(0, min(self.report['duration'], seconds))); event.accept()
        else: super().keyPressEvent(event)


class LoopExtensionDialog(QDialog):
    def __init__(self, studio):
        super().__init__(studio)
        self.studio, self.index = studio, studio.selected_index()
        self.baseline = copy.deepcopy(studio.session)
        self.track = self.baseline.tracks[self.baseline.clips[self.index].track_id]
        self.busy = self.closing = False
        self.cancel = self.extension = self.report = self.plan = self.help_window = None
        self.cache_paths = []
        self.setWindowTitle('Extend drums')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(920, 760); self.setMinimumSize(760, 650)
        self.player = ChoiceTransport(self); self.devices = QMediaDevices(self)
        self.player.set_volume(studio.volume.value()); self.player.set_loop(True)
        box = QVBoxLayout(self); box.setContentsMargins(22, 18, 22, 18); box.setSpacing(10)
        title = QLabel('Make room for a longer blend'); title.setProperty('role', 'title'); box.addWidget(title)
        song = QLabel(self.track.name); song.setTextFormat(Qt.TextFormat.PlainText); song.setWordWrap(True); box.addWidget(song)
        note = QLabel('Repeat complete bars near this song’s beginning or ending. Choose a steady drum passage without prominent voices or melodies, then compare before applying.')
        note.setWordWrap(True); box.addWidget(note)
        self.settings = QWidget(); grid = QGridLayout(self.settings); grid.setContentsMargins(0, 0, 0, 0)
        self.side = LoopPicker('Which end'); self.side.addItem('Beginning · intro', 'intro'); self.side.addItem('Ending · outro', 'outro')
        if self.index == 0 and len(self.baseline.clips) > 1: self.side.setCurrentIndex(1)
        self.side.setToolTip('Look near the beginning or ending of the source song, inside its kept Start/End cues. Protected passages are excluded.')
        self.bars = LoopPicker('Loop length')
        for n in (1, 2, 4, 8): self.bars.addItem(f'{n} bars', n)
        self.bars.setCurrentIndex(2)
        self.bars.setToolTip('Length of the passage to repeat. One bar is four beats. Short loops can sound repetitive; longer ones may contain unwanted musical changes.')
        self.repeats = QSpinBox(); self.repeats.setRange(1, 8); self.repeats.setValue(2); self.repeats.setSuffix(' extra copies')
        self.repeats.setToolTip('Number of additional copies, after the original passage. Four bars repeated twice adds eight bars; it does not replace other audio.')
        for col, (text, widget) in enumerate((('Which end?', self.side), ('Loop length', self.bars), ('Add', self.repeats))):
            label = QLabel(text); label.setBuddy(widget); grid.addWidget(label, 0, col); grid.addWidget(widget, 1, col)
            if not isinstance(widget, LoopPicker): widget.setAccessibleName(text)
        self.passage = LoopPicker('Drum passage to repeat')
        self.passage.setToolTip('Complete bars from the song’s detected or manually checked grid. Content labels are suggestions, not proof of drums-only audio. Times refer to the original source.')
        grid.addWidget(self.passage, 2, 0, 1, 3)
        self.smooth = QCheckBox('Smooth joins'); self.smooth.setChecked(True)
        self.smooth.setToolTip('Blend five milliseconds before each repeat into the natural lead-in. The new beat stays in place. Off uses direct repeats; the difference may be subtle.')
        grid.addWidget(self.smooth, 3, 0)
        self.overlap = QSpinBox(); self.overlap.setRange(1, 32); self.overlap.setSuffix(' bars')
        self.overlap.setAccessibleName('Neighboring overlap length')
        self.overlap.setToolTip('Optional new length of the blend on this side. Changing it moves later songs. Both neighbors and protected passages must still fit; no other blend is changed.')
        self.overlap_label = QLabel('Neighboring overlap'); self.overlap_label.setBuddy(self.overlap)
        grid.addWidget(self.overlap_label, 3, 1); grid.addWidget(self.overlap, 3, 2); box.addWidget(self.settings)
        self.details = QLabel(); self.details.setTextFormat(Qt.TextFormat.PlainText); self.details.setWordWrap(True); box.addWidget(self.details)
        row = QHBoxLayout()
        self.check_beats = QPushButton('Check beats and passages…'); self.check_beats.clicked.connect(self.open_cues)
        self.check_beats.setToolTip('Close this unapplied draft and open Song cues. Check beat 1, bars and drum-led passages there, then reopen Extend drums.')
        row.addWidget(self.check_beats); row.addStretch(); box.addLayout(row)
        row = QHBoxLayout(); self.context = LoopPicker('Comparison area'); self.context.addItem('Hear the repeated passage', 'passage')
        self.context.addItem('Hear the neighboring blend', 'blend')
        self.context.setToolTip('Compare the actual processed mix around the repeats or the adjacent blend. Both use project BPM, pitch, fades and blend settings.')
        row.addWidget(self.context)
        self.prepare = QPushButton('Prepare comparison'); self.prepare.clicked.connect(self.prepare_audio)
        self.prepare.setToolTip('Create a cached extended copy and matched-level A/B audio. Preparing does not change the mix. Unchanged source processing is reused.')
        row.addWidget(self.prepare); box.addLayout(row)
        self.wave = ExtensionWave(); self.wave.seekRequested.connect(lambda sec: self.player.seek(round(sec * 1000))); box.addWidget(self.wave, 1)
        self.alignment = QLabel(); self.alignment.setWordWrap(True); self.alignment.setProperty('role', 'secondary'); box.addWidget(self.alignment)
        row = QHBoxLayout(); self.choices = QButtonGroup(self)
        for title, variant in (('A · Current mix', 'current'), ('B · Extended mix', 'draft')):
            button = QPushButton(title); button.setCheckable(True); button.setChecked(variant == 'current')
            button.setToolTip('Switch continuously at the same comparison position, without restarting. This selects what you hear; it does not apply the draft.')
            button.toggled.connect(lambda checked, v=variant: self.select(v) if checked else None)
            self.choices.addButton(button); row.addWidget(button)
        self.play = QPushButton('Listen'); self.play.clicked.connect(self.toggle_play)
        self.play.setToolTip('Play or pause the selected version. Playback repeats this comparison until paused; this listening repeat is not added to the mix.')
        row.addWidget(self.play)
        self.first_join = QPushButton('First repeat'); self.first_join.clicked.connect(self.seek_join)
        self.first_join.setToolTip('Select the extended version and seek one second before its first visible repeat. Press Listen if paused.')
        row.addWidget(self.first_join); box.addLayout(row)
        row = QHBoxLayout(); self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.setAccessibleName('Extension comparison position')
        self.seek.setToolTip('Seek both versions together. Left/Right arrows move the position when this slider has focus.')
        self.seek.setSingleStep(1000); self.seek.setPageStep(5000)
        self.seek.sliderMoved.connect(self.player.seek)
        self.seek.valueChanged.connect(lambda ms: self.player.seek(ms) if not self.seek.isSliderDown() else None)
        row.addWidget(self.seek, 1); self.time = QLabel('0:00'); row.addWidget(self.time); box.addLayout(row)
        self.output = QLabel(); self.output.setWordWrap(True); box.addWidget(self.output)
        self.status = QLabel('Choose a passage and prepare it. Your working mix stays unchanged until Apply.')
        self.status.setTextFormat(Qt.TextFormat.PlainText); self.status.setWordWrap(True); box.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close | QDialogButtonBox.StandardButton.Help)
        self.apply = buttons.button(QDialogButtonBox.StandardButton.Apply); self.apply.setText('Apply extended copy')
        self.apply.setToolTip('Keep a new source copy and replace only this clip in the arrangement. One Undo restores the original clip; the copy remains in the library for reuse.')
        self.apply.clicked.connect(self.apply_extension); buttons.rejected.connect(self.reject); buttons.helpRequested.connect(self.show_help)
        buttons.button(QDialogButtonBox.StandardButton.Close).setToolTip('Discard this draft. If processing is running, cancel it before closing.')
        buttons.button(QDialogButtonBox.StandardButton.Help).setToolTip('Open the offline guide to loop selection, comparison, saved copies and Undo.')
        box.addWidget(buttons)
        self.shortcut = QShortcut(QKeySequence('F1'), self); self.shortcut.activated.connect(self.show_help)
        self.side.currentIndexChanged.connect(self.reload_choices); self.bars.currentIndexChanged.connect(self.reload_choices)
        self.passage.currentIndexChanged.connect(self.settings_changed); self.repeats.valueChanged.connect(self.settings_changed)
        self.smooth.toggled.connect(self.settings_changed); self.overlap.valueChanged.connect(self.settings_changed)
        self.context.currentIndexChanged.connect(self.context_changed)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playingChanged.connect(lambda playing: self.play.setText('Pause' if playing else 'Listen'))
        self.player.errorOccurred.connect(self.status.setText)
        self.devices.audioOutputsChanged.connect(self.refresh_output); self.finished.connect(self.cleanup)
        for widget in self.findChildren(QWidget):
            widget.setProperty('helpTopic', 'drum-loops')
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton): widget.setAutoDefault(False)
            if isinstance(widget, (QPushButton, QSpinBox, QCheckBox, QSlider)): widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.reload_choices(); self.refresh_output()

    def clear_audio(self, clear_extension=True):
        self.player.close(); self.player.mixer = None; self.report = None
        self.wave.report = None; self.wave.update(); self.alignment.clear()
        with QSignalBlocker(self.seek): self.seek.setRange(0, 0)
        if clear_extension: self.extension = None
        self.cache_paths = [self.extension['folder']] if self.extension else []
        self.studio.update_storage_pins()

    def reload_choices(self, *_):
        side = self.side.currentData(); pair = self.index if side == 'intro' else self.index + 1
        neighbor = 0 < pair < len(self.baseline.clips)
        self.overlap.setEnabled(neighbor)
        self.overlap_label.setText(f'Blend {pair} → {pair + 1}' if neighbor else 'No neighbor on this side')
        with QSignalBlocker(self.overlap): self.overlap.setValue(self.baseline.clips[pair].blend_bars if neighbor else 1)
        self.context.setItemEnabled(1, neighbor)
        if not neighbor:
            with QSignalBlocker(self.context): self.context.setCurrentIndex(0)
        with QSignalBlocker(self.passage):
            self.passage.clear()
            try: choices = choices_for_clip(self.baseline, self.index, self.bars.currentData(), side)
            except (djmix.MixError, OSError): choices = []
            for choice in choices:
                text = f'{clock(choice["start"])}–{clock(choice["end"])} · {choice["evidence"]}'
                if choice['uneven']: text += ' · uneven bars'
                self.passage.addItem(text, choice)
        self.settings_changed()

    def settings_changed(self, *_):
        self.clear_audio(); self.plan = None
        choice = self.passage.currentData()
        if choice is None:
            self.details.setText('No complete, unprotected bars found inside the kept audio near this end. Try a shorter loop or the other end. In Song cues, check beats or widen Start/End; no grid is invented for you.')
        else:
            try:
                overlap = self.overlap.value() if self.overlap.isEnabled() else None
                plan = extension_plan(self.baseline, self.index, choice, self.repeats.value(), self.smooth.isChecked(), overlap)
                duration = self.track.duration + plan['added_frames'] / djmix.SR
                placeholder = Track('__loop_draft__', '', 'Extended draft', duration, bpm=self.track.bpm, cue_out=duration)
                draft = draft_with_copy(self.baseline, self.index, plan, placeholder)
                from studio_tempo_curve import session_curve
                curve = session_curve(self.baseline)
                delta = float(curve.to_output(layout(draft)[-1]['end'] / djmix.SR) -
                              curve.to_output(layout(self.baseline)[-1]['end'] / djmix.SR))
                text = f'Adds {choice["bars"] * plan["repeats"]} drum bars. Mix becomes {abs(delta):.1f} seconds {"longer" if delta >= 0 else "shorter"}, including overlap changes. '
                text += 'Smooth joins use 5 ms of audio before each repeat. ' if plan['smooth'] else 'Direct joins use no smoothing. '
                if plan['baked_timing']: text += 'Your accepted tempo correction is included in the new copy. '
                text += 'The original stays in your library. Recheck beats and content on the new copy.'
                self.details.setText(text); self.plan = plan
            except (djmix.MixError, OSError) as error: self.details.setText(str(error))
        self.status.setText('Prepare to hear these choices before applying.'); self.update_controls()

    def context_changed(self, *_):
        self.clear_audio(False); self.status.setText('Prepare this comparison area. The extended source copy is reused.'); self.update_controls()

    def update_controls(self):
        self.settings.setEnabled(not self.busy)
        # Disabling/re-enabling the settings parent preserves each child's local state.
        self.context.setEnabled(not self.busy); self.check_beats.setEnabled(not self.busy)
        self.prepare.setEnabled(self.plan is not None and not self.busy)
        self.apply.setEnabled(self.extension is not None and self.report is not None and not self.busy)
        ready = self.report is not None and not self.busy
        self.play.setEnabled(ready and self.player.sink is not None); self.seek.setEnabled(ready)
        self.first_join.setEnabled(ready and bool(self.report['join_times']))
        for button in self.choices.buttons(): button.setEnabled(ready)

    def prepare_audio(self):
        if self.busy or self.plan is None: return
        plan = copy.deepcopy(self.plan); context = self.context.currentData(); existing = self.extension
        self.busy = True; self.player.pause(); self.update_controls()
        def work(progress, emit):
            extension = existing or prepare_extension(self.baseline, self.index, plan['choice'], plan['repeats'],
                         self.studio.data / 'audio-cache', progress, smooth=plan['smooth'], overlap=plan['overlap'])
            return extension, prepare_comparison(self.baseline, extension, self.studio.data / 'audio-cache', progress, context=context)
        def finished(result):
            self.busy = False
            if self.closing or (self.cancel and self.cancel.is_set()): self.failed('Operation cancelled.'); return
            self.extension, self.report = result
            self.cache_paths = [self.extension['folder'], self.report['folder']]; self.studio.update_storage_pins()
            try: self.player.load(self.report['folder'])
            except (OSError, ValueError, RuntimeError) as error:
                self.clear_audio(); self.failed(str(error)); return
            self.wave.report = self.report; self.wave.update(); self.alignment.setText(self.report['note'] + ' Listening levels are matched; export uses the normal mix levels.')
            with QSignalBlocker(self.seek): self.seek.setRange(0, round(self.report['duration'] * 1000))
            self.position_changed(0)
            self.status.setText('Ready. Switch A/B while playing. Listen for a repeating melody, cut-off voice, doubled kicks or an awkward return to the song.')
            self.refresh_output()
        self.cancel = self.studio.job(work, finished, rendering=True, cancellable=True,
                                      failed_callback=self.failed, progress_callback=self.status.setText)

    def select(self, variant):
        self.player.select(variant); self.wave.variant = variant; self.wave.update()

    def seek_join(self):
        if self.report and self.report['join_times']:
            self.choices.buttons()[1].setChecked(True)
            self.player.seek(round(max(0, self.report['join_times'][0] - 1) * 1000))

    def toggle_play(self):
        self.player.pause() if self.player.playing else self.player.play()

    def position_changed(self, milliseconds):
        with QSignalBlocker(self.seek): self.seek.setValue(milliseconds)
        self.time.setText(f'{clock(milliseconds / 1000)} / {clock(self.seek.maximum() / 1000)}')
        self.wave.position = milliseconds / 1000; self.wave.update()

    def refresh_output(self):
        preferred = self.studio.output_preference
        device = QMediaDevices.defaultAudioOutput() if preferred == 'system' else next(
            (d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == preferred), None)
        self.player.set_device(device)
        self.output.setText('Output: ' + device.description() if self.player.sink is not None else
                            'Output unavailable. Reconnect it, or close this window and choose another output.')
        self.update_controls()

    def failed(self, message):
        self.busy = False; self.status.setText(message); self.update_controls()
        if self.closing: super().reject()

    def apply_extension(self):
        if self.busy or self.extension is None or self.report is None: return
        if self.studio.session.to_dict() != self.baseline.to_dict():
            self.status.setText('The mix changed. Close and reopen Extend drums before applying.'); return
        self.player.pause(); self.busy = True; self.update_controls()
        extension = self.extension
        def work(progress, emit):
            return publish_extension(extension, self.studio.data / 'media' / 'loops', progress)
        def finished(replacement):
            self.busy = False
            if self.closing or (self.cancel and self.cancel.is_set()): self.failed('Operation cancelled.'); return
            try:
                if self.studio.apply_loop_extension(self.baseline, extension, replacement): self.accept()
                else: self.failed('The mix could not accept the copy. Reopen Extend drums to try again.')
            except (djmix.MixError, OSError) as error: self.failed(str(error))
        self.cancel = self.studio.job(work, finished, rendering=True, cancellable=True,
                                      failed_callback=self.failed, progress_callback=self.status.setText)

    def show_help(self):
        from studio_help import HelpDialog
        if self.help_window is None: self.help_window = HelpDialog(self)
        self.help_window.open_topic('drum-loops'); self.help_window.show(); self.help_window.raise_()

    def open_cues(self):
        if not self.busy:
            self.reject(); self.studio.review_song_cues()

    def reject(self):
        self.player.close()
        if self.busy:
            self.closing = True
            if self.cancel: self.cancel.set()
            self.status.setText('Cancelling before closing…'); return
        super().reject()

    def cleanup(self, *_):
        self.player.close(); self.player.mixer = None; self.cache_paths = []
        if self.help_window is not None: self.help_window.close()
        self.studio.update_storage_pins()
