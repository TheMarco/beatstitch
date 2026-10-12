"""Compact native library filters and project collection actions."""
import sys

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

import djmix
from studio_library import collection_name
from studio_widgets import ComboBox


class LibraryTools(QWidget):
    def __init__(self, studio):
        super().__init__()
        self.studio = studio
        column = QVBoxLayout(self); column.setContentsMargins(0, 0, 0, 0); column.setSpacing(4)
        row = QHBoxLayout(); row.setSpacing(4)
        self.collection = ComboBox(); self.collection.addItem('All collections', '')
        self.collection.setAccessibleName('Library collection')
        self.collection.setToolTip('Show all library songs or one collection saved in this project. Collections do not move files or change the mix.')
        self.collection.currentIndexChanged.connect(lambda _: studio.refresh_library())
        row.addWidget(self.collection, 1)
        self.manage = QPushButton('Collections…'); self.manage.clicked.connect(self.show_menu)
        self.manage.setToolTip('Create, rename or delete a project collection, or add/remove selected songs. These actions can be undone; source files stay untouched.')
        row.addWidget(self.manage); column.addLayout(row)
        self.filter = ComboBox()
        for name, key in [('All songs', 'all'), ('Not yet in the mix', 'unused'), ('Already in the mix', 'used'),
                          ('Key fits selected mix song', 'key'), ('Within 6% of mix tempo', 'tempo'),
                          ('Needs BPM or key', 'analysis'), ('Source audio missing', 'missing')]:
            self.filter.addItem(name, key)
        self.filter.setAccessibleName('Library filter')
        self.filter.setToolTip('Combine this filter with Search and a collection. Key fit uses the selected mix song’s key and pitch shift; it is a suggestion, not a guarantee.')
        self.filter.currentIndexChanged.connect(lambda _: studio.refresh_library())
        column.addWidget(self.filter)
        self.add = QPushButton('Add selected to mix'); self.add.clicked.connect(self.add_selected)
        self.add.setAccessibleName('Add selected songs to the end of the mix')
        column.addWidget(self.add)
        self.hint = QLabel(); self.hint.setWordWrap(True); self.hint.setProperty('role', 'small')
        column.addWidget(self.hint)
        for control in (self.collection, self.manage, self.filter, self.add):
            control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            control.setProperty('helpTopic', 'library')
            control.setAccessibleDescription(control.toolTip())

    def refresh(self):
        chosen = self.collection.currentData()
        blocker = QSignalBlocker(self.collection)
        self.collection.clear(); self.collection.addItem('All collections', '')
        for name in sorted(self.studio.session.collections, key=str.casefold):
            self.collection.addItem(name, name)
        self.collection.setCurrentIndex(max(0, self.collection.findData(chosen)))
        del blocker

    def update_selection(self):
        identities = self.studio.library.selected_ids()
        busy = self.studio.importing or self.studio.rendering
        self.add.setText(f'Add selected to mix ({len(identities)})' if identities else 'Add selected to mix')
        self.add.setEnabled(bool(identities) and not busy)
        self.manage.setEnabled(not busy)
        modifier = 'Cmd' if sys.platform == 'darwin' else 'Ctrl'
        tip = 'Append selected songs in the order currently shown in the library. One Undo removes the whole batch. You can deliberately add a song more than once.'
        self.add.setToolTip(tip); self.add.setAccessibleDescription(tip)
        if busy:
            text = 'Finish or cancel the current audio job before adding songs.'
        elif self.filter.currentData() == 'key' and not self.studio.selected_clip():
            text = 'Select a song in the mix to filter by a compatible key.'
        elif not self.studio.library_model.ids and self.studio.session.tracks:
            text = 'No matching songs. Change the filter, collection or search.'
        elif identities:
            text = f'{len(identities)} selected · Enter adds them in list order.'
        else:
            text = f'{modifier}-click to pick songs; Shift-click for a range.'
        self.hint.setText(text)

    def add_selected(self):
        self.studio.add_tracks(self.studio.library.selected_ids(), len(self.studio.session.clips))

    def choose_name(self, old=None):
        name, ok = QInputDialog.getText(self, 'Rename collection' if old else 'New collection',
                                        'Collection name (saved in this project):', text=old or '')
        if not ok: return None
        try: return collection_name(name, self.studio.session.collections, old)
        except djmix.MixError as error:
            self.studio.notify(str(error), 'error')
            return None

    def create(self):
        name = self.choose_name()
        if name is None: return
        identities = self.studio.library.selected_ids()
        if self.studio.edit(lambda: self.studio.session.collections.update({name: identities}), action=f'Create collection {name}'):
            self.collection.setCurrentIndex(self.collection.findData(name))

    def add_to(self, name):
        identities = self.studio.library.selected_ids()
        def mutate():
            members = self.studio.session.collections[name]
            members.extend(identity for identity in identities if identity not in members)
        self.studio.edit(mutate, action=f'Add songs to collection {name}')

    def remove_selected(self):
        name = self.collection.currentData(); identities = set(self.studio.library.selected_ids())
        if not name: return
        def mutate():
            self.studio.session.collections[name] = [v for v in self.studio.session.collections[name] if v not in identities]
        self.studio.edit(mutate, action=f'Remove songs from collection {name}')

    def rename(self):
        old = self.collection.currentData()
        if not old: return
        name = self.choose_name(old)
        if name is None or name == old: return
        def mutate():
            self.studio.session.collections[name] = self.studio.session.collections.pop(old)
        if self.studio.edit(mutate, action=f'Rename collection {old}'):
            self.collection.setCurrentIndex(self.collection.findData(name))

    def delete(self):
        name = self.collection.currentData()
        if name: self.studio.edit(lambda: self.studio.session.collections.pop(name), action=f'Delete collection {name}')

    def show_menu(self):
        menu = QMenu(self)
        menu.addAction('New collection from selection…', self.create)
        add = menu.addMenu('Add selected songs to collection')
        selected = bool(self.studio.library.selected_ids())
        add.setEnabled(selected and bool(self.studio.session.collections))
        for name in sorted(self.studio.session.collections, key=str.casefold):
            add.addAction(name, lambda checked=False, name=name: self.add_to(name))
        current = bool(self.collection.currentData())
        action = menu.addAction('Remove selected songs from this collection', self.remove_selected)
        action.setEnabled(current and selected)
        menu.addSeparator()
        action = menu.addAction('Rename this collection…', self.rename); action.setEnabled(current)
        action = menu.addAction('Delete this collection (keep songs)', self.delete); action.setEnabled(current)
        menu.exec(self.manage.mapToGlobal(self.manage.rect().bottomLeft()))
