"""Storage controls for rebuildable app data; original media is outside this policy."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                              QPushButton, QVBoxLayout)

from studio_storage import GIB, size_text


class StorageDialog(QDialog):
    def __init__(self, manager, save_limit, parent=None):
        super().__init__(parent)
        self.manager, self.save_limit = manager, save_limit
        self.setWindowTitle('Storage and cache')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(560, 440)
        box = QVBoxLayout(self); box.setContentsMargins(20, 20, 20, 20); box.setSpacing(12)
        heading = QLabel('Storage and cache'); heading.setProperty('role', 'title'); box.addWidget(heading)
        note = QLabel('Prepared audio, analysis and waveforms are kept so repeat work is faster. '
                      'Older unused cache files are removed when the limit is exceeded. Your songs, projects and exports stay where you saved them.')
        note.setWordWrap(True); box.addWidget(note)
        self.usage = QLabel(); self.usage.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(self.usage)
        row = QFormLayout()
        self.limit = QDoubleSpinBox(); self.limit.setRange(.25, 64); self.limit.setSingleStep(.25)
        self.limit.setDecimals(2); self.limit.setSuffix(' GB'); self.limit.setValue(manager.limit() / GIB)
        self.limit.setAccessibleName('Maximum disposable cache size in gigabytes')
        self.limit.setToolTip('Total allowance for processed songs, prepared mixes, listening audio, analysis and waveforms. '
                              'Files in use can temporarily keep usage above the limit.')
        row.addRow('Cache limit', self.limit); box.addLayout(row)
        actions = QHBoxLayout()
        apply = QPushButton('Apply limit'); apply.clicked.connect(self.apply_limit)
        apply.setToolTip('Save this limit and remove the oldest unused cache entries if necessary.')
        self.clear = QPushButton('Clear unused cached files'); self.clear.clicked.connect(self.clear_cache)
        self.clear.setToolTip('Remove rebuildable cached audio and analysis. Current source songs and loaded playback stay protected. '
                              'Audio may need preparing again next time.')
        refresh = QPushButton('Refresh'); refresh.clicked.connect(self.refresh_usage)
        refresh.setToolTip('Recount cache files and free space without deleting anything.')
        for button in (apply, self.clear, refresh):
            actions.addWidget(button); button.setAutoDefault(False)
        box.addLayout(actions)
        self.status = QLabel('Active processing is protected. Loaded audio and source files remain after cleanup.')
        self.status.setWordWrap(True); box.addWidget(self.status)
        location = QLabel(str(manager.data)); location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        location.setWordWrap(True); location.setProperty('role', 'secondary'); box.addWidget(location)
        close = QPushButton('Close'); close.clicked.connect(self.accept); box.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.refresh_usage()

    def refresh_usage(self):
        result = self.manager.inspect()
        lines = [f"Cached: {size_text(result['bytes'])} · Limit: {size_text(result['limit'])}",
                 f"Free on this drive: {size_text(result['free'])}"]
        lines.extend(f'{name}: {size_text(size)}' for name, size in result['groups'].items())
        self.usage.setText('\n'.join(lines))

    def clean(self, clear=False):
        result = self.manager.prune(clear=clear)
        self.status.setText('Processing is using the cache. Try cleanup after it finishes.' if result['busy'] else
                            f"Freed {size_text(result['removed'])}. {size_text(result['remaining'])} remains, including files still protected.")
        self.refresh_usage()

    def apply_limit(self):
        self.save_limit(self.limit.value())
        self.clean()

    def clear_cache(self):
        self.clean(clear=True)
