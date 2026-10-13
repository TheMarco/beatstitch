"""Native, keyboard-accessible recent-project and recovery browser."""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                              QPushButton, QScrollArea, QTabBar, QVBoxLayout, QWidget)

from studio_projects import PAGE_SIZE, inspect_page, project_candidates
from studio_widgets import clock


class ProjectsDialog(QDialog):
    def __init__(self, studio):
        super().__init__(studio)
        self.studio = studio
        self.records, self.rows, self.paths = [], [], []
        self.closed, self.busy, self.cancel = False, False, None
        self.setWindowTitle('Projects and recovery')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(800, 640)
        box = QVBoxLayout(self); box.setContentsMargins(20, 20, 20, 20); box.setSpacing(10)
        heading = QLabel('Projects and recovery'); heading.setProperty('role', 'title'); box.addWidget(heading)
        self.current = QLabel(); self.current.setTextFormat(Qt.TextFormat.PlainText)
        self.current.setWordWrap(True); box.addWidget(self.current)
        note = QLabel('Opening a project keeps unsaved work as a recovery copy first. '
                      'Recover opens an unsaved copy; Save as… then chooses where to keep it. Your source songs stay where they are.')
        note.setWordWrap(True); box.addWidget(note)
        tools = QHBoxLayout()
        self.browse = QPushButton('Open a file…'); self.browse.clicked.connect(self.browse_file)
        self.browse.setToolTip('Choose a .djmix project anywhere on this computer, including a moved project.')
        self.refresh_button = QPushButton('Refresh'); self.refresh_button.clicked.connect(self.reload)
        self.refresh_button.setToolTip('Read the latest project locations and recovery copies again.')
        self.save = QPushButton('Save current mix'); self.save.clicked.connect(self.save_current)
        self.save.setToolTip('Save the current editable project, choosing a location if it has never been saved. Does not export audio.')
        for button in (self.browse, self.save, self.refresh_button): tools.addWidget(button)
        tools.addStretch(); box.addLayout(tools)
        self.tabs = QTabBar(); self.tabs.addTab('Recent projects'); self.tabs.addTab('Recovery copies')
        self.tabs.setAccessibleName('Project browser category'); box.addWidget(self.tabs)
        self.search = QLineEdit(); self.search.setPlaceholderText('Find a loaded project by name or location')
        self.search.setAccessibleName('Find a project'); self.search.setClearButtonEnabled(True)
        self.search.setToolTip('Search the projects currently loaded below. Load older copies to include them in this search.')
        self.search.textChanged.connect(self.refresh_rows); box.addWidget(self.search)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget(); self.list = QVBoxLayout(content); self.list.setContentsMargins(4, 4, 4, 4)
        self.empty = QLabel(); self.empty.setWordWrap(True); self.list.addWidget(self.empty); self.list.addStretch()
        scroll.setWidget(content); box.addWidget(scroll, 1)
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText)
        box.addWidget(self.status)
        actions = QHBoxLayout()
        self.more = QPushButton('Load older copies'); self.more.clicked.connect(self.load_more)
        self.more.setToolTip('Read the next 20 recovery copies. No files are removed.')
        actions.addWidget(self.more); actions.addStretch()
        close = QPushButton('Close'); close.clicked.connect(self.reject); actions.addWidget(close); box.addLayout(actions)
        self.tabs.currentChanged.connect(self.reload)
        for widget in self.findChildren(QWidget):
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton): widget.setAutoDefault(False); widget.setMinimumHeight(30)
        self.reload()

    @property
    def kind(self):
        return 'recovery' if self.tabs.currentIndex() else 'recent'

    def set_busy(self, value):
        self.busy = value
        for widget in (self.tabs, self.browse, self.save, self.refresh_button, self.more): widget.setEnabled(not value)
        self.refresh_rows()

    def reload(self):
        if self.busy or self.closed: return
        self.current.setText(f'Current: {self.studio.session.name}\n{self.studio.project_status_text()}')
        self.records = []
        try: self.paths = project_candidates(self.studio.data, self.studio.prefs, self.kind)
        except OSError as error:
            self.paths = []; self.status.setText(f'Could not list projects: {error}'); self.refresh_rows(); return
        self.load_more()

    def load_more(self):
        if self.busy or self.closed: return
        paths = self.paths[len(self.records):len(self.records) + PAGE_SIZE]
        if not paths:
            self.status.setText('No recent projects yet. Save a mix or use Open a file….' if self.kind == 'recent' else 'No recovery copies yet.')
            self.refresh_rows(); return
        kind = self.kind
        self.set_busy(True); self.status.setText('Reading projects…')
        def finished(records):
            if self.closed: return
            self.records.extend(records); self.set_busy(False)
            self.status.setText(f'{len(self.records)} of {len(self.paths)} locations loaded. '
                                'Search covers these locations. Missing audio is reported but does not prevent opening the project.')
        def failed(message):
            if self.closed: return
            self.set_busy(False); self.status.setText(message)
        self.cancel = self.studio.job(lambda progress, emit: inspect_page(paths, kind, progress), finished,
                                      cancellable=True, failed_callback=failed)

    def refresh_rows(self):
        query = self.search.text().strip().casefold()
        visible = [r for r in self.records if query in (r['name'] + ' ' + r['path']).casefold()]
        while len(self.rows) < len(visible):
            row = QWidget(); line = QHBoxLayout(row); line.setContentsMargins(3, 7, 3, 7)
            row.text = QLabel(); row.text.setTextFormat(Qt.TextFormat.PlainText); row.text.setWordWrap(True)
            row.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            line.addWidget(row.text, 1)
            row.open = QPushButton(); row.open.setMinimumHeight(32); row.open.setAutoDefault(False)
            row.open.clicked.connect(lambda checked=False, r=row: self.open_entry(r.entry))
            line.addWidget(row.open)
            row.reveal = QPushButton('Show folder'); row.reveal.setMinimumHeight(32); row.reveal.setAutoDefault(False)
            row.reveal.clicked.connect(lambda checked=False, r=row: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(r.entry['path']).parent))))
            row.reveal.setToolTip('Open the containing folder without changing the mix.')
            row.reveal.setAccessibleDescription(row.reveal.toolTip()); line.addWidget(row.reveal)
            self.list.insertWidget(self.list.count() - 1, row); self.rows.append(row)
        for index, row in enumerate(self.rows):
            row.setVisible(index < len(visible))
            if index >= len(visible): continue
            record = visible[index]; row.entry = record
            stamp = datetime.fromtimestamp(record['modified']).strftime('%d %b %Y, %H:%M:%S') if record['modified'] else 'Date unavailable'
            label = 'Latest autosave' if Path(record['path']).name == 'autosave.djmix' and record['kind'] == 'recovery' else 'Recovery copy' if record['kind'] == 'recovery' else 'Saved project'
            summary = f'{record["songs"]} songs · {clock(record["duration"])}' if record['ready'] else record['error']
            if record['missing']: summary += f' · {record["missing"]} source file(s) missing'
            owner = f'\nOriginal project: {record["owner"]}' if record['kind'] == 'recovery' and record['owner'] else ''
            reason = f'\n{record["reason"]}' if record['kind'] == 'recovery' and record.get('reason') else ''
            row.text.setText(f'{record["name"]}\n{label} · {stamp}{reason}\n{summary}\n{record["path"]}{owner}')
            row.open.setText('Recover' if record['kind'] == 'recovery' else 'Open')
            row.open.setAccessibleName(f'{row.open.text()} {record["name"]}, {stamp}{reason.replace(chr(10), ", ")}')
            tip = 'Open this saved project. Unsaved current work is kept first.'
            if record['kind'] == 'recovery':
                tip = ('Open as an unsaved copy. The original saved project stays unchanged. '
                       'The latest autosave follows the active mix.' if label == 'Latest autosave' else
                       'Open as an unsaved copy. The original project and this recovery file stay unchanged.')
            row.open.setToolTip(record['error'] or tip)
            row.open.setAccessibleDescription(row.open.toolTip()); row.open.setEnabled(record['ready'] and not self.busy)
            row.reveal.setAccessibleName(f'Show folder for {record["name"]}, {stamp}')
            row.reveal.setEnabled(Path(record['path']).parent.is_dir())
        self.empty.setVisible(not visible)
        self.empty.setText('Reading projects…' if self.busy else 'No matching loaded projects.' if query else 'No projects in this list yet.')
        self.more.setVisible(len(self.records) < len(self.paths))

    def open_entry(self, record):
        if self.busy: return
        self.open_path(Path(record['path']), recovery=record['kind'] == 'recovery')

    def open_path(self, path, recovery=None):
        self.set_busy(True); self.status.setText('Opening and checking source audio…')
        def finished(success):
            if self.closed: return
            self.set_busy(False)
            if success: self.accept()
            else: self.status.setText(self.studio.project_error + ' Your current mix remains open.')
        self.cancel = self.studio.open_project_async(path, recovery, finished)

    def browse_file(self):
        start = self.studio.project_path.parent if self.studio.project_path else self.studio.data
        path, _ = QFileDialog.getOpenFileName(self, 'Open a mix', str(start), 'BeatStitch project (*.djmix)')
        if path:
            self.open_path(Path(path))

    def save_current(self):
        path = self.studio.project_path
        if path is None:
            value, _ = QFileDialog.getSaveFileName(self, 'Save mix', str(self.studio.data / f'{self.studio.session.name}.djmix'), 'BeatStitch project (*.djmix)')
            if not value: return
            path = Path(value)
        if self.studio.write_project(path): self.reload()
        else: self.status.setText('The project could not be saved. Your mix is still open; choose another location.')

    def done(self, result):
        self.closed = True
        if self.cancel is not None: self.cancel.set()
        super().done(result)
