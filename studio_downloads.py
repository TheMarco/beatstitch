"""Native, optional feature downloads with progress, cancellation and repair."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRunnable, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
                              QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from studio_model_store import available, install, model_root, packs, remove, status, supported
from studio_process import cancel_on


def size_text(n):
    return f'{n/1e9:.2f} GB' if n >= 1e9 else f'{n/1e6:.0f} MB'


def ensure_models(owner, selected, on_ready):
    """Offer the needed optional pack from a nested native review window."""
    if all(available(p) for p in selected):
        return True
    host = owner
    while host is not None:
        handler = getattr(host, 'require_models', None)
        if callable(handler):
            return handler(selected, on_ready, requester=owner.window())
        host = host.parentWidget()
    # Standalone development dialogs still get the engine's actionable message.
    return True


class DownloadSignals(QObject):
    progress = Signal(object)
    error = Signal(str)
    complete = Signal()


class DownloadWorker(QRunnable):
    def __init__(self, selected, cancel, *, removing=False):
        super().__init__()
        self.selected, self.cancel, self.removing = selected, cancel, removing
        self.signals = DownloadSignals()

    def run(self):
        try:
            with cancel_on(self.cancel):
                for pack in self.selected:
                    if self.removing:
                        remove(pack)
                    else:
                        install(pack, self.signals.progress.emit)
        except Exception as error:
            self.signals.error.emit(str(error))
        finally:
            self.signals.complete.emit()


class DownloadsDialog(QDialog):
    stateChanged = Signal()

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.worker = None
        self.cancel = None
        self.busy = False
        self.failure = None
        self.requested = ()
        self.on_ready = None
        self.banner = ''
        self.setWindowTitle('Downloads & features')
        self.resize(840, 750)
        self.setMinimumSize(660, 560)
        box = QVBoxLayout(self)
        box.setContentsMargins(22, 20, 22, 18)
        box.setSpacing(12)
        heading = QLabel('Choose the help you want')
        heading.setStyleSheet('font-size: 22px; font-weight: 700;'); box.addWidget(heading)
        intro = QLabel('Mixing, pitch and tempo changes, transitions, mastering and export work without model downloads. '
                       'Add detectors when you need them. Once installed, they run locally and work offline; your songs are not uploaded.')
        intro.setWordWrap(True); box.addWidget(intro)
        selection_note = QLabel('Checkboxes choose files for Download selected. Installed packs remain available until you remove them.')
        selection_note.setWordWrap(True); selection_note.setProperty('role', 'secondary'); box.addWidget(selection_note)
        self.request_note = QLabel(); self.request_note.setWordWrap(True); self.request_note.hide(); box.addWidget(self.request_note)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget(); scroll.setWidget(body); rows = QVBoxLayout(body)
        rows.setContentsMargins(0, 0, 8, 0); rows.setSpacing(12)
        self.rows = {}
        for identity, spec in packs().items():
            group = QFrame(); group.setProperty('role', 'panel')
            layout = QVBoxLayout(group); layout.setContentsMargins(12, 9, 12, 9); layout.setSpacing(5)
            top = QHBoxLayout()
            chosen = QCheckBox(spec['title']); chosen.setObjectName('downloadSelect_'+identity)
            chosen.setToolTip('Select this optional feature for Download selected. Checking this box does not start a download.')
            chosen.setChecked(spec['recommended'] and not available(identity))
            chosen.toggled.connect(self.selection_changed); top.addWidget(chosen, 1)
            action = QPushButton('Download'); action.setObjectName('downloadPack_'+identity)
            action.setToolTip('Download and verify only this feature. Completed files and saved partial bytes are reused on retry.')
            action.clicked.connect(lambda checked=False, p=identity: self.start([p])); top.addWidget(action)
            delete = QPushButton('Remove'); delete.setObjectName('removePack_'+identity)
            delete.setToolTip('Remove this downloaded model and any saved partial download. Songs, projects, prepared audio and saved observations stay intact.')
            delete.clicked.connect(lambda checked=False, p=identity: self.remove_pack(p)); top.addWidget(delete)
            layout.addLayout(top)
            description = QLabel(spec['description']); description.setWordWrap(True); layout.addWidget(description)
            details = QLabel(); details.setProperty('role', 'secondary'); details.setWordWrap(True); layout.addWidget(details)
            license_note = QLabel(spec['license']); license_note.setProperty('role', 'secondary'); license_note.setWordWrap(True)
            license_note.setToolTip('Model licenses still apply to separately downloaded files. Help → About BeatStitch → Credits & licenses contains the original notices.')
            layout.addWidget(license_note)
            rows.addWidget(group)
            self.rows[identity] = (chosen, action, delete, details)
        rows.addStretch(); box.addWidget(scroll, 1)
        self.progress = QProgressBar(); self.progress.setRange(0, 1000); self.progress.setValue(0); self.progress.hide()
        self.progress.setAccessibleName('Optional feature download progress'); box.addWidget(self.progress)
        self.message = QLabel('Recommended: the three small detectors. Advanced build/drop detection is a large optional download.')
        self.message.setWordWrap(True); self.message.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(self.message)
        location = QLabel('Stored separately from the app: '+str(model_root()))
        location.setWordWrap(True); location.setProperty('role', 'secondary')
        location.setToolTip('App updates keep these downloads. They are separate from disposable audio caches and are not cleared by Storage and cache.')
        box.addWidget(location)
        buttons = QHBoxLayout()
        self.install_selected = QPushButton('Download selected'); self.install_selected.setProperty('role', 'primary')
        self.install_selected.setObjectName('downloadSelected')
        self.install_selected.setToolTip('Download the checked features only. The size is the full download size; retries may need less.')
        self.install_selected.clicked.connect(lambda: self.start(self.selected()))
        buttons.addWidget(self.install_selected)
        self.stop = QPushButton('Cancel download'); self.stop.setObjectName('cancelModelDownload')
        self.stop.setToolTip('Stop safely. Downloaded bytes stay available for Retry; installed features remain usable.')
        self.stop.clicked.connect(self.request_cancel); buttons.addWidget(self.stop); self.stop.hide()
        buttons.addStretch()
        self.credits_window = None
        credits = QPushButton('Credits & licenses'); credits.clicked.connect(self.show_credits); buttons.addWidget(credits)
        self.close_button = QPushButton('Keep working without downloads')
        self.close_button.setToolTip('Close this window. A running download continues in the background and its progress remains visible in the main window.')
        self.close_button.clicked.connect(self.close); buttons.addWidget(self.close_button)
        box.addLayout(buttons)
        self.refresh()

    def selected(self):
        return [p for p, row in self.rows.items() if row[0].isChecked() and status(p) not in ('ready', 'included', 'unsupported')]

    def selection_changed(self, *_):
        selected = self.selected()
        total = sum(packs()[p]['download_bytes'] for p in selected)
        self.install_selected.setText(('Download & continue' if self.on_ready else 'Download selected')+
                                      (' · '+size_text(total) if selected else ''))
        self.install_selected.setEnabled(bool(selected) and not self.busy)

    def refresh(self):
        states = {'missing': 'Not downloaded', 'partial': 'Partial download / retry available', 'verify': 'Existing files need verification',
                  'ready': 'Ready · works offline', 'included': 'Available in this existing installation', 'unsupported': 'Unavailable in this app build'}
        for pack, (chosen, action, delete, details) in self.rows.items():
            state = status(pack); spec = packs()[pack]
            details.setText(f'{states[state]}  ·  Download {size_text(spec["download_bytes"])}  ·  Installed {size_text(spec["installed_bytes"])}')
            chosen.setEnabled(not self.busy and state not in ('ready', 'included', 'unsupported'))
            if state in ('ready', 'included', 'unsupported'):
                with QSignalBlocker(chosen): chosen.setChecked(state != 'unsupported')
            action.setText('Installed' if state in ('ready', 'included') else 'Verify files' if state == 'verify' else 'Retry' if state == 'partial' else 'Download')
            action.setEnabled(not self.busy and state not in ('ready', 'included', 'unsupported'))
            delete.setEnabled(not self.busy and state in ('ready', 'partial', 'verify') and not self.host.jobs)
        self.selection_changed()
        self.stop.setVisible(self.busy and not (self.worker and self.worker.removing))
        self.stop.setEnabled(self.busy and not self.cancel.is_set())
        self.close_button.setText('Hide · keep downloading' if self.busy else 'Keep working without downloads')

    def request(self, selected, on_ready=None):
        if not self.busy:
            self.requested = tuple(selected)
            self.on_ready = on_ready
            for pack, row in self.rows.items():
                with QSignalBlocker(row[0]): row[0].setChecked(pack in selected and not available(pack))
            names = ', '.join(packs()[p]['title'] for p in selected if not available(p))
            self.request_note.setText('For this action, download: '+names+'. You can cancel and keep using manual controls.')
            self.request_note.setVisible(bool(names))
            self.refresh()
        self.show(); self.raise_(); self.activateWindow()

    def start(self, selected, *, removing=False):
        if self.busy or not selected:
            return
        selected = tuple(selected)
        self.busy = True; self.failure = None; self.cancel = threading.Event()
        self.banner = 'Removing optional model files' if removing else 'Downloading optional features'
        self.message.setText(self.banner+'. Your mix audio is unchanged.')
        self.progress.setRange(0, 1000); self.progress.setValue(0); self.progress.setVisible(not removing)
        self.worker = DownloadWorker(selected, self.cancel, removing=removing)
        self.worker.signals.progress.connect(self.receive_progress)
        self.worker.signals.error.connect(self.failed)
        self.worker.signals.complete.connect(self.finished_download)
        self.refresh(); self.stateChanged.emit(); self.host.pool.start(self.worker)

    def receive_progress(self, event):
        spec = packs()[event['pack']]
        text = f'{spec["title"]}: {event["phase"].lower()}'
        if event['phase'] == 'Downloading':
            text += f' · {size_text(event["pack_received"])} of {size_text(event["pack_total"])}'
        elif event.get('message'):
            text += ' · '+event['message']
        self.message.setText(text); self.banner = text
        if event['phase'] in ('Preparing', 'Verifying'):
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, round(1000*event['pack_received']/max(1,event['pack_total']))))
        self.stateChanged.emit()

    def failed(self, message):
        self.failure = message

    def finished_download(self):
        removed = bool(self.worker and self.worker.removing)
        if removed:
            for pack in self.worker.selected:
                with QSignalBlocker(self.rows[pack][0]): self.rows[pack][0].setChecked(False)
        self.busy = False
        if self.failure:
            cancelled = self.failure == 'Operation cancelled.'
            self.message.setText(('Stopped safely. Saved bytes are kept for Retry.' if cancelled else
                                  'Download could not finish: '+self.failure+' Retry keeps completed files and resumes saved bytes.'))
        else:
            self.message.setText('Optional download removed. Your songs, mix, saved observations and prepared audio are unchanged.' if removed else
                                 'Ready. Downloaded features now work offline. Existing song settings stay as you chose them; analyze a song again to use a newly installed detector.')
        self.banner = ''; self.progress.hide(); self.worker = None
        self.refresh(); self.stateChanged.emit()
        if self.host.closing:
            self.host.close(); return
        if not self.failure and self.on_ready and all(available(p) for p in self.requested):
            callback = self.on_ready; self.on_ready = None; self.request_note.hide(); self.hide()
            callback()

    def request_cancel(self):
        if self.busy:
            self.cancel.set(); self.banner = 'Stopping download safely; keeping saved bytes for retry…'
            self.message.setText(self.banner); self.stop.setEnabled(False); self.stateChanged.emit()

    def remove_pack(self, pack):
        if self.host.jobs:
            self.message.setText('Finish the current audio or analysis job before removing models.'); return
        if QMessageBox.question(self, 'Remove optional download?',
                'Remove '+packs()[pack]['title']+' and any partial download? You can download it again. '
                'Songs, projects, saved observations and prepared audio stay intact.') != QMessageBox.StandardButton.Yes:
            return
        self.start([pack], removing=True)

    def show_credits(self):
        from studio_about import AboutDialog
        if self.credits_window is None:
            self.credits_window = AboutDialog(self)
        self.credits_window.show(); self.credits_window.raise_(); self.credits_window.activateWindow()

    def reject(self):
        # Escape/Close hides a background download, rather than abandoning an
        # unowned worker or silently cancelling its network request.
        self.hide()

    def closeEvent(self, event):
        self.hide(); event.ignore()
