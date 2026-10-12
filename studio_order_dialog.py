"""Native draft planner with explicit constraints, change summary and pair audition."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QRadioButton,
                              QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                              QSlider, QSplitter, QTextEdit, QScrollArea,
                              QVBoxLayout, QWidget, QSizePolicy)

from studio_order import OrderOptions, PACES, default_options, propose_order
from studio_energy import estimate_mix_energy
from studio_transition_audio import BlendTransport, prepare_blend
from studio_transition_view import TransitionView
from studio_widgets import clock, display_key


class OrderChoice(QFrame):
    """Stable, real widgets: no virtual table-cell accessibility identities."""
    def __init__(self, index, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self); row.setContentsMargins(4, 7, 4, 7)
        self.select = QRadioButton(str(index + 1)); row.addWidget(self.select)
        text = QVBoxLayout(); text.setSpacing(2)
        self.name, self.detail = QLabel(), QLabel()
        for label in (self.name, self.detail):
            label.setWordWrap(True); label.setTextFormat(Qt.TextFormat.PlainText)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            text.addWidget(label)
        self.detail.setProperty('role', 'secondary')
        row.addLayout(text, 1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.select.setChecked(True); event.accept()
        else:
            super().mousePressEvent(event)


class OrderAudition(QDialog):
    def __init__(self, session, index, bands, report, output, volume, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'Draft blend {index} → {index + 1}')
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(950, 550)
        self.output = output
        self.player = BlendTransport(self)
        self.devices = QMediaDevices(self)
        box = QVBoxLayout(self)
        note = QLabel(f'Draft blend {index} → {index + 1} · Your mix has not changed.\n'
                      'Listen for clashing notes, doubled kicks and awkward changes in energy.')
        note.setWordWrap(True); box.addWidget(note)
        view = TransitionView(); view.setMinimumHeight(180)
        view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        view.set_data(session, index, bands, report['start'], report['end'])
        from studio_tempo_curve import session_curve
        curve = session_curve(session); origin = float(curve.to_output(report['start']))
        view.seekRequested.connect(lambda seconds: self.player.seek(round(float(curve.to_output(report['start'] + seconds) - origin) * 1000)))
        self.player.positionChanged.connect(lambda ms: view.set_playhead(float(curve.to_reference(origin + ms / 1000)) - report['start']))
        box.addWidget(view, 1)
        row = QHBoxLayout()
        self.play = QPushButton('Listen'); self.play.clicked.connect(self.toggle)
        self.play.setToolTip('Play or pause this proposed blend. Nothing is applied to the mix.')
        row.addWidget(self.play)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, round(report['duration'] * 1000)); self.seek.setAccessibleName('Draft blend position')
        self.seek.setSingleStep(1000); self.seek.setPageStep(5000)
        self.seek.setToolTip('Seek within this proposed blend. Left/Right moves one second; Page Up/Down moves five seconds; Home/End goes to either end. Seeking keeps paused audio paused.')
        self.seek.setAccessibleDescription(self.seek.toolTip())
        self.seek.valueChanged.connect(self.player.seek); row.addWidget(self.seek, 1)
        self.time = QLabel(); row.addWidget(self.time)
        loop = QCheckBox('Loop'); loop.setChecked(True); loop.toggled.connect(self.player.set_loop)
        loop.setToolTip('Repeat this preview while you decide. It does not loop the exported mix.')
        row.addWidget(loop); box.addLayout(row)
        self.status = QLabel(); self.status.setWordWrap(True); box.addWidget(self.status)
        close = QPushButton('Back to the plan'); close.clicked.connect(self.reject); box.addWidget(close)
        close.setToolTip('Return to the suggested order without changing the mix.')
        self.duration = report['duration']
        self.player.load(report['folder']); self.player.set_volume(volume); self.player.set_loop(True)
        self.player.positionChanged.connect(self.position)
        self.player.playingChanged.connect(lambda value: self.play.setText('Pause' if value else 'Listen'))
        self.player.errorOccurred.connect(self.status.setText)
        self.devices.audioOutputsChanged.connect(self.refresh_output)
        self.finished.connect(lambda _: self.player.close())
        self.position(0); self.refresh_output()

    def refresh_output(self):
        device = QMediaDevices.defaultAudioOutput() if self.output == 'system' else next(
            (d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == self.output), None)
        self.player.set_device(device)
        self.play.setEnabled(self.player.sink is not None)
        self.status.setText('Output: ' + device.description() if self.player.sink is not None else
                            'Output unavailable. Reconnect it or choose another output in the main window.')

    def position(self, milliseconds):
        self.seek.blockSignals(True); self.seek.setValue(milliseconds); self.seek.blockSignals(False)
        self.time.setText(f'{clock(milliseconds / 1000)} / {clock(self.duration)}')

    def toggle(self):
        self.player.pause() if self.player.playing else self.player.play()


class OrderDialog(QDialog):
    def __init__(self, session, bands, cache_dir, output, volume, start_job, apply_draft, parent=None,
                 *, new_mix=False, review_notice=''):
        super().__init__(parent)
        self.session = copy.deepcopy(session)
        self.bands, self.cache_dir, self.output, self.volume = bands, cache_dir, output, volume
        self.start_job, self.apply_draft = start_job, apply_draft
        self.new_mix = new_mix
        self.back_label = 'Back to settings' if new_mix else 'Keep current mix'
        self.proposal = None
        self.order_index = -1
        self.busy = self.closing_after_job = False
        self.cancel = None
        self.audition = None
        self.help_window = None
        self.setProperty('helpTopic', 'planning')
        self.setWindowTitle('Plan song order')
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(920, 600); self.resize(1120, 740)
        box = QVBoxLayout(self); box.setContentsMargins(20, 18, 20, 18); box.setSpacing(10)
        intro = QLabel('Plan the flow of your mix'); intro.setProperty('role', 'title'); box.addWidget(intro)
        note = QLabel('Try an order, hear its blends, then apply it. Your current mix stays unchanged while you plan.')
        if any(c.protected_moments for c in session.clips):
            note.setText(note.text() + ' Songs with protected passages stay included, with those passages outside all blends.')
        note.setWordWrap(True); box.addWidget(note)
        if review_notice:
            notice = QLabel(review_notice); notice.setWordWrap(True)
            notice.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(notice)
        self.controls = QWidget(); grid = QGridLayout(self.controls); grid.setContentsMargins(0, 0, 0, 0)
        options = default_options(session)
        self.pace, self.opener, self.closer = QComboBox(), QComboBox(), QComboBox()
        for value, title in PACES.items():
            self.pace.addItem(title, value)
        self.pace.setCurrentIndex(max(0, self.pace.findData(options.pace)))
        for control, selected in ((self.opener, options.opener), (self.closer, options.closer)):
            control.addItem('Let the planner choose', '')
            for i, clip in enumerate(session.clips):
                control.addItem(f'{i + 1}. {session.tracks[clip.track_id].name}', clip.id)
            control.setCurrentIndex(max(0, control.findData(selected)))
        for col, (title, control, tip) in enumerate((
            ('Energy direction', self.pace, 'Uses your ratings first, then optional audio activity estimates. Automatic estimates have less influence than your ratings. Listen to judge the musical flow.'),
            ('Opening song', self.opener, 'Always include this song first. It must not conflict with a fixed position.'),
            ('Closing song', self.closer, 'Always include this song last. It must not conflict with a fixed position.'))):
            grid.addWidget(QLabel(title), 0, col); grid.addWidget(control, 1, col)
            control.setMinimumContentsLength(18)
            control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            control.setMinimumHeight(28)
            control.setToolTip(tip); control.currentIndexChanged.connect(self.invalidate)
        self.shorter = QCheckBox('Choose a shorter set; songs may be left out')
        self.shorter.setChecked(options.allow_omissions)
        self.shorter.setToolTip('Explicitly allow a subset of at least two songs. Openers, closers and fixed-position songs remain included. Omitted songs stay in your library.')
        self.minutes = QDoubleSpinBox(); self.minutes.setRange(.5, 1440); self.minutes.setDecimals(1); self.minutes.setSuffix(' min')
        self.minutes.setValue((options.target_seconds or 1800) / 60)
        self.minutes.setAccessibleName('Target set duration in minutes')
        self.minutes.setToolTip('Aim for this duration using whole clips. The draft shows the actual duration and difference; it does not secretly trim songs.')
        self.minutes.setEnabled(self.shorter.isChecked())
        self.shorter.toggled.connect(lambda value: (self.minutes.setEnabled(value), self.invalidate()))
        self.minutes.valueChanged.connect(self.invalidate)
        grid.addWidget(self.shorter, 2, 0, 1, 2); grid.addWidget(self.minutes, 2, 2)
        self.auto_energy = QCheckBox('Estimate energy from audio when I have not rated a song')
        self.auto_energy.setChecked(options.auto_energy)
        self.auto_energy.setToolTip('Analyze the kept part of each unrated song locally. Uses attack density, brightness, spectral spread and sustained level; not mastering gain. These are rough activity estimates, not emotional or quality scores. Your ratings always win. Turn off to use manual ratings only.')
        self.auto_energy.toggled.connect(self.invalidate)
        grid.addWidget(self.auto_energy, 3, 0, 1, 3)
        box.addWidget(self.controls)
        split = QSplitter(Qt.Orientation.Horizontal); split.setChildrenCollapsible(False)
        left = QWidget(); left_box = QVBoxLayout(left); left_box.setContentsMargins(0, 0, 0, 0)
        left_box.addWidget(QLabel('Your songs · optional energy ratings and position locks'))
        self.songs = QScrollArea(); self.songs.setWidgetResizable(True)
        song_fields = QWidget(); song_grid = QGridLayout(song_fields)
        song_grid.setContentsMargins(4, 4, 4, 4); song_grid.setHorizontalSpacing(8); song_grid.setVerticalSpacing(8)
        song_grid.setColumnStretch(0, 1)
        for col, title in enumerate(('Song', 'BPM / key', 'Energy', 'Keep position')):
            song_grid.addWidget(QLabel(title), 0, col)
        self.songs.setWidget(song_fields)
        self.ratings, self.locks, self.energy_labels = {}, {}, {}
        for row, clip in enumerate(session.clips):
            track = session.tracks[clip.track_id]
            name = QLabel(f'{row + 1}. {track.name}'); name.setWordWrap(True); name.setToolTip(track.name)
            song_grid.addWidget(name, row + 1, 0)
            song_grid.addWidget(QLabel(f'{track.bpm:.1f}\n{display_key(track.key)}'), row + 1, 1)
            rating = QComboBox()
            for title, value in [('Use estimate', None), ('1 · Gentle', 1), ('2 · Low', 2), ('3 · Medium', 3), ('4 · High', 4), ('5 · Peak', 5)]:
                rating.addItem(title, value)
            rating.setCurrentIndex(clip.energy or 0)
            rating.setAccessibleName(f'Energy of {track.name}')
            rating.setToolTip('Use the audio estimate, or override it with your impression from 1 (gentle) to 5 (peak). Your rating takes precedence and is saved only when you apply. Switch off automatic estimates above to leave unrated energy unknown.')
            rating.currentIndexChanged.connect(self.invalidate)
            rating.setMinimumHeight(30)
            field = QWidget(); field_box = QVBoxLayout(field); field_box.setContentsMargins(0, 0, 0, 0); field_box.setSpacing(2)
            field_box.addWidget(rating)
            result = QLabel(); result.setWordWrap(True); result.setTextFormat(Qt.TextFormat.PlainText)
            field_box.addWidget(result); self.energy_labels[clip.id] = result
            song_grid.addWidget(field, row + 1, 2); self.ratings[clip.id] = rating
            locked = QCheckBox(f'Position {row + 1}'); locked.setChecked(clip.order_locked)
            locked.setToolTip('Keep this song at exactly this position in suggestions, and always include it in a shorter set. Manual arrangement edits remain possible.')
            locked.toggled.connect(self.invalidate); song_grid.addWidget(locked, row + 1, 3); self.locks[clip.id] = locked
        song_grid.setRowStretch(len(session.clips) + 1, 1)
        left_box.addWidget(self.songs); split.addWidget(left)
        right = QWidget(); right_box = QVBoxLayout(right); right_box.setContentsMargins(0, 0, 0, 0)
        right_box.addWidget(QLabel('Proposed order · select a song to hear its incoming blend'))
        self.order = QScrollArea(); self.order.setWidgetResizable(True)
        self.order.setAccessibleName('Proposed song order')
        proposed_fields = QWidget(); proposed_box = QVBoxLayout(proposed_fields)
        proposed_box.setContentsMargins(4, 4, 4, 4); proposed_box.setSpacing(2)
        self.order_group = QButtonGroup(self); self.order_rows = []
        for index in range(len(session.clips)):
            choice = OrderChoice(index); proposed_box.addWidget(choice); choice.hide()
            self.order_group.addButton(choice.select)
            choice.select.toggled.connect(lambda checked, i=index: self.select_proposal(i) if checked else None)
            self.order_rows.append(choice)
        proposed_box.addStretch(1); self.order.setWidget(proposed_fields)
        right_box.addWidget(self.order); split.addWidget(right); split.setSizes([535, 535]); box.addWidget(split, 1)
        self.details = QTextEdit(); self.details.setReadOnly(True); self.details.setMaximumHeight(108)
        self.details.setAccessibleName('Draft changes'); self.details.setPlainText('Choose your preferences, then Suggest draft. Automatic energy estimates are optional and can be overridden.')
        box.addWidget(self.details)
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(self.status)
        row = QHBoxLayout()
        self.generate = QPushButton('Suggest draft'); self.generate.setProperty('role', 'primary'); self.generate.clicked.connect(self.suggest)
        self.generate.setToolTip('Build a proposal using these preferences. It does not change the current mix.')
        self.listen = QPushButton('Listen to selected blend'); self.listen.setEnabled(False); self.listen.clicked.connect(self.preview)
        self.listen.setToolTip('Hear the selected song’s incoming blend in the proposed order, before applying the plan.')
        row.addWidget(self.generate); row.addWidget(self.listen); row.addStretch()
        help_button = QPushButton('Help'); help_button.clicked.connect(self.show_help)
        help_button.setToolTip('Learn about energy ratings, position locks, target duration and draft listening. F1 opens the same guide.')
        row.addWidget(help_button)
        self.help_shortcut = QShortcut(QKeySequence('F1'), self)
        self.help_shortcut.activated.connect(self.show_help)
        self.apply = QPushButton('Apply plan'); self.apply.setEnabled(False); self.apply.clicked.connect(self.commit)
        self.apply.setToolTip('Apply the displayed song order, included songs, ratings and position locks as one undoable edit. Read the omitted-song list first.')
        self.back = QPushButton(self.back_label); self.back.clicked.connect(self.reject)
        self.back.setToolTip('Close and discard this plan, its ratings and locks. Nothing is applied.')
        row.addWidget(self.back); row.addWidget(self.apply); box.addLayout(row)
        for widget in self.findChildren(QWidget):
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton):
                widget.setAutoDefault(False); widget.setMinimumHeight(30)
        self.update_energy_labels()

    def update_energy_labels(self):
        info = self.proposal.get('energy', {}) if self.proposal else {}
        for identity, label in self.energy_labels.items():
            value = self.ratings[identity].currentData()
            estimate = info.get(identity, {})
            if value is not None:
                text, tip = 'Your rating', 'Your rating overrides automatic analysis.'
            elif not self.auto_energy.isChecked():
                text, tip = 'Estimate off', 'Energy is unknown unless you choose a personal rating.'
            elif estimate.get('origin') == 'estimated':
                text, tip = f'Estimated {round(estimate["value"])}/5', estimate['reason']
            elif estimate:
                text, tip = 'Unknown', estimate['reason']
            else:
                text, tip = 'After Suggest draft', 'Audio is analyzed locally when you request a draft; unchanged clips reuse cached analysis.'
            label.setText(text); label.setToolTip(tip); label.setAccessibleDescription(tip)

    def show_help(self):
        from studio_help import HelpDialog
        if self.help_window is None:
            self.help_window = HelpDialog(self)
        self.help_window.open_topic('planning')
        self.help_window.show(); self.help_window.raise_()

    def invalidate(self, *_):
        if not hasattr(self, 'apply'):
            return
        self.proposal = None
        self.order_index = -1
        self.apply.setEnabled(False); self.listen.setEnabled(False)
        self.order_group.setExclusive(False)
        for row in self.order_rows:
            row.select.setChecked(False); row.hide()
        self.order_group.setExclusive(True)
        self.update_energy_labels()
        self.status.setText('Preferences changed. Choose Suggest draft to see a new plan.')

    def inputs(self):
        session = copy.deepcopy(self.session)
        for clip in session.clips:
            clip.energy = self.ratings[clip.id].currentData()
            clip.order_locked = self.locks[clip.id].isChecked()
        options = OrderOptions(self.pace.currentData(), self.opener.currentData(), self.closer.currentData(),
                               self.minutes.value() * 60 if self.shorter.isChecked() else None, self.shorter.isChecked(),
                               self.auto_energy.isChecked())
        return session, options

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.controls, self.songs, self.order, self.generate): widget.setEnabled(not busy)
        self.apply.setEnabled(not busy and self.proposal is not None)
        self.back.setText('Stop and close' if busy else self.back_label)
        self.refresh_listen()

    def suggest(self):
        if self.busy: return
        session, options = self.inputs()
        self.set_busy(True); self.status.setText('Finding a valid order…')
        def work(progress, emit):
            options.validate(session)
            energy = estimate_mix_energy(session, self.cache_dir, progress) if options.auto_energy else {}
            return propose_order(session, options, progress, energy_estimates=energy)
        def finished(proposal):
            self.set_busy(False)
            if self.closing_after_job:
                super(OrderDialog, self).reject(); return
            self.proposal = proposal; self.show_proposal(); self.set_busy(False)
        self.cancel = self.start_job(work, finished, cancellable=True, failed_callback=self.failed, progress_callback=self.status.setText)

    def show_proposal(self):
        proposal = self.proposal; draft = proposal['draft']
        self.update_energy_labels()
        old = {c.id: i + 1 for i, c in enumerate(self.session.clips)}
        labels = {'compatible': 'Key labels fit', 'clash': 'Possible key clash', 'unknown': 'Key unknown', 'percussion': 'Marked beats only'}
        for row, clip in enumerate(draft.clips):
            name = draft.tracks[clip.track_id].name
            advice = proposal['transitions'][row - 1] if row else None
            choice = self.order_rows[row]
            choice.name.setText(name)
            info = proposal['energy'][clip.id]
            energy = (f'{"Your energy" if info["origin"] == "manual" else "Estimated energy"} {round(info["value"])}/5'
                      if info['value'] is not None else 'Energy unknown')
            choice.detail.setText(f'From position {old[clip.id]} · {energy}\n' + (labels[advice['state']] if advice else 'Opening song'))
            choice.select.setAccessibleName(f'Select song {row + 1}: {name}')
            choice.select.setToolTip(advice['reason'] if advice else 'First song; no incoming blend.')
            choice.select.setAccessibleDescription(choice.select.toolTip())
            choice.show()
        for row in self.order_rows[len(draft.clips):]: row.hide()
        baseline = 'selected songs' if self.new_mix else 'current mix'
        text = [f'{len(draft.clips)} songs · {clock(proposal["duration"])} ({baseline}: {clock(proposal["current_duration"])}) · {proposal["moved"]} position(s) changed.']
        delta = proposal['target_difference']
        if delta is not None: text.append(f'Target difference: {abs(delta):.1f} sec {"longer" if delta >= 0 else "shorter"}. Whole clips are used; exact duration is not guaranteed.')
        if proposal['omitted']:
            names = [self.session.tracks[next(c for c in self.session.clips if c.id == identity).track_id].name for identity in proposal['omitted']]
            text.append('LEFT OUT OF THE MIX: ' + '; '.join(names) + '. They stay in your library; Undo restores this arrangement.')
        else: text.append('All songs are included.')
        unknown = len(self.session.clips) - proposal['rated'] - proposal['estimated']
        text.append(f'Energy: {proposal["rated"]} personal ratings, {proposal["estimated"]} audio estimates, {unknown} unknown. '
                    'Estimates describe audio activity; listen to judge the feel. Key labels and source tempos also guide the order.')
        text.extend(proposal['label_changes'])
        text.append('Cues, tempo, pitch and overlap lengths stay as set. Listen to the new neighbors before keeping the order.')
        self.details.setPlainText('\n'.join(text))
        self.order_rows[1].select.setChecked(True)
        self.status.setText('Draft ready. Select a song to hear its incoming blend, or apply this plan.')

    def select_proposal(self, index):
        self.order_index = index
        self.refresh_listen()

    def refresh_listen(self, *_):
        row = self.order_index
        self.listen.setEnabled(not self.busy and self.proposal is not None and row > 0)
        self.listen.setText(f'Listen to {row} → {row + 1}' if row > 0 else 'Select an incoming song')

    def preview(self):
        index = self.order_index
        if self.busy or self.proposal is None or index < 1: return
        snapshot = copy.deepcopy(self.proposal['draft'])
        self.set_busy(True); self.status.setText('Preparing this proposed blend…')
        def work(progress, emit): return prepare_blend(snapshot, index, self.cache_dir, progress)
        def finished(report):
            self.set_busy(False)
            if self.closing_after_job:
                super(OrderDialog, self).reject(); return
            try:
                self.audition = OrderAudition(snapshot, index, self.bands, report, self.output, self.volume, self)
                self.audition.finished.connect(lambda _: setattr(self, 'audition', None))
                self.audition.open(); self.audition.player.play()
                self.status.setText('Preview ready. Apply the plan only if you want to keep this order.')
            except Exception as error:
                self.failed(str(error))
        self.cancel = self.start_job(work, finished, rendering=True, cancellable=True, failed_callback=self.failed, progress_callback=self.status.setText)

    def failed(self, message):
        self.set_busy(False)
        if self.closing_after_job: super().reject()
        else: self.status.setText(message)

    def commit(self):
        if not self.busy and self.proposal is not None and self.apply_draft(self.proposal['draft']):
            self.accept()

    def reject(self):
        if self.busy:
            self.closing_after_job = True
            if self.cancel is not None: self.cancel.set()
            self.status.setText('Stopping after the current planning or audio step…')
        else:
            super().reject()
