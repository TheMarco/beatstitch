"""Review the size and destination before copying a project with all its media."""
import copy
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QPushButton, QVBoxLayout

from studio_media import create_portable_copy, portable_folder_name, portable_size
from studio_relink_ui import text
from studio_storage import size_text


class PortableDialog(QDialog):
    def __init__(self, studio):
        super().__init__(studio)
        self.studio, self.snapshot = studio, copy.deepcopy(studio.session)
        self.destination, self.result, self.cancel = None, None, None
        self.closed = self.busy = False
        self.setWindowTitle('Create portable project'); self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(660, 430)
        box = QVBoxLayout(self); box.setContentsMargins(20, 20, 20, 20); box.setSpacing(12)
        heading = text('Take this mix to another computer'); heading.setProperty('role', 'title'); box.addWidget(heading)
        box.addWidget(text('Create a new folder containing Mix.djmix and copies of every source song in this project, '
                           'including library songs outside the arrangement. Move the whole folder together. '
                           'Your current project and original audio stay unchanged.'))
        self.summary = text(); box.addWidget(self.summary)
        try:
            amount, count = portable_size(self.snapshot)
            self.valid = bool(self.snapshot.tracks)
            self.summary.setText(f'{count} audio file(s) · approximately {size_text(amount)} plus the editable project. '
                                 'Cues, pitch choices, protected passages and valid analysis travel with it.' if self.valid else 'Add source songs before creating a portable copy.')
        except Exception as error:
            self.valid = False; self.summary.setText(str(error))
        self.choose = QPushButton('Choose destination folder…'); self.choose.clicked.connect(self.choose_folder)
        self.choose.setEnabled(self.valid); self.choose.setMinimumHeight(32)
        self.choose.setToolTip('Choose the parent folder. The app creates a new subfolder and never replaces an existing one.')
        box.addWidget(self.choose, alignment=Qt.AlignmentFlag.AlignLeft)
        self.location = text('No destination selected.'); box.addWidget(self.location)
        self.status = text(); box.addWidget(self.status); box.addStretch()
        actions = QHBoxLayout()
        self.close_button = QPushButton('Cancel'); self.close_button.clicked.connect(self.reject); actions.addWidget(self.close_button)
        actions.addStretch()
        self.show_folder = QPushButton('Show portable folder'); self.show_folder.clicked.connect(self.reveal); self.show_folder.hide()
        self.show_folder.setToolTip('Open the completed folder. Move the whole folder; open Mix.djmix in DJ Studio on the other computer.')
        self.create = QPushButton('Create portable copy'); self.create.clicked.connect(self.start); self.create.setEnabled(False)
        self.create.setToolTip('Copy all source audio and the editable mix into the new folder. Checks free space and verifies copied identities.')
        actions.addWidget(self.show_folder); actions.addWidget(self.create); box.addLayout(actions)
        for button in self.findChildren(QPushButton):
            button.setMinimumHeight(32); button.setAutoDefault(False); button.setAccessibleDescription(button.toolTip())

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Choose where to create the portable folder')
        if path: self.set_destination(Path(path))

    def set_destination(self, parent):
        base = parent / portable_folder_name(self.snapshot.name)
        destination = base; number = 2
        while destination.exists():
            destination = base.with_name(f'{base.name} {number}'); number += 1
        self.destination = destination; self.location.setText('New folder:\n' + str(destination))
        self.create.setEnabled(self.valid and not self.busy)

    def start(self):
        if self.busy or self.destination is None or not self.valid: return
        self.busy = True; self.choose.setEnabled(False); self.create.setEnabled(False)
        self.close_button.setText('Stop and close'); self.status.setText('Preparing the portable copy…')
        def finished(result):
            if self.closed: return
            self.busy = False; self.result = result; self.create.hide(); self.show_folder.show()
            self.close_button.setText('Done')
            self.status.setText('Portable copy ready. Open Mix.djmix in DJ Studio. Keep the media folder beside it. '
                                'Your current mix has not changed.')
        def failed(message):
            if self.closed: return
            self.busy = False; self.choose.setEnabled(True); self.create.setEnabled(True)
            self.close_button.setText('Cancel'); self.status.setText(message)
        self.cancel = self.studio.job(lambda progress, emit: create_portable_copy(self.snapshot, self.destination, progress),
                                      finished, rendering=True, cancellable=True, failed_callback=failed,
                                      progress_callback=self.status.setText)

    def reveal(self):
        if self.result: QDesktopServices.openUrl(QUrl.fromLocalFile(self.result['folder']))

    def done(self, result):
        self.closed = True
        if self.cancel is not None: self.cancel.set()
        super().done(result)
