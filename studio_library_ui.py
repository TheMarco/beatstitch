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
        self.key_info = QLabel(); self.key_info.setWordWrap(True)
        self.key_info.setTextFormat(Qt.TextFormat.PlainText)
        self.key_info.setProperty('role', 'secondary')
        self.key_info.setProperty('helpTopic', 'keys')
        self.key_info.setToolTip('Compatibility compares this library song at its original pitch with the selected mix '
                                 'song after its pitch shift. Same key, relative major/minor and neighboring keys are '
                                 'suggestions to audition, not a guarantee about melodies or vocals.')
        column.addWidget(self.key_info)
        self.add = QPushButton('Add selected to mix'); self.add.clicked.connect(self.add_selected)
        self.add.setAccessibleName('Add selected songs to the end of the mix')
        column.addWidget(self.add)
        actions = QHBoxLayout(); actions.setSpacing(4)
        self.detect = QPushButton('Detect keys'); self.detect.clicked.connect(studio.check_library_keys)
        self.detect.setToolTip('Detect keys for every selected song. Fill unknown labels; keep existing choices. If needed, download the optional 19 MB key detector first. Valid cached estimates are reused.')
        actions.addWidget(self.detect)
        self.remove = QPushButton('Remove songs'); self.remove.clicked.connect(lambda: studio.remove_library_selected())
        self.remove.setToolTip('Remove selected songs from the library, collections and mix. Original audio files stay on disk. One Undo restores everything. You can also press Delete in the song list.')
        actions.addWidget(self.remove)
        column.addLayout(actions)
        self.hint = QLabel(); self.hint.setWordWrap(True); self.hint.setProperty('role', 'small')
        result_row = QHBoxLayout(); result_row.addWidget(self.hint, 1)
        self.results = QPushButton('Results…'); self.results.clicked.connect(studio.show_library_batch_results)
        self.results.setToolTip('Read the latest batch result for every selected song and retry unfinished checks.')
        self.results.setProperty('helpTopic', 'library'); result_row.addWidget(self.results)
        column.addLayout(result_row)
        for control in (self.collection, self.manage, self.filter, self.add, self.detect, self.remove):
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
        model = self.studio.library_model
        current = self.studio.session.tracks.get(self.studio.library.current_id())
        key_text = ''
        if model.context and current and current.id != model.context_track:
            relation = model.relation(current)
            pair = f'{djmix.key_name(model.context)} ↔ {current.key or "Unknown"}'
            if relation:
                kind = {'same key': 'Same key', 'relative major/minor': 'Relative major/minor keys',
                        'neighboring keys on the circle of fifths': 'Neighboring keys'}[relation]
                key_text = f'{pair}\n{kind}. No extra pitch shift needed.'
            elif current.key:
                key_text = f'{pair}\nNo simple key match. Listen before deciding.'
            else:
                key_text = f'{pair}\nDetect or set this song’s key to check compatibility.'
        elif self.filter.currentData() == 'key':
            key_text = (f'Matches {djmix.key_name(model.context)} in the mix: same, relative or neighboring keys. '
                        'Candidates keep their original pitch.' if model.context else
                        'Select a mix song with a known key to find compatible songs.')
        unknown = [self.studio.session.tracks[i] for i in identities if not self.studio.session.tracks[i].key]
        if unknown:
            from studio_model_store import available
            if available('key'):
                explanation = f'{len(unknown)} selected song(s) have an unknown key. Detect keys checks the selection and fills missing labels.'
            else:
                explanation = 'Key detection needs the optional 19 MB download. Click Detect keys to install it and check the selection.'
            if any(t.key_origin == 'manual' for t in unknown):
                explanation += ' A manually chosen Unknown is kept; Use detected key accepts its estimate.'
            key_text += ('\n' if key_text else '') + explanation
        self.key_info.setText(key_text)
        self.key_info.setVisible(bool(key_text))
        busy = self.studio.importing or self.studio.rendering
        self.add.setText(f'Add selected to mix ({len(identities)})' if identities else 'Add selected to mix')
        self.add.setEnabled(bool(identities) and not busy)
        self.manage.setEnabled(not busy)
        self.detect.setText(f'Detect keys ({len(identities)})' if identities else 'Detect keys')
        self.detect.setEnabled(bool(identities) and not self.studio.jobs)
        self.remove.setEnabled(bool(identities) and not self.studio.jobs)
        arranged = any(c.track_id in identities for c in self.studio.session.clips)
        self.remove.setText('Remove + mix' if arranged else 'Remove songs')
        self.remove.setAccessibleName('Remove selected songs from library and mix' if arranged else 'Remove selected songs from library')
        self.results.setVisible(bool(getattr(self.studio, 'last_library_batch', None)))
        self.results.setEnabled(not self.studio.jobs)
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
