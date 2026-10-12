"""One-clock Current / Draft listening before committing a blend alternative."""
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox,
                              QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from studio_blend_choices import ChoiceTransport, change_summary
from studio_transition_view import TransitionView
from studio_widgets import clock


class BlendComparison(QDialog):
    def __init__(self, current, draft, index, bands, report, output_preference, volume, apply_draft, parent=None):
        super().__init__(parent)
        self.current, self.draft, self.index = current, draft, index
        self.bands, self.report, self.apply_draft = bands, report, apply_draft
        from studio_tempo_curve import session_curve
        self.tempo_curve = session_curve(current)
        self.output_origin = float(self.tempo_curve.to_output(report['start']))
        self.output_preference = output_preference
        self.setWindowTitle(f'Compare blend {index} → {index + 1}')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(760, 550)
        self.resize(980, 680)
        self.player = ChoiceTransport(self)
        self.devices = QMediaDevices(self)
        box = QVBoxLayout(self); box.setContentsMargins(22, 20, 22, 20); box.setSpacing(12)
        title = QLabel(f'Compare blend {index} → {index + 1}'); title.setProperty('role', 'title'); box.addWidget(title)
        summary = QLabel('Draft: ' + change_summary(current, index, draft.clips[index]))
        summary.setWordWrap(True); summary.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(summary)
        if draft.clips[index].balance_levels:
            from studio_level_balance import report_text
            level_note = QLabel(report_text(report.get('draft_level_balance', []), index, True))
            level_note.setWordWrap(True); level_note.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(level_note)
        note = QLabel('Switch while it plays. Both versions follow the outgoing song on one clock and use matched listening levels. '
                      'A different overlap moves the incoming song; that is part of the comparison. These listening gains are not exported.')
        note.setWordWrap(True); note.setProperty('role', 'secondary'); box.addWidget(note)
        row = QHBoxLayout(); self.choices = QButtonGroup(self)
        for title, variant in (('A · Current mix', 'current'), ('B · Draft blend', 'draft')):
            button = QPushButton(title); button.setCheckable(True); button.setChecked(variant == 'current')
            button.setMinimumHeight(36)
            button.setToolTip('Switch versions without seeking, pausing or restarting. Choosing a version does not change the mix.')
            button.toggled.connect(lambda checked, v=variant: self.select(v) if checked else None)
            self.choices.addButton(button); row.addWidget(button)
        box.addLayout(row)
        self.view = TransitionView()
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.view.setMinimumHeight(180); box.addWidget(self.view, 1)
        self.view.seekRequested.connect(lambda seconds: self.player.seek(round(float(
            self.tempo_curve.to_output(self.report['start'] + seconds) - self.output_origin) * 1000)))
        row = QHBoxLayout()
        self.play = QPushButton('Listen'); self.play.clicked.connect(self.toggle)
        self.play.setToolTip('Play or pause the selected version. The other version stays at the same position.')
        row.addWidget(self.play)
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.setRange(0, round(report['duration'] * 1000))
        self.seek.setAccessibleName('Current and draft comparison position')
        self.seek.setSingleStep(1000); self.seek.setPageStep(5000)
        self.seek.setToolTip('Seek both versions together. Left/Right moves one second; Page Up/Down moves five seconds; Home/End goes to either end. Seeking keeps paused audio paused.')
        self.seek.sliderMoved.connect(self.player.seek)
        self.seek.valueChanged.connect(lambda ms: self.player.seek(ms) if not self.seek.isSliderDown() else None)
        row.addWidget(self.seek, 1)
        self.time = QLabel(); row.addWidget(self.time)
        self.loop = QCheckBox('Loop'); self.loop.setChecked(True)
        self.loop.setToolTip('Repeat the comparison. This does not create loops in the exported mix.')
        self.loop.toggled.connect(self.player.set_loop); row.addWidget(self.loop); box.addLayout(row)
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(self.status)
        footer = QLabel('Listen for clashing notes, competing voices, doubled kicks and a dip or jump in energy. '
                        'The grid follows cues and project BPM; it is not proof of phrase alignment. Apply only if you prefer the draft.')
        footer.setWordWrap(True); footer.setProperty('role', 'hint'); box.addWidget(footer)
        buttons = QDialogButtonBox()
        self.apply = buttons.addButton('Apply draft to mix', QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply.setToolTip('Keep the draft overlap, bass handling, outgoing effect, level balance, incoming pitch and incoming Start as one undoable edit.')
        self.apply.clicked.connect(self.commit)
        back = buttons.addButton('Back to editing', QDialogButtonBox.ButtonRole.RejectRole)
        back.setToolTip('Return to the draft editor without changing the mix. The draft stays available there.')
        back.clicked.connect(self.reject); box.addWidget(buttons)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playingChanged.connect(lambda playing: self.play.setText('Pause' if playing else 'Listen'))
        self.player.errorOccurred.connect(self.status.setText)
        self.devices.audioOutputsChanged.connect(self.refresh_output)
        self.finished.connect(lambda _: self.player.close())
        self.player.load(report['folder']); self.player.set_volume(volume); self.player.set_loop(True)
        self.select('current'); self.position_changed(0); self.refresh_output()
        for widget in self.findChildren(QWidget):
            if widget.toolTip():
                widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton):
                widget.setAutoDefault(False)

    def select(self, variant):
        self.player.select(variant)
        session = self.current if variant == 'current' else self.draft
        self.view.set_data(session, self.index, self.bands, self.report['start'], self.report['end'])
        self.position_changed(self.player.position())

    def refresh_output(self):
        device = QMediaDevices.defaultAudioOutput() if self.output_preference == 'system' else next(
            (d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == self.output_preference), None)
        self.player.set_device(device)
        available = self.player.sink is not None
        self.play.setEnabled(available)
        self.status.setText('Output: ' + device.description() if available else
                            'Output unavailable. Reconnect it, or return to the main window and choose another output.')

    def position_changed(self, milliseconds):
        if not self.seek.isSliderDown():
            self.seek.blockSignals(True); self.seek.setValue(milliseconds); self.seek.blockSignals(False)
        self.time.setText(f'{clock(milliseconds / 1000)} / {clock(self.report["duration"])}')
        self.view.set_playhead(float(self.tempo_curve.to_reference(self.output_origin + milliseconds / 1000)) - self.report['start'])

    def toggle(self):
        self.player.pause() if self.player.playing else self.player.play()

    def commit(self):
        if self.apply_draft(self.draft.clips[self.index]):
            self.accept()
        else:
            self.status.setText('The mix could not accept this draft. Return to the editor to see the reason, or reopen this blend.')
