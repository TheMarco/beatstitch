"""Native, purpose-based export choices."""
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox,
                              QLabel, QRadioButton, QVBoxLayout)

from studio_export import PRESETS, estimate_bytes
from studio_widgets import clock


class ExportDialog(QDialog):
    def __init__(self, duration, preset="wav", tracklist=False, parent=None, *, mastering_enabled=False):
        super().__init__(parent)
        self.setWindowTitle("Export your mix")
        self.setMinimumWidth(450)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 20, 24, 20)
        box.setSpacing(12)
        title = QLabel(f"Your mix · {clock(duration)}")
        title.setProperty("role", "title")
        box.addWidget(title)
        subtitle = QLabel("Choose how you will use this audio file. Your project stays editable.")
        subtitle.setWordWrap(True)
        box.addWidget(subtitle)
        self.mastering_state = QLabel('Automatic mastering: ' + ('On — included in this export.' if mastering_enabled else 'Off — exporting the mix before mastering.'))
        self.mastering_state.setWordWrap(True)
        self.mastering_state.setToolTip('This follows the project choice in Mastering beside Export, or Edit → Automatic mastering. Cancel this dialog to change it or compare before/after. Listening volume and A/B volume matching never change export loudness.')
        box.addWidget(self.mastering_state)
        self.choices = QButtonGroup(self)
        self.buttons = {}
        for identity, info in PRESETS.items():
            size = estimate_bytes(duration, identity) / (1024 * 1024)
            control = QRadioButton(f"{info['label']} — {identity.upper()}\n{info['description']} · about {size:.0f} MB")
            control.setMinimumHeight(52)
            control.setToolTip("FLAC size varies with the music; this is an estimate." if identity == "flac" else
                               "The exported sound includes all mix changes and checked peak headroom.")
            self.choices.addButton(control)
            self.buttons[identity] = control
            box.addWidget(control)
        self.buttons[preset if preset in self.buttons else "wav"].setChecked(True)
        self.tracklist = QCheckBox("Include a tracklist with song timestamps (CSV)")
        self.tracklist.setChecked(tracklist)
        self.tracklist.setToolTip("Creates a separate file beside your audio. Song start times include their incoming overlap.")
        box.addWidget(self.tracklist)
        hint = QLabel("Listening volume does not change export loudness. WAV and FLAC preserve the master quality; MP3 is smaller and widely playable.")
        hint.setWordWrap(True)
        hint.setProperty("role", "secondary")
        box.addWidget(hint)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Save).setText("Choose where to save…")
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        box.addWidget(controls)

    def preset(self):
        return next(identity for identity, button in self.buttons.items() if button.isChecked())
