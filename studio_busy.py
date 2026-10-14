"""One application-modal progress window for every tracked processing task."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout


class ProcessingDialog(QDialog):
    def __init__(self, studio):
        super().__init__(studio)
        self.studio = studio
        self.setWindowTitle('BeatStitch is working')
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(460)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        box = QVBoxLayout(self); box.setContentsMargins(24, 20, 24, 20); box.setSpacing(12)
        self.title = QLabel(); self.title.setProperty('role', 'title'); self.title.setWordWrap(True)
        self.detail = QLabel(); self.detail.setWordWrap(True)
        self.note = QLabel('Please wait. The app will be available when processing finishes.'); self.note.setWordWrap(True)
        for label in (self.title, self.detail, self.note):
            label.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(label)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); box.addWidget(self.progress)
        self.cancel = QPushButton('Cancel processing'); self.cancel.clicked.connect(studio.cancel_job)
        self.cancel.setToolTip('Request a safe stop. This window stays open until the running tasks actually stop. Completed downloads and cached work are kept.')
        box.addWidget(self.cancel)
        self.active = False

    def update_state(self, active, title='', detail='', *, cancellable=False, stopping=False, download=None):
        self.active = active
        if not active:
            self.hide()
            return
        self.title.setText('Stopping safely…' if stopping else title)
        self.detail.setText(detail)
        self.note.setText('Waiting for running tasks to stop. Completed work is kept.' if stopping else
                         'Please wait. The app will be available when processing finishes.')
        self.cancel.setVisible(cancellable or stopping)
        self.cancel.setEnabled(cancellable and not stopping)
        self.cancel.setText('Stopping…' if stopping else 'Cancel processing')
        if download is not None and download.maximum() > 0:
            self.progress.setRange(download.minimum(), download.maximum()); self.progress.setValue(download.value())
        else:
            self.progress.setRange(0, 0)
        if not self.isVisible(): self.show()

    def reject(self):
        # Escape and the title-bar close button never dismiss running work.
        if self.active:
            if self.cancel.isVisible() and self.cancel.isEnabled(): self.studio.cancel_job()
            return
        super().reject()

    def closeEvent(self, event):
        if self.active:
            self.reject(); event.ignore()
        else:
            event.accept()
