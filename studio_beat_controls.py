"""Native controls for listening to a reference grid against original audio."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
                              QLayout, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget)

from studio_beat_grid import TapTempo
from studio_widgets import clock


class BeatControls(QScrollArea):
    bpmChanged = Signal(float)
    estimateRequested = Signal()
    openingRequested = Signal()
    anchorChanged = Signal(float)
    manualChanged = Signal(bool)
    markRequested = Signal()

    def __init__(self, duration, parent=None):
        super().__init__(parent)
        self.tapper, self.tapped = TapTempo(), None
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        box = QVBoxLayout(content)
        box.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.setWidget(content)
        box.setContentsMargins(10, 8, 10, 8)
        instructions = QLabel('Turn on Click and listen near the beginning, middle and end. '
                              'A higher click marks beat 1 of each four-beat bar. Pause and zoom to a kick before marking beat 1.')
        instructions.setWordWrap(True)
        box.addWidget(instructions)
        estimate_row = QHBoxLayout()
        self.tempo_summary = QLabel()
        self.tempo_summary.setWordWrap(True)
        self.tempo_summary.setAccessibleName('Beat-based tempo check')
        self.tempo_summary.setToolTip('A second estimate from the locally detected beats. It can count every other beat, '
                                      'miss beats, or follow a different rhythm. Agreement is not a confidence percentage.')
        estimate_row.addWidget(self.tempo_summary, 1)
        self.try_estimate = QPushButton('Try beat estimate')
        self.try_estimate.setObjectName('tryBeatTempo')
        self.try_estimate.setToolTip('Try this BPM with a manual reference click in this draft. The original song keeps playing '
                                    'at its original speed. Listen near the start, middle and ending, then choose Apply changes '
                                    'to change the mix, or Discard draft to keep your saved settings.')
        self.try_estimate.clicked.connect(self.estimateRequested)
        alternatives = QVBoxLayout()
        alternatives.addWidget(self.try_estimate)
        self.try_opening = QPushButton('Try opening estimate')
        self.try_opening.setObjectName('tryOpeningTempo')
        self.try_opening.setToolTip('Try the separate estimate from an opening passage. It can be wrong too. '
                                   'Listen with Click before applying; Discard draft keeps your saved BPM.')
        self.try_opening.clicked.connect(self.openingRequested)
        alternatives.addWidget(self.try_opening)
        estimate_row.addLayout(alternatives)
        box.addLayout(estimate_row)
        row = QHBoxLayout()
        row.addWidget(QLabel('Source BPM'))
        self.bpm = QDoubleSpinBox()
        self.bpm.setRange(20, 300); self.bpm.setDecimals(3); self.bpm.setSingleStep(.01)
        self.bpm.setAccessibleName('Draft source BPM')
        self.bpm.setToolTip('The original song’s tempo. Applying this changes the speed of every clip using this source in this mix. '
                            'While checking beats, the song keeps its original speed.')
        self.bpm.valueChanged.connect(self.bpmChanged)
        row.addWidget(self.bpm)
        self.half, self.double = QPushButton('÷2'), QPushButton('×2')
        for button, factor, label in ((self.half, .5, 'Halve'), (self.double, 2, 'Double')):
            button.setAccessibleName(label + ' draft source BPM')
            button.setToolTip(label + ' the draft BPM if the estimate counts every other beat or twice per beat. Listen before applying.')
            button.clicked.connect(lambda checked=False, f=factor: self.bpmChanged.emit(self.actual_bpm * f))
            row.addWidget(button)
        self.tap = QPushButton('Tap tempo')
        self.tap.setToolTip('Tap once per beat at least four times. Keep tapping to refine the estimate; a gap over three seconds starts again. '
                            'The result changes nothing until you choose Use tapped BPM.')
        self.tap.clicked.connect(self.tap_tempo)
        row.addWidget(self.tap)
        self.tap_value = QLabel('Tap 4+ beats')
        row.addWidget(self.tap_value, 1)
        self.use_tapped = QPushButton('Use tapped BPM')
        self.use_tapped.setEnabled(False)
        self.use_tapped.setToolTip('Try the displayed tap estimate in this draft. Apply changes is still required to change the mix.')
        self.use_tapped.clicked.connect(lambda: self.bpmChanged.emit(self.tapped['bpm']))
        row.addWidget(self.use_tapped)
        box.addLayout(row)
        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(['Detected grid', 'Manual regular grid'])
        self.mode.setAccessibleName('Beat grid source')
        self.mode.setToolTip('Detected uses only supported observations. Manual uses a steady reference at your BPM and marked beat 1. '
                             'A manual grid does not straighten drifting audio or prove the rhythm is correct.')
        self.mode.currentIndexChanged.connect(lambda index: self.manualChanged.emit(index == 1))
        row.addWidget(self.mode)
        row.addWidget(QLabel('Beat 1 at'))
        self.anchor = QDoubleSpinBox()
        self.anchor.setDecimals(3); self.anchor.setRange(0, max(0, duration - .001)); self.anchor.setSuffix(' s')
        self.anchor.setSingleStep(.01)
        self.anchor.setAccessibleName('Manual bar-start reference in source seconds')
        self.anchor.setToolTip('A known first beat of a bar anywhere in the song. The regular grid extends before and after it. '
                               'Nudge by 10 milliseconds or type a source time.')
        self.anchor.valueChanged.connect(self.anchorChanged)
        row.addWidget(self.anchor)
        self.mark = QPushButton('Mark playhead as beat 1')
        self.mark.setToolTip('Pause and click a kick in the zoomed waveform, then mark it as beat 1 of a bar. '
                             'This sets a manual reference; it does not move any audio or song cues.')
        self.mark.clicked.connect(self.markRequested)
        row.addWidget(self.mark)
        row.addStretch(1)
        box.addLayout(row)
        note = QLabel('Grid edits are a listening and snapping reference. BPM is a source setting for all uses of this song; '
                      'Start and End still affect only the selected clip. Apply changes commits the draft as one undoable edit.')
        note.setWordWrap(True); note.setProperty('role', 'secondary')
        box.addWidget(note)
        box.addStretch(1)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)

    def sync(self, bpm, anchor, manual):
        self.actual_bpm = bpm
        for field, value in ((self.bpm, bpm), (self.anchor, anchor)):
            field.blockSignals(True); field.setValue(value); field.blockSignals(False)
        self.mode.blockSignals(True); self.mode.setCurrentIndex(int(manual)); self.mode.blockSignals(False)
        self.half.setEnabled(bpm / 2 >= 20); self.double.setEnabled(bpm * 2 <= 300)

    def show_tempo_estimate(self, estimate, bpm, opening=None):
        candidate = estimate.get('bpm')
        opening_bpm = (opening or {}).get('bpm')
        self.try_estimate.setVisible(candidate is not None)
        self.try_estimate.setEnabled(candidate is not None and abs(candidate - bpm) >= .001)
        self.try_opening.setVisible(opening_bpm is not None)
        self.try_opening.setEnabled(opening_bpm is not None and abs(opening_bpm - bpm) >= .001)
        if opening_bpm is not None:
            self.try_opening.setText(f'Try opening: {opening_bpm:.2f} BPM')
        if candidate is None:
            text = ('Beat-based tempo is inconclusive. Keep your BPM or tap along to check it.' if estimate else
                    'Analyze song to get a second BPM estimate from the detected beats.')
            if opening_bpm is not None:
                text += f' Opening-passage estimate: {opening_bpm:.2f} BPM. You can try it in this draft.'
            self.tempo_summary.setText(text)
            return
        self.try_estimate.setText(f'Try beat: {candidate:.2f} BPM')
        comparison = opening_bpm if opening_bpm is not None and abs(candidate / opening_bpm - 1) > .02 else bpm
        ratio = candidate / comparison
        if abs(ratio - .5) <= .025 or abs(ratio - 2) <= .1:
            detail = ('The two estimates count the rhythm at half/double speed. Either estimate may be wrong. '
                      'Compare the click with the main beat and four-beat bars.')
        elif abs(ratio - 1) > .02:
            detail = 'The estimates disagree. Try an estimate and listen with Click before deciding.'
        else:
            detail = 'The estimates are close. Check that the click stays with the music; this does not verify every beat.'
        text = f'Your draft: {bpm:g} BPM · Beat-based estimate: {candidate:.2f} BPM.'
        if opening_bpm is not None:
            text += f' Opening-passage estimate: {opening_bpm:.2f} BPM.'
        self.tempo_summary.setText(f'{text} {detail}')

    def tap_tempo(self):
        self.tapped = self.tapper.tap()
        self.use_tapped.setEnabled(bool(self.tapped and self.tapped['steady']))
        self.tap_value.setText((f"{self.tapped['bpm']:g} BPM" + ('' if self.tapped['steady'] else ' · keep tapping steadily'))
                              if self.tapped else f'{len(self.tapper.times)} taps · keep tapping')


class GridAudition(QWidget):
    prepareRequested = Signal(float, bool)
    failed = Signal(str)

    def __init__(self, track, parent=None):
        super().__init__(parent)
        from studio_grid_audio import GridTransport
        self.track, self.ready = track, False
        self.grid = {}
        self.player = GridTransport(self)
        self.player.errorOccurred.connect(self.failed)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playingChanged.connect(lambda playing: self.play_button.setText('Pause song' if playing else 'Listen'))
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(5)
        row = QHBoxLayout()
        self.play_button = QPushButton('Listen')
        self.play_button.clicked.connect(self.toggle)
        self.play_button.setToolTip('Listen to the original song, with an optional click. BPM and grid edits stay drafts; this does not play the processed blend.')
        row.addWidget(self.play_button, 1)
        for label, fraction in (('Start', 0.), ('Middle', .5), ('Ending', 1.)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, f=fraction: self.listen_at(max(0, self.track.duration - 30) if f == 1 else f * self.track.duration))
            button.setToolTip('Listen to the ' + label.lower() + ' of the original song to check whether the click stays aligned.')
            row.addWidget(button, 1)
        self.click = QCheckBox('Click')
        self.click.setToolTip('Add a quiet reference click on the same audio clock. Higher clicks mark beat 1. '
                              'This is for checking only and never appears in the exported mix.')
        self.click.toggled.connect(self.player.set_click)
        row.addWidget(self.click)
        box.addLayout(row)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, round(track.duration * 1000))
        self.seek.setAccessibleName('Position in original song with reference click')
        self.seek.setSingleStep(1000); self.seek.setPageStep(5000)
        self.seek.setToolTip('Seek in original source audio. Left/Right moves one second; Page Up/Down moves five seconds; Home/End goes to either end. Seeking keeps paused audio paused. Cue points stay unchanged.')
        self.seek.setAccessibleDescription(self.seek.toolTip())
        self.seek.valueChanged.connect(self.seek_to)
        box.addWidget(self.seek)
        self.position = QLabel(); box.addWidget(self.position)
        self.position_changed(0)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)

    def set_grid(self, grid):
        self.grid = grid
        self.click.setEnabled(bool(grid.get('beats')))
        if not grid.get('beats'):
            self.click.setChecked(False)
        if self.ready:
            # Container duration can include encoder padding beyond decoded PCM.
            from studio_ab_audio import RATE
            frames = self.player.mixer.frames
            within = lambda times: [t for t in times if 0 <= round(t * RATE) < frames]
            self.player.set_grid(within(grid.get('beats', [])), within(grid.get('downbeats', [])))

    def load(self, report):
        self.player.load(report)
        self.ready = True
        self.set_grid(self.grid)

    def position_changed(self, ms):
        if not self.seek.isSliderDown():
            self.seek.blockSignals(True); self.seek.setValue(ms); self.seek.blockSignals(False)
        self.position.setText(f'{clock(ms / 1000)} / {clock(self.track.duration)} · Original speed and pitch')

    def toggle(self):
        self.player.pause() if self.player.playing else self.listen_at(self.player.position() / 1000)

    def listen_at(self, seconds):
        if not self.ready:
            self.prepareRequested.emit(seconds, True)
        else:
            self.player.seek(round(seconds * 1000)); self.player.play()

    def seek_to(self, ms):
        if self.ready:
            self.player.seek(ms)
        else:
            self.prepareRequested.emit(ms / 1000, False)

    def stop(self):
        self.player.pause()

    def close_audio(self):
        self.player.close()
