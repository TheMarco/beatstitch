"""Choose an audible pitch destination without changing the source key label."""
import djmix
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget
from studio_widgets import ComboBox, display_key, parsed_key


RELATIONS = {
    'same key': 'Same key',
    'relative major/minor': 'Relative key',
    'neighboring keys on the circle of fifths': 'Neighbouring key',
}


def pitch_choices(session, track, clip, *, show_all=False):
    """Use the preceding clip's heard key, without changing any project state."""
    source = parsed_key(track.key)
    if not source:
        return {'source': None, 'restricted': False, 'items': [('Source key unknown', None, '')],
                'explanation': 'Set the source key before choosing a destination key.'}
    index = next((i for i, item in enumerate(session.clips) if item.id == clip.id), None)
    previous, previous_key = None, None
    if index:
        outgoing = session.clips[index - 1]
        previous = session.tracks[outgoing.track_id]
        previous_key = djmix.transpose(parsed_key(previous.key), outgoing.shift)
        percussion = outgoing.outro_percussive or clip.intro_percussive
    else:
        percussion = False
    restricted = previous_key is not None and not percussion
    shifts = set(range(-5, 7)) | {clip.shift}
    items = []
    for shift in sorted(shifts, key=lambda n: (abs(n), n)):
        target = djmix.transpose(source, shift)
        relation = djmix.relationship(previous_key, target) if restricted else None
        if restricted and not show_all and not relation and shift not in (0, clip.shift):
            continue
        name = display_key(target)
        label = f'Original · {name}' if shift == 0 else f'{name} ({shift:+d} st)'
        if restricted:
            label += ' · ' + (RELATIONS[relation] if relation else 'Different key')
        if shift and shift == clip.shift and restricted and not show_all and not relation:
            label = 'Current · ' + label
        tip = ('Original pitch; no transposition.' if shift == 0 else
               f'Transpose this song {shift:+d} semitones to {name}. {source[1].capitalize()} stays {source[1]}.')
        if restricted:
            tip += (f' {RELATIONS[relation]} with {previous.name} playing in {display_key(previous_key)}.' if relation else
                    f' No simple key match with {previous.name} playing in {display_key(previous_key)}.')
        if shift == clip.shift and restricted and not show_all and not relation:
            tip += ' Your current choice is kept; nothing changes until you choose another pitch.'
        if abs(shift) > 2:
            tip += ' This is a larger pitch change; listen for an artificial sound before keeping it.'
        tip += ' Based on key labels; listen to the blend to judge melodies and vocals.'
        items.append((label, shift, tip))
    if restricted:
        current = djmix.transpose(source, clip.shift)
        relation = djmix.relationship(previous_key, current)
        explanation = f'Previous plays in {display_key(previous_key)}. '
        if relation and not clip.shift:
            explanation += f'Original fits: {RELATIONS[relation].lower()}. No pitch change needed.'
        elif relation:
            explanation += f'This song plays in {display_key(current)} ({clip.shift:+d} st): {RELATIONS[relation].lower()}.'
        elif clip.shift:
            explanation += 'Current pitch has no simple match; kept until you choose another key.'
            if djmix.relationship(previous_key, source):
                explanation += ' Original would fit; choose Original to remove the shift.'
        else:
            explanation += 'Original has no simple key match. Choose a compatible key to try.'
        explanation += ' All keys shown for experimentation.' if show_all else ' Smallest shifts first.'
    elif percussion:
        explanation = f'Drums-only overlap marked: all {source[1]} keys shown. Listen for pitched bass and tails.'
    elif previous is not None:
        explanation = f'Previous key unknown: all {source[1]} keys shown. Check its key label or listen.'
    else:
        explanation = f'No previous song to match: all {source[1]} keys shown. Original keeps its pitch.'
    return {'source': source, 'restricted': restricted, 'items': items, 'explanation': explanation}


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
        title = QLabel('Play in key'); title.setProperty('role', 'section')
        heading = QHBoxLayout(); heading.setSpacing(6); heading.addWidget(title); heading.addStretch(1)
        self.all_keys = QPushButton('Show all keys'); self.all_keys.setCheckable(True)
        self.all_keys.setMinimumHeight(28)
        self.all_keys.setAccessibleName('Show all pitch destination keys')
        self.all_keys.setToolTip('Show every destination key for manual experimentation. Off shows simple harmonic matches '
                                'to the previous song after its pitch shift, plus Original and your current choice. '
                                'This only changes the list; it never changes pitch or prepares audio.')
        self.all_keys.toggled.connect(self.show_all_keys)
        heading.addWidget(self.all_keys); box.addLayout(heading)
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
        self.matches = QPushButton('Find next songs')
        self.matches.clicked.connect(host.show_compatible_songs)
        self.matches.setMinimumHeight(28)
        self.matches.setToolTip('Continue from this song’s playing key, including its pitch shift: find library songs '
                                'that fit at their original pitch. Includes relative keys, such as A minor and C major. '
                                'Select songs in the library to add them to the mix. This search does not transpose anything.')
        actions = QHBoxLayout(); actions.setSpacing(4)
        actions.addWidget(self.listen); actions.addWidget(self.matches); box.addLayout(actions)
        for control in (self.target, self.all_keys, self.listen, self.matches): control.setProperty('helpTopic', 'keys')

    def show_all_keys(self, checked):
        self.host.show_all_pitch_keys = checked
        self.host.refresh()

    def choose(self, index):
        shift = self.target.itemData(index)
        if isinstance(shift, int): self.host.set_transpose(shift)

    def refresh(self, track, clip):
        show_all = getattr(self.host, 'show_all_pitch_keys', False)
        choices = pitch_choices(self.host.session, track, clip, show_all=show_all)
        source, items = choices['source'], choices['items']
        with QSignalBlocker(self.all_keys): self.all_keys.setChecked(show_all)
        self.all_keys.setVisible(choices['restricted'])
        with QSignalBlocker(self.target):
            # Keep existing rows alive during selection and background refreshes.
            # Clearing a just-activated popup can invalidate native accessibility items.
            for index, (label, value, tip) in enumerate(items):
                if index >= self.target.count():
                    self.target.addItem(label, value)
                else:
                    if self.target.itemText(index) != label:
                        self.target.setItemText(index, label)
                    if self.target.itemData(index) != value:
                        self.target.setItemData(index, value)
                self.target.setItemData(index, tip, Qt.ItemDataRole.ToolTipRole)
                self.target.setItemData(index, label + '. ' + tip, Qt.ItemDataRole.AccessibleDescriptionRole)
            while self.target.count() > len(items):
                self.target.removeItem(self.target.count() - 1)
            if source:
                self.target.setCurrentIndex(self.target.findData(clip.shift))
                self.explanation.setText(choices['explanation'])
            else:
                self.target.setCurrentIndex(0)
                self.explanation.setText('Use More settings to set the source key or shift semitones.' if self.simple else
                                         'Set the Source key above, or shift semitones below.')
        self.target.setEnabled(bool(source))
        self.matches.setEnabled(bool(source))
        self.target.setToolTip('Changes this song’s pitch in the mix, using the source key label. '
                               'By default, show keys related to the previous song after its pitch shift. '
                               'Original and an existing manual choice are always available. Show all keys opens every destination. '
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
        for control in (self.target, self.all_keys, self.listen, self.matches): control.setAccessibleDescription(control.toolTip())
