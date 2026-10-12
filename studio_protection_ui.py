"""Draft-only passage marking inside the source-cue editor."""
import copy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel,
                              QLayout, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)
import djmix
from studio_protection import KINDS, fit_protected_overlaps, validate_moments
from studio_widgets import clock


class ProtectedPassages(QScrollArea):
    def __init__(self, editor):
        super().__init__()
        self.editor, self.rows = editor, []
        self.setWidgetResizable(True)
        content = QWidget(); self.setWidget(content)
        box = QVBoxLayout(content); box.setContentsMargins(6, 6, 6, 6); box.setSpacing(5)
        box.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        note = QLabel('Mark a passage to hear in full, with no other song blended over it. You choose the exact range and label. Find possible vocals offers listening suggestions; changes remain a draft.')
        note.setWordWrap(True)
        self.find_vocals = QPushButton('Find possible vocals'); self.find_vocals.clicked.connect(editor.analyze_passages)
        self.find_vocals.setToolTip('Check this source locally for possible voice-like passages, including singing and speech. Instruments can be mistaken for voices and some vocals can be missed. Reuses the same observations as Find drum-led passages. Listen before protecting a range.')
        intro = QHBoxLayout(); intro.addWidget(note, 1); intro.addWidget(self.find_vocals); box.addLayout(intro)
        grid = QGridLayout(); self.start, self.end = QDoubleSpinBox(), QDoubleSpinBox()
        for row, (name, control, which) in enumerate((('From', self.start, 'start'), ('Through', self.end, 'end'))):
            control.setRange(0, editor.track.duration); control.setDecimals(3); control.setSuffix(' s')
            control.setMinimumHeight(26)
            control.setSingleStep(60 / editor.track.bpm); control.setAccessibleName('Protected passage ' + which + ' in source seconds')
            control.setToolTip('Original song time. Type or step by a beat; Mark uses the white listening cursor on the waveform.')
            grid.addWidget(QLabel(name), row, 0); grid.addWidget(control, row, 1)
            mark = QPushButton('Mark ' + which + ' here')
            mark.clicked.connect(lambda checked=False, c=control: c.setValue(editor.wave.cursor))
            mark.setToolTip('Use the current listening position. Click or seek in the waveform to choose the position first.')
            grid.addWidget(mark, row, 2)
        span = editor.clip.cue_out - editor.clip.cue_in
        self.start.setValue(editor.clip.cue_in + span / 3)
        self.end.setValue(editor.clip.cue_in + min(span * 2 / 3, span / 3 + 4 * 240 / editor.track.bpm))
        self.kind = QComboBox(); self.kind.setAccessibleName('Protected passage type')
        for key, title in KINDS.items(): self.kind.addItem(title, key)
        self.kind.setToolTip('A label you choose. The same protection applies to every type.')
        self.name = QLineEdit(); self.name.setPlaceholderText('Name (optional)'); self.name.setMaxLength(160)
        self.name.setAccessibleName('Protected passage name'); self.name.setToolTip('For example: final chorus, main drop, or favorite melody.')
        grid.addWidget(self.kind, 0, 3); grid.addWidget(self.name, 1, 3); grid.setColumnStretch(3, 1); box.addLayout(grid)
        actions = QHBoxLayout()
        self.add = QPushButton('Protect this passage'); self.add.clicked.connect(self.add_moment)
        self.add.setToolTip('Add these source times to the draft. Apply changes saves protection with this clip in the project.')
        self.fit = QPushButton('Shorten blends to fit'); self.fit.clicked.connect(self.fit_blends)
        self.fit.setToolTip('Try shorter adjacent overlaps around protected passages. Never lengthens a blend or changes cues. Review before Apply.')
        actions.addWidget(self.add); actions.addWidget(self.fit); actions.addStretch(); box.addLayout(actions)
        fields = QWidget(); self.list = QVBoxLayout(fields); self.list.setContentsMargins(4, 4, 4, 4); self.list.addStretch()
        box.addWidget(fields); box.addStretch(1)
        self.empty = QLabel('No protected passages yet.'); self.list.insertWidget(0, self.empty)
        from studio_passages_ui import VocalSuggestions
        self.vocals = VocalSuggestions(editor, self.use_voice_range)
        self.list.insertWidget(self.list.count() - 1, self.vocals)
        self.refresh()
        for widget in self.findChildren(QWidget):
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton): widget.setAutoDefault(False)

    def refresh(self):
        moments = self.editor.clip.protected_moments
        while len(self.rows) < len(moments):
            index = len(self.rows)
            field = QWidget(); row = QHBoxLayout(field); row.setContentsMargins(0, 2, 0, 2)
            label = QLabel(); label.setTextFormat(Qt.TextFormat.PlainText); label.setWordWrap(True); row.addWidget(label, 1)
            listen = QPushButton('Listen'); listen.clicked.connect(lambda checked=False, i=index: self.listen(i))
            listen.setToolTip('Play the original source from this passage’s start. Does not change the mix.')
            remove = QPushButton('Remove protection'); remove.clicked.connect(lambda checked=False, i=index: self.remove(i))
            remove.setToolTip('Remove only this protection from the draft. Audio and cues stay as set. Apply is still required.')
            for button in (listen, remove):
                button.setAutoDefault(False); button.setAccessibleDescription(button.toolTip()); row.addWidget(button)
            self.list.insertWidget(self.list.indexOf(self.vocals), field); self.rows.append((field, label, listen, remove))
        for i, (field, label, listen, remove) in enumerate(self.rows):
            field.setVisible(i < len(moments))
            if i >= len(moments): continue
            moment = moments[i]
            label.setText(f'{moment["label"]} · {clock(moment["start"], True)} – {clock(moment["end"], True)}')
            listen.setAccessibleName('Listen to protected ' + moment['label'])
            remove.setAccessibleName('Remove protection for ' + moment['label'])
            listen.setEnabled(getattr(self.editor, 'output_available', False))
        self.empty.setVisible(not moments)
        self.vocals.refresh()

    def use_voice_range(self, candidate):
        from studio_content import current_content, voice_regions
        if candidate not in voice_regions(current_content(self.editor.track)):
            self.editor.status.setText('That observation is no longer current. Find possible vocals again, or mark your own range.'); return
        self.start.setValue(candidate['start']); self.end.setValue(candidate['end'])
        self.kind.setCurrentIndex(self.kind.findData('vocal')); self.name.setText('Vocal passage')
        self.start.setFocus()
        self.ensureWidgetVisible(self.start)
        self.editor.status.setText('Review these approximate times, then choose Protect this passage. Apply changes keeps the protection; Undo reverses it.')

    def add_moment(self):
        clip = self.editor.clip
        moment = {'start': self.start.value(), 'end': self.end.value(), 'kind': self.kind.currentData(),
                  'label': self.name.text().strip() or self.kind.currentText()}
        candidate = copy.deepcopy(clip); candidate.protected_moments.append(moment)
        try: validate_moments(self.editor.track, candidate)
        except djmix.MixError as error: self.editor.status.setText(str(error)); return
        if moment in clip.protected_moments:
            self.editor.status.setText('That exact passage is already protected in the draft.'); return
        clip.protected_moments.append(moment)
        self.refresh(); self.editor.status.setText('Passage added to the draft. Review its range and apply to keep it.')
        self.editor.refresh_draft()

    def remove(self, index):
        del self.editor.clip.protected_moments[index]
        self.refresh(); self.editor.status.setText('Protection removed from the draft. Apply changes to keep that choice.')
        self.editor.refresh_draft()

    def listen(self, index):
        if getattr(self.editor, 'output_available', False):
            self.editor.audition.listen_at(self.editor.clip.protected_moments[index]['start'])

    def fit_blends(self):
        try: draft, changes = fit_protected_overlaps(self.editor.session, self.editor.index)
        except djmix.MixError as error: self.editor.status.setText(str(error)); return
        self.editor.session.clips = draft.clips; self.editor.clip = draft.clips[self.editor.index]
        self.editor.status.setText('Draft overlaps: ' + '; '.join(changes) + '. Apply to keep them.' if changes else 'The current blends already leave every protected passage clear.')
        self.refresh(); self.editor.refresh_draft()
