"""Native audition-first review of tentative EDM section labels."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QPushButton, QRadioButton,
                              QScrollArea, QVBoxLayout, QWidget)
from studio_song_sections import LABELS, current_sections, model_available, section_cues
from studio_widgets import clock

COLORS = {'intro': '#5CCBEA', 'buildup': '#E8B65A', 'drop': '#E98477',
          'breakdown': '#B6A1DC', 'outro': '#6DCEAB', 'silence': '#9AA5A4'}


class SongSections(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor, self.regions, self.last_key = editor, [], None
        self.table_regions = None
        self.cue_choices = None
        box = QVBoxLayout(self); box.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        note = QLabel('Find likely builds, drops, intros and outros. Listen before trusting a label or boundary. '
                      'A drop is the main release of energy; a build leads toward it.')
        note.setWordWrap(True); row.addWidget(note, 1)
        self.find = QPushButton('Find builds and drops'); self.find.setObjectName('findSongSections')
        self.find.setToolTip('Analyze this song locally with the experimental section model. '
                             'Reuses results if the file and model are unchanged. Runs separately so Stop and close can cancel it. '
                             'Does not change cues, BPM, key, or protection. Individual songs up to 30 minutes.')
        self.find.clicked.connect(editor.analyze_sections); row.addWidget(self.find); box.addLayout(row)
        self.summary = QLabel(); self.summary.setWordWrap(True); box.addWidget(self.summary)
        self.list = QScrollArea(); self.list.setWidgetResizable(True)
        self.list.setAccessibleName('Suggested song sections')
        self.list.setToolTip('Original song times. Select a section without playing it or changing the mix. '
                             'Arrow keys move between section choices. Listen around both boundaries before protecting a range.')
        self.list_body = QWidget(); self.list.setWidget(self.list_body)
        self.list_rows = QVBoxLayout(self.list_body); self.list_rows.setContentsMargins(6, 3, 6, 3)
        self.list_rows.setSpacing(2); self.list_rows.addStretch()
        self.group = QButtonGroup(self); self.buttons = []
        self.group.buttonToggled.connect(lambda button, checked: self.selection_changed() if checked else None)
        box.addWidget(self.list, 1)
        actions = QHBoxLayout()
        self.begin = QPushButton('Listen to start'); self.end = QPushButton('Listen to end')
        self.protect = QPushButton('Review protection…'); self.protect.setObjectName('reviewSectionProtection')
        self.begin.setToolTip('Play from four beats before the selected section begins. Pause stops playback; no cues change.')
        self.end.setToolTip('Play from four beats before the selected section ends to check its boundary.')
        self.protect.setToolTip('Fill the protection form with this suggested range. Review the exact times, choose Protect this passage, then Apply changes. This button alone protects nothing.')
        self.begin.clicked.connect(lambda: self.listen(False)); self.end.clicked.connect(lambda: self.listen(True))
        self.protect.clicked.connect(self.use_range)
        for button in (self.begin, self.end, self.protect): actions.addWidget(button)
        actions.addStretch(); box.addLayout(actions)
        self.cue_note = QLabel(); self.cue_note.setWordWrap(True); box.addWidget(self.cue_note)
        self.cue_row = QHBoxLayout(); box.addLayout(self.cue_row)
        for widget in self.findChildren(QWidget):
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton): widget.setAutoDefault(False)

    def refresh(self):
        r = self.editor; result = current_sections(r.track)
        key = (id(result) if result else None, r.clip.cue_in, r.clip.cue_out, r.track.bpm,
               id(r.track.structure), repr(r.track.beat_grid), repr(r.clip.protected_moments),
               tuple(c.blend_bars for c in r.session.clips), getattr(r, 'output_available', False))
        if key == self.last_key: return
        self.last_key = key
        selected = self.group.checkedId()
        self.regions = result.get('regions', [])
        installed = model_available()
        self.find.setEnabled(True)
        self.find.setText('Find builds and drops' if installed else 'Download build/drop detector…')
        self.summary.setText(f'{len(self.regions)} suggested sections · Experimental labels and approximate boundaries, not an accuracy score.' if result else
                             'Not checked yet. Finding sections leaves your mix unchanged.' if installed else
                             'Optional download: about 3 GB. Beat-based cues, manual cues and protection do not need this advanced detector.')
        table_regions = tuple((v['label'], v['start'], v['end']) for v in self.regions)
        # Native radio choices avoid macOS's populated-table accessibility crash.
        # Keep their objects and selection stable during cue/output updates.
        if table_regions != self.table_regions:
            self.table_regions = table_regions
            self.group.blockSignals(True)
            for button in self.buttons:
                self.group.removeButton(button); self.list_rows.removeWidget(button)
                button.hide(); button.setParent(None); button.deleteLater()
            self.buttons = []
            for row, region in enumerate(self.regions):
                text = f'{LABELS[region["label"]]} · {clock(region["start"], True)} – {clock(region["end"], True)}'
                button = QRadioButton(text); button.setAccessibleName('Likely ' + text)
                button.setToolTip('Select this model suggestion without starting playback. Listen to start/end checks its boundaries; Review protection opens an editable range.')
                button.setAccessibleDescription(button.toolTip())
                self.group.addButton(button, row); self.buttons.append(button)
                self.list_rows.insertWidget(self.list_rows.count() - 1, button)
            if self.regions: self.select_row(max(0, min(selected, len(self.regions) - 1)))
            self.group.blockSignals(False)
        self.selection_changed()
        candidates = section_cues(r.session, r.index)
        self.cue_note.setText('Intro/outro cue choices fit the current bar grid, neighboring blends and protected passages. '
                              'They can still contain melodies or vocals; preview the resulting blend.' if candidates else
                              'No intro/outro cue change fits this draft. Analyze song for bar starts, check beats/BPM, or keep your own cues. '
                              'You can still audition and protect the sections above.' if result else '')
        if candidates == self.cue_choices: return
        self.cue_choices = candidates
        while self.cue_row.count():
            item = self.cue_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # setParent(None) removes it from its QLayoutItem. Retain the
                # wrapper rather than looking it up again through that item.
                widget.hide(); widget.setParent(None); widget.deleteLater()
        for candidate in candidates:
            title = f'Try {candidate["kind"].title()} · {clock(candidate["time"], True)}'
            if candidate['overlap']: title += f' · {candidate["overlap"]} bars'
            button = QPushButton(title); button.setAutoDefault(False)
            button.setToolTip(f'Likely {LABELS[candidate["label"]].lower()}, aligned to your detected or manual bar grid. '
                              f'Omits {candidate["omitted"]:.1f} source seconds. Clears the old drums-only label. '
                              'May shorten this adjacent blend to fit. Apply changes keeps this draft; Undo reverses it.')
            button.setAccessibleDescription(button.toolTip()); button.setObjectName('trySectionCue')
            button.clicked.connect(lambda checked=False, c=candidate: r.try_section_cue(c))
            self.cue_row.addWidget(button)
        self.cue_row.addStretch()

    def selected(self):
        index = self.group.checkedId()
        return self.regions[index] if 0 <= index < len(self.regions) else None

    def select_row(self, index):
        if 0 <= index < len(self.buttons): self.buttons[index].setChecked(True)

    def selection_changed(self):
        selected = self.selected()
        available = bool(getattr(self.editor, 'output_available', False))
        self.begin.setEnabled(selected is not None and available)
        self.end.setEnabled(selected is not None and available)
        self.protect.setEnabled(selected is not None)
        if self.group.checkedButton(): self.list.ensureWidgetVisible(self.group.checkedButton())

    def current_selection(self):
        region = self.selected()
        if region not in current_sections(self.editor.track).get('regions', []):
            self.editor.status.setText('These section observations are no longer current. Find builds and drops again.'); self.refresh(); return None
        return region

    def listen(self, end):
        region = self.current_selection()
        if region is not None and getattr(self.editor, 'output_available', False):
            self.editor.audition.listen_at(max(0, region['end' if end else 'start'] - 240 / self.editor.track.bpm))

    def use_range(self):
        region = self.current_selection()
        if region is None: return
        r = self.editor; panel = r.protection
        panel.start.setValue(region['start']); panel.end.setValue(region['end'])
        kind = {'buildup': 'build', 'drop': 'drop'}.get(region['label'], 'passage')
        panel.kind.setCurrentIndex(panel.kind.findData(kind)); panel.name.setText(LABELS[region['label']])
        r.tabs.setCurrentWidget(panel); panel.start.setFocus(); panel.ensureWidgetVisible(panel.start)
        outside = region['start'] < r.clip.cue_in or region['end'] > r.clip.cue_out
        r.status.setText(('This range extends outside the kept audio. Review Start/End before protecting it. ' if outside else '') +
                        'Review these approximate times, then choose Protect this passage and Apply changes. The mix has not changed.')
