"""Choose, verify and audition relocated audio before an undoable relink."""
import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                              QLabel, QPushButton, QVBoxLayout, QWidget)

from studio_listening import SourceAudition
from studio_media import prepare_relink
from studio_widgets import BrowserPreview


def text(value=''):
    label = QLabel(value); label.setWordWrap(True); label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class RelinkDialog(QDialog):
    def __init__(self, studio, identity=None):
        super().__init__(studio)
        self.studio, self.revision = studio, studio.revision
        self.snapshot = copy.deepcopy(studio.session)
        self.result, self.cancel = None, None
        self.closed = self.busy = False
        self.setWindowTitle('Reconnect source audio')
        self.setWindowModality(Qt.WindowModality.WindowModal); self.resize(780, 620)
        box = QVBoxLayout(self); box.setContentsMargins(20, 20, 20, 20); box.setSpacing(10)
        title = text('Reconnect source audio'); title.setProperty('role', 'title'); box.addWidget(title)
        box.addWidget(text('Choose the original song at its new location. Review and listen before applying. '
                           'All uses of this source song will reconnect together; one Undo restores the previous location.'))
        self.track = QComboBox(); self.track.setAccessibleName('Source song to reconnect')
        self.track.setToolTip('Missing songs appear first. Each entry represents one source, including all its clips.')
        identities = set(identity) if isinstance(identity, (list, tuple, set)) else None
        self.identities = identities or set(self.snapshot.tracks)
        ordered = sorted((t for t in self.snapshot.tracks.values() if t.id in self.identities),
                         key=lambda t: (Path(t.path).is_file(), t.name.casefold()))
        for source in ordered:
            self.track.addItem(('Missing · ' if not Path(source.path).is_file() else '') + source.name, source.id)
        if isinstance(identity, str): self.track.setCurrentIndex(max(0, self.track.findData(identity)))
        box.addWidget(self.track)
        self.original = text(); box.addWidget(self.original)
        self.choose = QPushButton('Choose audio file…'); self.choose.clicked.connect(self.choose_file)
        self.choose.setToolTip('Choose the same recording and version. A matching filename alone does not prove it is the right audio.')
        box.addWidget(self.choose, alignment=Qt.AlignmentFlag.AlignLeft)
        self.find_folder = QPushButton('Find selected originals in a folder…'); self.find_folder.clicked.connect(self.choose_folder)
        self.find_folder.setToolTip('Search for every selected original and verify its saved file hash. Matching filenames alone are never enough. Reconnect verified matches together, with one Undo.')
        box.addWidget(self.find_folder, alignment=Qt.AlignmentFlag.AlignLeft)
        self.candidate = text('No replacement chosen.'); box.addWidget(self.candidate)
        self.wave = BrowserPreview({}); self.wave.setFixedHeight(130)
        self.wave.setToolTip('Real waveform of the chosen source. Listen and seek using the controls below.')
        self.wave.setCursor(Qt.CursorShape.ArrowCursor); self.wave.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        box.addWidget(self.wave)
        self.audition = SourceAudition(); self.audition.output.setVolume(studio.volume.value() / 100)
        self.audition.failed.connect(lambda value: self.status.setText(value)); box.addWidget(self.audition)
        self.output = text(); box.addWidget(self.output)
        self.confirm = QCheckBox('I confirm this is the same recording and version')
        self.confirm.setToolTip('Required for older projects without a saved content fingerprint. Listen first; a matching name and duration are not proof.')
        self.confirm.toggled.connect(self.refresh_apply); box.addWidget(self.confirm)
        self.status = text(); box.addWidget(self.status); box.addStretch()
        buttons = QHBoxLayout()
        self.close_button = QPushButton('Cancel'); self.close_button.clicked.connect(self.reject)
        self.apply = QPushButton('Reconnect audio'); self.apply.clicked.connect(self.apply_relink)
        self.apply.setToolTip('Apply the reviewed source location and listed analysis changes as one Undo action. Does not copy or edit the audio file.')
        buttons.addWidget(self.close_button); buttons.addStretch(); buttons.addWidget(self.apply); box.addLayout(buttons)
        self.track.currentIndexChanged.connect(self.reset_candidate)
        self.devices = QMediaDevices(self); self.devices.audioOutputsChanged.connect(self.refresh_output)
        self.audition.aboutToPlay.connect(self.refresh_output)
        for widget in self.findChildren(QWidget):
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton): widget.setMinimumHeight(30); widget.setAutoDefault(False)
        self.reset_candidate()

    def reset_candidate(self):
        self.result = None; self.confirm.setChecked(False); self.confirm.hide()
        self.batch_result = None; self.apply.setText('Reconnect audio')
        self.audition.set_track(None); self.wave.set_track(None)
        source = self.snapshot.tracks.get(self.track.currentData())
        self.original.setText(f'Previous location:\n{source.path}' if source else 'There are no source songs in this project.')
        self.choose.setEnabled(source is not None)
        self.candidate.setText('No replacement chosen.'); self.status.setText('The current mix has not changed.')
        self.refresh_apply(); self.refresh_output()

    def refresh_output(self):
        choice = self.studio.output_preference
        device = QMediaDevices.defaultAudioOutput() if choice == 'system' else next(
            (d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == choice), None)
        available = device is not None and not device.isNull()
        self.audition.setEnabled(available and not self.busy)
        self.audition.output.setMuted(not available)
        if available:
            self.audition.output.setDevice(device); self.output.setText('Output: ' + device.description())
        else:
            self.audition.stop(); self.output.setText('Output unavailable. Reconnect it, or close and choose another output.')

    def refresh_apply(self):
        self.apply.setEnabled(not self.busy and self.result is not None and
                              (self.result['verified'] or self.confirm.isChecked()))

    def set_busy(self, value):
        self.busy = value
        self.track.setEnabled(not value); self.choose.setEnabled(not value)
        self.find_folder.setEnabled(not value)
        self.close_button.setText('Stop and close' if value else 'Cancel')
        self.refresh_apply(); self.refresh_output()

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose the relocated original audio', '',
                                            'Audio (*.wav *.mp3 *.flac *.aif *.aiff *.m4a *.ogg *.aac)')
        if path: self.check_file(Path(path))

    def check_file(self, path):
        self.reset_candidate(); self.candidate.setText('Chosen file:\n' + str(path))
        identity = self.track.currentData(); self.set_busy(True); self.status.setText('Checking the chosen audio…')
        def work(progress, emit):
            return prepare_relink(self.snapshot, identity, path, self.studio.data / 'library-cache', progress)
        def finished(result):
            if self.closed: return
            self.result = result; self.set_busy(False)
            source = result['draft'].tracks[identity]
            self.wave.set_track(source); self.audition.set_track(source)
            self.confirm.setVisible(not result['verified'])
            if result['verified']:
                self.status.setText('Identical audio verified. Cues, pitch, protected passages and valid timing/grid analysis will be preserved.')
            else:
                self.status.setText('No saved content identity is available. Listen and confirm the recording before reconnecting. '
                                    'Cues, BPM, key, pitch and protected passages stay as set. Old beat/timing analysis will be cleared. '
                                    f'Tempo correction will be turned off for {len(result["reset_correction"])} clip(s); check timing again afterward.')
            self.refresh_apply(); self.refresh_output()
        def failed(message):
            if self.closed: return
            self.set_busy(False); self.status.setText(message + ' The current mix has not changed.')
        self.cancel = self.studio.job(work, finished, rendering=True, cancellable=True,
                                      failed_callback=failed, progress_callback=self.status.setText)

    def apply_relink(self):
        if not self.apply.isEnabled(): return
        if self.batch_result is not None:
            if self.studio.apply_relinks(self.batch_result, self.revision): self.accept()
            else: self.status.setText(self.studio.relink_error)
            return
        if self.studio.apply_relink(self.result, self.revision): self.accept()
        else: self.status.setText(self.studio.relink_error)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Find the selected original recordings')
        if folder: self.check_folder(Path(folder))

    def check_folder(self, folder):
        from studio_media import find_relinks
        self.reset_candidate(); self.set_busy(True)
        def work(progress, emit):
            return find_relinks(self.snapshot, self.identities, folder, self.studio.data / 'library-cache', progress)
        def finished(result):
            if self.closed: return
            self.batch_result = result['matches']; self.set_busy(False)
            self.apply.setText(f'Reconnect {len(self.batch_result)} verified source(s)')
            self.apply.setEnabled(bool(self.batch_result))
            self.status.setText(f'{len(self.batch_result)} original recording(s) verified by file contents. '
                                + '\n'.join(result['issues'])
                                + '\nThe mix is unchanged. Reconnect applies all verified locations in one Undo action.')
        def failed(message):
            if self.closed: return
            self.set_busy(False); self.status.setText(message + ' No locations have changed.')
        self.cancel = self.studio.job(work, finished, rendering=True, cancellable=True,
                                      title='Finding original source files', failed_callback=failed,
                                      progress_callback=self.status.setText)

    def done(self, result):
        self.closed = True
        if self.cancel is not None: self.cancel.set()
        self.audition.stop(); super().done(result)
