"""Explain possible voice collisions and offer explicit incoming-cue drafts."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from studio_overlap_content import overlap_voices, vocal_cue_choices, voice_advice
from studio_widgets import clock


class OverlapVoices(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.summary = QLabel(); self.summary.setWordWrap(True); self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setToolTip('Uses approximate source voice observations mapped through the cues, BPM and tempo correction into this overlap. Echo repeats are included. Instruments can be mistaken for voices and vocals can be missed. This is separate from key advice and does not detect competing melodies.')
        self.summary.setAccessibleDescription(self.summary.toolTip())
        row.addWidget(self.summary, 1)
        self.check = QPushButton('Check voices in blend'); self.check.clicked.connect(editor.check_voices)
        self.check.setToolTip('Check both source songs locally, reusing unchanged passage observations. Listening to an unprepared blend also checks them. Analysis never changes cues, protection or sound.')
        self.check.setAccessibleDescription(self.check.toolTip()); self.check.setAutoDefault(False)
        row.addWidget(self.check); box.addLayout(row)
        self.choices = QWidget(); box.addWidget(self.choices)
        self.box = box

    def refresh(self):
        result = overlap_voices(self.editor.session, self.editor.index)
        self.summary.setText(voice_advice(result))
        options = vocal_cue_choices(self.editor.session, self.editor.index)
        content = QWidget(); column = QVBoxLayout(content); column.setContentsMargins(0, 0, 0, 0)
        for option in options:
            row = QHBoxLayout()
            direction = 'Omits' if option['source_delta'] > 0 else 'Restores'
            later = 'longer' if option['mix_delta'] > 0 else 'shorter'
            note = QLabel(f'{direction} {abs(option["source_delta"]):.1f} source seconds at the beginning; '
                          f'mix becomes {abs(option["mix_delta"]):.1f} seconds {later}. '
                          f'About {option["seconds"]:.1f} seconds of simultaneous voice observations remain. Listen to judge.')
            note.setWordWrap(True); row.addWidget(note, 1)
            button = QPushButton('Try incoming start ' + clock(option['time'], True))
            button.setObjectName('tryVoiceCue'); button.setAutoDefault(False)
            button.setToolTip('Change only the incoming Start in the draft; keep its End and this overlap length. Clears its drums-only intro mark. Both neighbors and protected passages are checked. Compare current / draft, then Apply only if you prefer it.')
            button.setAccessibleDescription(button.toolTip())
            button.clicked.connect(lambda checked=False, c=option: self.editor.try_voice_cue(c))
            row.addWidget(button); column.addLayout(row)
        if result['state'] == 'possible_conflict' and not options:
            note = QLabel('No better incoming start found on the nearby bar grid. Check beats in Song cues, try a shorter blend, or choose another song. A shorter blend is not automatically better.')
            note.setWordWrap(True); column.addWidget(note)
        self.box.removeWidget(self.choices)
        self.choices.hide(); self.choices.setParent(None); self.choices.deleteLater()
        self.choices = content; self.box.addWidget(content)
        self.setEnabled(not self.editor.busy)
