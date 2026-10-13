"""Optional listening checklist, kept separate from detector estimates."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
                              QPushButton, QScrollArea, QVBoxLayout, QWidget)

from studio_widgets import ComboBox

JUDGMENTS = (('Not checked', 'unchecked'), ('Fits well', 'fits'),
             ('Possible problem', 'problem'), ('Not sure', 'unsure'))
DIMENSIONS = (
    ('timing', 'Do the beats stay together?',
     'Listen for double kicks, a stumbling pulse or beats that gradually pull apart. '
     'A matching BPM alone does not mean the beats are aligned.'),
    ('harmony', 'Do the musical notes fit?',
     'Listen to the bass, chords and melodies together. A compatible key label is '
     'only a starting point. Choose Not sure if this passage is too hard to judge.'),
    ('content', 'Do the voices and melodies coexist?',
     'Listen for two lyrics or lead melodies competing for attention. '
     'A busy overlap can be intentional; judge whether this blend works for you.'),
)


class ListeningNotesDialog(QDialog):
    def __init__(self, host, index):
        super().__init__(host)
        from studio_listening_notes import latest_note, note_status
        self.host, self.session, self.index = host, host.session, index
        self.pair = tuple(c.id for c in self.session.clips[index - 1:index + 1])
        import djmix
        from studio_render_cache import listening_recipe
        try:
            self.fingerprint = listening_recipe(self.session, 'transition', index)['fingerprint']
        except (djmix.MixError, OSError):
            self.fingerprint = None
        self.setWindowTitle('Listening notes')
        self.setModal(False)
        self.resize(590, 600)
        self.setMinimumSize(460, 430)
        self.setProperty('helpTopic', 'transitions')
        self.help_shortcut = QShortcut(QKeySequence('F1'), self)
        self.help_shortcut.activated.connect(lambda: host.show_help('transitions'))
        outer = QVBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setAccessibleName('Listening checklist')
        content = QWidget(); body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0); body.setSpacing(10)
        scroll.setWidget(content); outer.addWidget(scroll)
        names = [self.session.tracks[c.track_id].name for c in self.session.clips[index - 1:index + 1]]
        heading = QLabel(f'Blend {index} → {index + 1}: {names[0]} → {names[1]}')
        heading.setTextFormat(Qt.TextFormat.PlainText); heading.setWordWrap(True)
        heading.setProperty('role', 'section'); body.addWidget(heading)
        intro = QLabel('Listen, then record what you hear. You can leave any question unchecked. '
                       'These are your opinions, not detector scores. Saving notes does not change the mix. '
                       'Press F1 for the blend guide.')
        intro.setWordWrap(True); body.addWidget(intro)
        self.previous = QLabel(); self.previous.setWordWrap(True)
        self.previous.setTextFormat(Qt.TextFormat.PlainText)
        self.previous.setProperty('role', 'hint'); body.addWidget(self.previous)
        old = latest_note(self.session, index)
        current = bool(old and note_status(old, self.session) == 'current')
        if old:
            if current:
                self.previous.setText('Your saved notes for this sound are shown below. You can revise them.')
            else:
                summaries = [f'{title}: {dict((value, text) for text, value in JUDGMENTS)[old["judgments"][key]]}'
                             for key, title, _ in DIMENSIONS]
                self.previous.setText('The sound has changed or cannot be checked. Your earlier notes are kept '
                                      'in the project; check this version again.\n' + '\n'.join(summaries)
                                      + ('\nEarlier reminder: ' + old['comment'] if old.get('comment') else ''))
        else:
            self.previous.hide()
        self.answers = {}
        for key, title, tip in DIMENSIONS:
            question = QLabel(title); question.setWordWrap(True)
            question.setProperty('role', 'section'); body.addWidget(question)
            hint = QLabel(tip); hint.setWordWrap(True)
            hint.setProperty('role', 'hint'); body.addWidget(hint)
            combo = ComboBox(); combo.setAccessibleName(title)
            combo.setToolTip(tip + ' Not checked means you have not judged it; Not sure means you listened but cannot decide.')
            combo.setAccessibleDescription(combo.toolTip())
            for text, value in JUDGMENTS: combo.addItem(text, value)
            if current: combo.setCurrentIndex(combo.findData(old['judgments'][key]))
            self.answers[key] = combo; body.addWidget(combo)
        question = QLabel('Anything else to remember?'); body.addWidget(question)
        self.comment = QPlainTextEdit(); self.comment.setMaximumHeight(85)
        self.comment.setAccessibleName('Listening note')
        self.comment.setPlaceholderText('For example: the incoming drop feels early, or the bass gets too loud.')
        self.comment.setToolTip('Optional notes about phrases, levels, processing or anything you want to revisit. Up to 2,000 characters.')
        self.comment.setAccessibleDescription(self.comment.toolTip())
        if current: self.comment.setPlainText(old.get('comment', ''))
        body.addWidget(self.comment)
        self.message = QLabel(); self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setProperty('role', 'hint'); body.addWidget(self.message)
        actions = QHBoxLayout()
        self.listen = QPushButton('Listen to blend'); self.listen.clicked.connect(self.listen_blend)
        self.listen.setToolTip('Prepare and play this blend. It keeps playing while you write notes.')
        self.save = QPushButton('Save notes'); self.save.setProperty('role', 'primary')
        self.save.setToolTip('Save these judgments with the project for the sound currently loaded. No audio processing is started.')
        self.save.clicked.connect(self.save_notes)
        close = QPushButton('Close'); close.clicked.connect(self.reject)
        close.setToolTip('Close without saving these changes. Playback keeps its current state.')
        for widget in (self.listen, self.save, close):
            # Return belongs to the answer/editor, not an implicit audio action.
            # Dialog auto-defaults otherwise make a stray Return start playback.
            widget.setAutoDefault(False); widget.setDefault(False)
            widget.setMinimumHeight(30); widget.setAccessibleDescription(widget.toolTip())
            actions.addWidget(widget)
        outer.addLayout(actions)
        self.refresh_available()

    def matches_project(self):
        return (self.host.session is self.session and self.index < len(self.session.clips)
                and tuple(c.id for c in self.session.clips[self.index - 1:self.index + 1]) == self.pair)

    def refresh_available(self):
        same = self.matches_project()
        ready = same and self.host.notes_audio_ready(self.index)
        changed = False
        if ready:
            loaded = self.host.loaded_record['fingerprint']
            if self.fingerprint is None:
                self.fingerprint = loaded
            changed = loaded != self.fingerprint
            ready = not changed
        self.save.setEnabled(ready)
        self.listen.setEnabled(same and not self.host.jobs)
        self.message.setText('Save when you are ready. Unchecked questions stay unchecked.' if ready else
                             'This blend or project changed. Close these notes and open the current blend.' if not same or changed else
                             'Listen to this blend first. Saving becomes available when its current audio has loaded.')

    def listen_blend(self):
        if self.matches_project() and not self.host.jobs:
            self.host.select_transition(self.index)
            self.host.preview()
            self.refresh_available()

    def save_notes(self):
        import djmix
        self.refresh_available()
        if not self.save.isEnabled(): return
        judgments = {key: widget.currentData() for key, widget in self.answers.items()}
        comment = self.comment.toPlainText().strip()
        if len(comment) > 2000:
            self.message.setText('Shorten your note to 2,000 characters before saving.'); return
        if not comment and all(value == 'unchecked' for value in judgments.values()):
            self.message.setText('Choose an answer or write a note, or Close to leave this blend unchecked.'); return
        try:
            if self.host.save_listening_note(self.index, judgments, comment): self.accept()
        except (djmix.MixError, OSError) as error:
            self.message.setText(str(error))
