"""Optional saved final mastering, with continuous volume-matched comparison."""
import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
                              QLabel, QPushButton, QVBoxLayout, QWidget)

import djmix
from studio_cache import digest


class MasteringDialog(QDialog):
    def __init__(self, studio):
        super().__init__(studio)
        self.studio = studio
        self.baseline = copy.deepcopy(studio.session)
        self.busy = self.closing = False
        self.cancel = self.audition = self.help_window = None
        self.preview_paths = []
        self.setWindowTitle('Automatic mastering')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumWidth(750)
        self.resize(830, 310)
        box = QVBoxLayout(self); box.setContentsMargins(22, 20, 22, 20); box.setSpacing(12)
        title = QLabel('Finish the whole mix'); title.setProperty('role', 'title'); box.addWidget(title)
        hint = QLabel('Gentle compression, peak control and a restrained loudness increase. '
                      'The difference can be subtle. Compare at equal volume and choose what sounds best.')
        hint.setWordWrap(True); box.addWidget(hint)
        self.enabled = QCheckBox('Use automatic mastering for this mix')
        self.enabled.setChecked(self.baseline.mastering_enabled)
        self.enabled.setAccessibleName('Use automatic mastering for this mix')
        self.enabled.setToolTip('This is a draft choice until Apply changes. On adds the final finishing pass to Play mix, main Listen to blend, opening/ending previews and exports. Off restores the mix before mastering. Songs and cues are unchanged.')
        self.enabled.toggled.connect(self.draft_changed); box.addWidget(self.enabled)
        row = QHBoxLayout()
        self.compare = QPushButton('Compare before / after')
        self.compare.setToolTip('Prepare and play the current mix and its mastered version. Then switch A/B continuously. Both use equal listening loudness by default. Comparing alone does not enable mastering in the project.')
        self.compare.clicked.connect(self.prepare_comparison)
        row.addWidget(self.compare); row.addStretch(); box.addLayout(row)
        self.status = QLabel('Mastering is ' + ('on' if self.baseline.mastering_enabled else 'off') +
                             ' in the saved mix. Compare, then Apply changes to keep your choice.')
        self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(self.status)
        self.preview = QVBoxLayout(); box.addLayout(self.preview)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply |
                                       QDialogButtonBox.StandardButton.Close | QDialogButtonBox.StandardButton.Help)
        self.apply = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply.setText('Apply changes')
        self.apply.setToolTip('Save this on/off choice in the project. It takes effect the next time you prepare playback or export. One Undo restores the previous setting. A/B listening volume does not alter export loudness.')
        self.apply.clicked.connect(self.apply_changes)
        self.buttons.rejected.connect(self.reject)
        self.buttons.helpRequested.connect(self.show_help)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setToolTip('Close and stop comparison without applying a draft choice.')
        self.buttons.button(QDialogButtonBox.StandardButton.Help).setToolTip('Explain automatic mastering, volume matching, what is processed and cache reuse.')
        box.addWidget(self.buttons)
        self.help_shortcut = QShortcut(QKeySequence('F1'), self)
        self.help_shortcut.activated.connect(self.show_help)
        self.finished.connect(self.cleanup)
        for control in self.findChildren(QWidget):
            control.setProperty('helpTopic', 'automatic-mastering')
            if control.toolTip(): control.setAccessibleDescription(control.toolTip())
            if isinstance(control, QPushButton): control.setAutoDefault(False)
        self.update_controls()

    def show_help(self):
        from studio_help import HelpDialog
        if self.help_window is None: self.help_window = HelpDialog(self)
        self.help_window.open_topic('automatic-mastering'); self.help_window.show()
        self.help_window.raise_(); self.help_window.activateWindow()

    def update_controls(self, *_):
        self.enabled.setEnabled(not self.busy)
        self.apply.setEnabled(not self.busy and self.enabled.isChecked() != self.baseline.mastering_enabled)
        self.compare.setEnabled(not self.busy)
        self.compare.setVisible(self.audition is None)

    def draft_changed(self, enabled):
        if self.audition is not None and self.audition.ready:
            self.audition.choose('B' if enabled else 'A')
        self.update_controls()

    def prepare_comparison(self):
        if self.busy: return
        if self.audition is not None:
            self.audition.toggle_play(); return
        snapshot = copy.deepcopy(self.baseline)
        self.busy = True; self.status.setText('Preparing both versions. Unchanged song processing is reused.')
        self.update_controls()
        def work(progress, emit):
            from studio_render_cache import prepare_listening
            from studio_mastering import prepare_comparison
            from studio_engine import layout
            from studio_tempo_curve import session_curve
            dry = copy.deepcopy(snapshot); dry.mastering_enabled = False
            before = prepare_listening(dry, self.studio.data / 'renders', progress=progress,
                                       audio_cache=self.studio.data / 'audio-cache')
            after_session = copy.deepcopy(snapshot); after_session.mastering_enabled = True
            after = prepare_listening(after_session, self.studio.data / 'renders', progress=progress,
                                      audio_cache=self.studio.data / 'audio-cache')
            positions, curve = layout(snapshot), session_curve(snapshot)
            segments = []
            for i in range(1, len(positions)):
                left = curve.output_frame(positions[i]['start']) / djmix.SR
                right = curve.output_frame(positions[i - 1]['end']) / djmix.SR
                segments.append({'name': f'Blend {i} → {i + 1}', 'start': max(0, left - 8),
                                 'end': min(after['duration'], right + 8)})
            name = snapshot.name if snapshot.name != 'Untitled mix' else f'Your mix ({len(snapshot.clips)} songs)'
            folder = self.studio.data / 'audio-cache' / 'auditions' / digest(
                ['mastering-comparison', before['fingerprint'], after['fingerprint']])
            report = prepare_comparison(Path(before['path']), Path(after['path']), folder,
                                        name=name, segments=segments,
                                        processing_summary=after['mastering']['processing_summary'], progress=progress)
            from studio_mastering_audio import prepare_playback
            prepared = prepare_playback(folder, progress)
            return report, [before['path'], after['path']], prepared
        def finished(payload):
            self.busy = False
            if self.closing:
                if len(payload) > 2: payload[2][0].close()
                super(MasteringDialog, self).reject(); return
            if self.studio.session.to_dict() != self.baseline.to_dict():
                if len(payload) > 2: payload[2][0].close()
                self.status.setText('The mix changed. Close and reopen Automatic mastering before comparing or applying.')
                self.update_controls(); return
            report, inputs, prepared = payload
            folder = Path(report['folder'])
            self.preview_paths = inputs + [str(folder / f'{v}.wav') for v in ('current', 'mastered', 'matched')]
            self.studio.update_storage_pins()
            from studio_mastering_player import MasteringLab
            self.audition = MasteringLab(folder, parent=self, embedded=True,
                                         output_preference=self.studio.output_preference,
                                         volume=self.studio.volume.value(), prepared=prepared)
            self.audition.setMinimumSize(0, 0)
            self.audition.layout().setContentsMargins(0, 0, 0, 0)
            self.preview.addWidget(self.audition)
            for control in self.audition.findChildren(QWidget):
                control.setProperty('helpTopic', 'automatic-mastering')
                if control.toolTip(): control.setAccessibleDescription(control.toolTip())
                if isinstance(control, QPushButton): control.setAutoDefault(False)
            if not self.audition.ready:
                self.status.setText(self.audition.status.text())
            else:
                self.audition.choose('B' if self.enabled.isChecked() else 'A')
                self.status.setText('A = before mastering. B = after mastering. Matching is on by default. '
                                    'The checkbox above decides what Apply changes keeps in your mix.')
                self.audition.toggle_play()
            self.resize(860, 730)
            self.update_controls()
        self.cancel = self.studio.job(work, finished, rendering=True, cancellable=True,
                                      title='Comparing automatic mastering',
                                      reason='Preparing both versions of the finished mix. Unchanged songs are reused.',
                                      failed_callback=self.failed, progress_callback=self.status.setText)

    def failed(self, message):
        self.busy = False; self.status.setText(message); self.update_controls()
        if self.closing: super().reject()

    def apply_changes(self):
        if self.busy: return
        if self.studio.session.to_dict() != self.baseline.to_dict():
            self.status.setText('The mix changed. Close and reopen Automatic mastering before applying.'); return
        choice = self.enabled.isChecked()
        if self.studio.edit(lambda: setattr(self.studio.session, 'mastering_enabled', choice),
                            action='Turn automatic mastering ' + ('on' if choice else 'off')):
            self.accept()

    def reject(self):
        if self.audition is not None: self.audition.player.pause()
        if self.busy:
            self.closing = True
            if self.cancel: self.cancel.set()
            self.status.setText('Cancelling the comparison before closing…'); return
        super().reject()

    def cleanup(self, *_):
        if self.audition is not None: self.audition.player.close()
        if self.help_window is not None: self.help_window.close()
        self.preview_paths = []
        self.studio.update_storage_pins()
