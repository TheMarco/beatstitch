"""Choose an audible pitch destination without changing the source key label."""
import djmix
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget
from studio_widgets import ComboBox, display_key, parsed_key


class PitchAmount(QSpinBox):
    def __init__(self):
        super().__init__()
        self.setRange(-12, 12)
        self.setSuffix(' st')
        self.setKeyboardTracking(False)
        self.setMinimumHeight(28)
        self.setFixedWidth(78)

    def textFromValue(self, value):
        return f'{value:+d}' if value else '0'


class PlayKey(QWidget):
    def __init__(self, host, *, simple=False):
        super().__init__()
        self.host = host
        self.simple = simple
        self.setProperty('helpTopic', 'keys')
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(4)
        title = QLabel('Play in key'); title.setProperty('role', 'section'); box.addWidget(title)
        self.target = ComboBox()
        self.target.setAccessibleName('Play in key')
        self.target.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.target.activated.connect(self.choose)
        box.addWidget(self.target)
        self.explanation = QLabel(); self.explanation.setWordWrap(True)
        self.explanation.setProperty('role', 'hint'); box.addWidget(self.explanation)
        self.listen = QPushButton('Listen to blend')
        self.listen.clicked.connect(host.listen_pitch)
        self.listen.setMinimumHeight(28)
        self.matches = QPushButton('Find matches')
        self.matches.clicked.connect(host.show_compatible_songs)
        self.matches.setMinimumHeight(28)
        self.matches.setToolTip('Find library songs with a compatible key at their original pitch. '
                                'Includes relative keys, such as A minor and C major. This does not transpose anything.')
        actions = QHBoxLayout(); actions.setSpacing(4)
        actions.addWidget(self.listen); actions.addWidget(self.matches); box.addLayout(actions)
        for control in (self.target, self.listen, self.matches): control.setProperty('helpTopic', 'keys')

    def choose(self, index):
        shift = self.target.itemData(index)
        if isinstance(shift, int): self.host.set_transpose(shift)

    def refresh(self, track, clip):
        source = parsed_key(track.key)
        items = [('Source key unknown', None)]
        if source:
            items = [(f'Original · {display_key(source)}', 0)]
            # One destination per chromatic root, always retaining the source mode.
            for shift in range(-5, 7):
                if shift:
                    target = djmix.transpose(source, shift)
                    items.append((f'{display_key(target)} ({shift:+d} st)', shift))
            # Preserve an existing octave / larger manual shift without silently folding it.
            if clip.shift not in [shift for _, shift in items]:
                items.append((f'{display_key(djmix.transpose(source, clip.shift))} ({clip.shift:+d} st)', clip.shift))
        with QSignalBlocker(self.target):
            # Keep existing rows alive during selection and background refreshes.
            # Clearing a just-activated popup can invalidate native accessibility items.
            for index, (label, value) in enumerate(items):
                if index >= self.target.count():
                    self.target.addItem(label, value)
                else:
                    if self.target.itemText(index) != label:
                        self.target.setItemText(index, label)
                    if self.target.itemData(index) != value:
                        self.target.setItemData(index, value)
            while self.target.count() > len(items):
                self.target.removeItem(self.target.count() - 1)
            if source:
                self.target.setCurrentIndex(self.target.findData(clip.shift))
                self.explanation.setText(f'{source[1].capitalize()} keys only. Choose a key, then listen.')
            else:
                self.target.setCurrentIndex(0)
                self.explanation.setText('Use More settings to set the source key or shift semitones.' if self.simple else
                                         'Set the Source key above, or shift semitones below.')
        self.target.setEnabled(bool(source))
        self.matches.setEnabled(bool(source))
        self.target.setToolTip('Changes this song’s pitch in the mix, using the source key label. '
                               'Major stays major and minor stays minor. Original restores zero shift. '
                               'Changing a label does not change the recording. Listen again to hear edits; '
                               'audio already playing is unchanged.')
        has_blend = len(self.host.session.clips) > 1
        self.listen.setText('Listen to blend' if has_blend else 'Listen in mix')
        self.listen.setEnabled(not self.host.jobs)
        self.listen.setToolTip('Prepare and play the selected blend with the current pitch setting. '
                               'The library Listen button always plays the original recording.' if has_blend else
                               'Prepare and play this one-song mix with its current pitch setting. '
                               'The library Listen button always plays the original recording.')
        for control in (self.target, self.listen, self.matches): control.setAccessibleDescription(control.toolTip())
