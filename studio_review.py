"""Persistent native mix guidance beside the library, without hiding waveforms."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                              QScrollArea, QSpinBox, QVBoxLayout, QWidget)


def text(value, role=None):
    result = QLabel(value)
    result.setWordWrap(True)
    result.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        result.setProperty("role", role)
    return result


class MixReview(QWidget):
    actionRequested = Signal(str, str, float)
    limitsChanged = Signal(float, int)

    def __init__(self, preferred_percent=6, max_shift=2, parent=None):
        super().__init__(parent)
        self.setProperty("helpTopic", "mix-review")
        self.report = None
        box = QVBoxLayout(self)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(8)
        self.summary = text("Add songs to review your mix.", "section")
        box.addWidget(self.summary)
        box.addWidget(text("Tempo and pitch suggestions use your labels. Voice checks use saved source analysis and can be wrong. Listen to decide.", "secondary"))
        limits_button = QPushButton("Suggestion preferences")
        limits_button.setCheckable(True)
        limits_button.setToolTip("Set preferred tempo changes and the largest pitch shift the app may suggest. This does not alter audio.")
        box.addWidget(limits_button)
        self.limits = QWidget()
        grid = QGridLayout(self.limits)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(text("Preferred tempo change"), 0, 0)
        self.tempo_limit = QDoubleSpinBox()
        self.tempo_limit.setRange(1, 25)
        self.tempo_limit.setDecimals(0)
        self.tempo_limit.setSuffix(" %")
        self.tempo_limit.setValue(preferred_percent)
        self.tempo_limit.setAccessibleName("Preferred maximum tempo change percentage")
        self.tempo_limit.setToolTip("A listening preference, not a sound-quality guarantee. Automatic tempo advice stays within this limit; you can still set mix BPM manually.")
        grid.addWidget(self.tempo_limit, 0, 1)
        grid.addWidget(text("Largest suggested pitch shift"), 1, 0)
        self.pitch_limit = QSpinBox()
        self.pitch_limit.setRange(0, 4)
        self.pitch_limit.setSuffix(" st")
        self.pitch_limit.setValue(max_shift)
        self.pitch_limit.setAccessibleName("Largest suggested pitch shift in semitones")
        self.pitch_limit.setToolTip("Zero turns pitch suggestions off. Manual pitch controls remain available. Small shifts usually need less alteration; always listen.")
        grid.addWidget(self.pitch_limit, 1, 1)
        grid.addWidget(text("Preferences affect advice only. Zero semitones turns pitch suggestions off.", "hint"), 2, 0, 1, 2)
        box.addWidget(self.limits)
        self.limits.hide()
        limits_button.toggled.connect(self.limits.setVisible)
        for widget in (self.tempo_limit, self.pitch_limit):
            widget.valueChanged.connect(lambda _: self.limitsChanged.emit(self.tempo_limit.value(), self.pitch_limit.value()))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        box.addWidget(self.scroll, 1)

    def control(self, title, action, identity="", value=0, tooltip=""):
        button = QPushButton(title)
        button.setToolTip(tooltip or title)
        button.setAccessibleDescription(button.toolTip())
        button.clicked.connect(lambda: self.actionRequested.emit(action, identity, value))
        return button

    def update_report(self, report, busy=False):
        count = report["count"]
        self.summary.setText("Add songs to review your mix." if not report["songs"] and not report["transitions"] and not report["tempo"].get("available") else
                             f'{count} {"item" if count == 1 else "items"} to review' if count else
                             "No flagged settings. Listen before exporting.")
        pending = report.get('voice_pending', 0)
        if pending:
            self.summary.setText(self.summary.text() + f'\n{pending} voice {"check" if pending == 1 else "checks"} still pending.')
        if report != self.report:
            self.report = report
            scroll = self.scroll.verticalScrollBar().value()
            content = QWidget()
            column = QVBoxLayout(content)
            column.setContentsMargins(0, 0, 4, 0)
            column.setSpacing(10)
            tempo = report["tempo"]
            column.addWidget(text("Mix tempo", "section"))
            column.addWidget(text(tempo["reason"]))
            if tempo.get("available"):
                if tempo["improves"]:
                    apply = self.control(f'Use {tempo["target"]:g} BPM', "tempo", value=tempo["target"],
                                         tooltip="Change the whole mix tempo. Song key and cue choices stay as set. You can undo this.")
                    apply.setEnabled(tempo["can_apply"])
                    if not tempo["within_preference"]:
                        apply.setToolTip("This compromise exceeds your preferred tempo-change limit. Adjust the preference or choose mix BPM manually.")
                    column.addWidget(apply)
                else:
                    column.addWidget(text("Your current mix tempo already balances these song tempos.", "hint"))
                changes = QWidget()
                rows = QVBoxLayout(changes)
                rows.setContentsMargins(0, 0, 0, 0)
                for row in tempo["rows"]:
                    rows.addWidget(text(f'{row["name"]} · {row["source"]:g} BPM\n'
                                        f'Now {row["current_percent"]:+.1f}% · Suggested {row["suggested_percent"]:+.1f}%', "small"))
                show = QPushButton("Song-by-song tempo changes")
                show.setCheckable(True)
                show.toggled.connect(changes.setVisible)
                column.addWidget(show)
                column.addWidget(changes)
                changes.hide()
            for song in report["songs"]:
                column.addWidget(self.separator())
                column.addWidget(text(f'{song["number"]}. {song["name"]}', "section"))
                for kind, note in song["notes"]:
                    column.addWidget(text(note))
                actions = QHBoxLayout()
                actions.addWidget(self.control("Song settings", "select", song["id"], tooltip="Select this clip and its original waveform to inspect BPM, pitch and cues."))
                if any(kind == 'missing' for kind, _ in song['notes']):
                    actions.addWidget(self.control('Reconnect audio…', 'relink', song['id'], tooltip='Choose the original song at its new location. Keeps cue and pitch edits; review before applying.'))
                if any(kind == "correction" for kind, _ in song["notes"]):
                    actions.addWidget(self.control("Compare timing", "timing", song["id"], tooltip="Open the fixed-tempo versus stabilized comparison for this clip."))
                column.addLayout(actions)
            if report["transitions"]:
                column.addWidget(self.separator())
                column.addWidget(text("Listen to each blend", "section"))
            labels = {"compatible": "Related key labels", "percussion": "Marked as beats only",
                      "unknown": "Key advice unavailable", "clash": "Possible key clash"}
            for pair in report["transitions"]:
                column.addWidget(text(f'{pair["index"]} → {pair["index"] + 1} · {labels[pair["state"]]}', "section"))
                column.addWidget(text(f'{pair["outgoing"]} → {pair["incoming"]}', "small"))
                column.addWidget(text(pair["reason"], "hint"))
                suggestion = pair["suggestion"]
                if suggestion:
                    column.addWidget(text(f'{suggestion["name"]}: {suggestion["semitones"]:+d} st → '
                                          f'{suggestion["resulting_key"]}. {suggestion["reason"]}'))
                    column.addWidget(self.control(f'Try {suggestion["semitones"]:+d} st', "pitch", suggestion["id"],
                                                  suggestion["semitones"], "Apply this suggested shift, then listen. Both adjacent key labels were checked. Undo reverses it."))
                elif pair["state"] == "clash":
                    column.addWidget(text("No pitch suggestion for this pair under your current preferences. Try different cues or song order.", "hint"))
                voices = pair.get('voices')
                if voices:
                    column.addWidget(text(voices['reason']))
                    if voices['state'] in ('possible_conflict', 'not_checked'):
                        title = 'Review voices…' if voices['state'] == 'possible_conflict' else 'Check voices…'
                        button = self.control(title, 'voices', pair['id'],
                                              tooltip='Open this exact pair in the blend editor. Missing voice analysis runs there; saved observations are reused. Listen to each side and review optional cue changes before Apply.')
                        button.setObjectName('reviewVoices'); button.setProperty('clipId', pair['id'])
                        button.setAccessibleName(title + ' ' + pair['outgoing'] + ' to ' + pair['incoming'])
                        button.setEnabled(voices['can_check'])
                        if not voices['can_check']:
                            button.setToolTip(voices['reason']); button.setAccessibleDescription(voices['reason'])
                        column.addWidget(button)
                actions = QHBoxLayout()
                actions.addWidget(self.control("Show blend", "select", pair["id"]))
                actions.addWidget(self.control("Listen", "listen", pair["id"], tooltip="Listen to this transition with context on both sides."))
                column.addLayout(actions)
            column.addStretch(1)
            old = self.scroll.takeWidget()
            if old:
                old.deleteLater()
            self.scroll.setWidget(content)
            self.scroll.verticalScrollBar().setValue(scroll)
        if self.scroll.widget():
            self.scroll.widget().setEnabled(not busy)

    @staticmethod
    def separator():
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        return line
