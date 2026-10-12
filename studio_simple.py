"""A compact, outcome-focused song inspector; the host owns all mix edits."""
import djmix
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import studio_theme as T
from studio_widgets import display_key, parsed_key, tool_button


class SimpleSong(QFrame):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.setObjectName('box')
        self.setFixedWidth(244)
        self.setProperty('helpTopic', 'workspace')
        box = QVBoxLayout(self); box.setContentsMargins(9, 7, 9, 7); box.setSpacing(5)
        self.position = QLabel('Selected song'); self.position.setProperty('role', 'small'); box.addWidget(self.position)
        self.name = QLabel(); self.name.setWordWrap(True); self.name.setTextFormat(Qt.TextFormat.PlainText)
        self.name.setStyleSheet('font-size: 14px; font-weight: 600'); box.addWidget(self.name)
        self.metadata = QLabel(); self.metadata.setWordWrap(True); self.metadata.setFont(T.font(13)); box.addWidget(self.metadata)
        self.pitch = QLabel(); self.pitch.setWordWrap(True); box.addWidget(self.pitch)
        self.pitch.setToolTip('Pitch applies to this whole song in the mix, including both neighboring blends. Matching BPM preserves the original pitch unless you choose a shift.')
        self.pitch.setProperty('helpTopic', 'keys')
        from studio_pitch_ui import PlayKey
        self.play_key = PlayKey(host, simple=True); box.addWidget(self.play_key)
        pitch_actions = QHBoxLayout(); pitch_actions.setSpacing(4)
        self.pitch_toggle = QPushButton(); self.pitch_toggle.clicked.connect(self.toggle_pitch)
        self.pitch_toggle.setProperty('helpTopic', 'keys'); pitch_actions.addWidget(self.pitch_toggle)
        self.suggest_key = QPushButton(); self.suggest_key.clicked.connect(host.match_key)
        self.suggest_key.setProperty('helpTopic', 'keys'); pitch_actions.addWidget(self.suggest_key)
        box.addLayout(pitch_actions)
        self.timing = QLabel(); self.timing.setWordWrap(True); self.timing.setProperty('helpTopic', 'timing')
        box.addWidget(self.timing)
        self.cues = QLabel(); self.cues.setWordWrap(True); self.cues.setProperty('role', 'small'); box.addWidget(self.cues)
        box.addStretch(1)
        actions = QHBoxLayout(); actions.setSpacing(4)
        self.cue_button = QPushButton('Song cues…'); self.cue_button.clicked.connect(host.review_song_cues)
        self.cue_button.setProperty('helpTopic', 'musical-cues')
        self.cue_button.setToolTip('Check beats and musical sections, audition start/end choices, then apply a cue draft. You can also drag Start and End on the real waveform.')
        actions.addWidget(self.cue_button)
        self.more = QPushButton('More settings…'); self.more.clicked.connect(host.show_song_settings)
        self.more.setToolTip('Show Advanced controls for this song: exact cues, source BPM and key, manual pitch, and tempo correction. This does not change the sound.')
        actions.addWidget(self.more); box.addLayout(actions)
        self.extend = QPushButton('Extend drums…'); self.extend.clicked.connect(host.show_loop_extension)
        self.extend.setProperty('helpTopic', 'drum-loops')
        self.extend.setToolTip('Make a longer intro or outro by repeating complete drum bars. Listen to the repeated passage and neighboring blend before applying an undoable source copy.')
        box.addWidget(self.extend)
        self.separate = QPushButton('Reduce competing parts…'); self.separate.clicked.connect(host.show_separation)
        self.separate.setProperty('helpTopic', 'separation')
        self.separate.setToolTip('Try reducing voices or keeping mostly drums from this song during one blend. Listen before applying; Undo restores the original clip.')
        box.addWidget(self.separate)
        moves = QHBoxLayout(); moves.setSpacing(4)
        self.earlier = tool_button('arrow-left', 'Move selected song earlier in the mix', lambda: host.nudge(-1))
        self.later = tool_button('arrow-right', 'Move selected song later in the mix', lambda: host.nudge(1))
        self.remove = QPushButton('Remove song'); self.remove.clicked.connect(host.remove_clip)
        self.remove.setToolTip('Remove this clip from the arrangement. The source stays in the library. Undo restores it.')
        moves.addWidget(self.earlier); moves.addWidget(self.later); moves.addStretch(); moves.addWidget(self.remove)
        box.addLayout(moves)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False); button.setMinimumHeight(28)
        for widget in self.findChildren(QWidget):
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())

    def toggle_pitch(self):
        clip = self.host.selected_clip()
        if clip is not None:
            self.host.set_clip_value('transpose', not clip.transpose)

    def update_song(self, track, clip, index):
        host = self.host
        self.position.setText(f'Selected song · {index + 1} of {len(host.session.clips)}')
        self.name.setText(track.name); self.name.setToolTip(track.name)
        key = display_key(track.key) if track.key else 'Key unknown'
        if track.bpm:
            from studio_tempo_curve import clip_tempo_range
            low, high = clip_tempo_range(host.session, index)
            mix = f'{low:g} BPM ({(low / track.bpm - 1) * 100:+.1f}%)' if low == high else f'{low:g}–{high:g} BPM · follows the tempo curve'
            self.metadata.setText(f'Source {track.bpm:.1f} BPM · {key}\nMix {mix}')
        else:
            self.metadata.setText(f'Source BPM unknown · {key}')
        self.metadata.setToolTip('Source BPM and key describe the original file and may be estimates. Mix BPM is the common playback speed; the percentage shows the speed change. More settings lets you correct source labels.')
        result = djmix.transpose(parsed_key(track.key), clip.shift)
        unit = 'semitone' if abs(clip.shift) == 1 else 'semitones'
        self.pitch.setText(f'Pitch shifted {clip.shift:+d} {unit}' + (f' · {display_key(result)}' if result else '')
                           if clip.shift else 'Keeping the original key')
        self.play_key.refresh(track, clip)
        self.pitch_toggle.setVisible(clip.semitones != 0)
        self.pitch_toggle.setText('Use original key' if clip.transpose else f'Restore {clip.semitones:+d} shift')
        self.pitch_toggle.setToolTip('Turn pitch shifting off, remembering the chosen shift.' if clip.transpose else
                                    'Restore the remembered pitch shift for this clip. Listen to both neighboring blends afterward.')
        suggestion = host.key_suggestion
        self.suggest_key.setVisible(bool(suggestion and suggestion['semitones'] not in (0, clip.shift, clip.semitones)))
        if suggestion:
            shift = suggestion['semitones']
            self.suggest_key.setText(f'Try {shift:+d} semitone' + ('s' if abs(shift) != 1 else ''))
            self.suggest_key.setToolTip(host.match_btn.toolTip())
        self.timing.setText(('Tempo correction on · ' if clip.stabilize else '') + host.timing_result.text())
        self.timing.setToolTip(host.timing_result.toolTip() + ' Choose More settings to analyze, compare or change correction.')
        self.cues.setText(f'Kept audio: {clip.cue_in // 60:.0f}:{clip.cue_in % 60:04.1f} – '
                          f'{clip.cue_out // 60:.0f}:{clip.cue_out % 60:04.1f}' +
                          (f'\n{len(clip.protected_moments)} protected passage' + ('s' if len(clip.protected_moments) != 1 else '')
                           if clip.protected_moments else ''))
        self.cues.setToolTip('These Start/End times refer to the original song. Only this clip’s kept audio is used in the mix.')
        self.cue_button.setEnabled(not host.jobs)
        self.extend.setEnabled(not host.jobs)
        self.separate.setEnabled(not host.jobs and len(host.session.clips) > 1)
        self.earlier.setEnabled(index > 0); self.later.setEnabled(index < len(host.session.clips) - 1)
        for widget in (self.metadata, self.pitch_toggle, self.suggest_key, self.timing, self.cues):
            widget.setAccessibleDescription(widget.toolTip())
