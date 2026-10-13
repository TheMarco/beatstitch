"""Review a draft overlap with aligned source waveforms and seamless solo listening."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
                              QLabel, QLayout, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget)

import djmix
from studio_engine import overlap_limit
from studio_recommendations import transition_harmony
from studio_transition_audio import BlendTransport, pair_range, prepare_blend
from studio_transition_view import TransitionView
from studio_widgets import clock, display_key
from studio_blend_choices import alternatives, change_summary, sound_settings, DRAFT_FIELDS, prepare_choice_comparison
from studio_overlap_harmony import analyze_overlap, advice_text
from studio_level_balance import report_text as level_report_text
from studio_transition_effects import EFFECTS
from studio_overlap_content import analyze_pair_voices, vocal_cue_choices
from studio_overlap_content_ui import OverlapVoices
from studio_crowding_ui import OverlapCrowding


class TransitionEditor(QDialog):
    def __init__(self, session, index, bands, cache_dir, output_preference, volume, start_job, apply_draft, parent=None, max_shift=2,
                 save_content=None):
        super().__init__(parent)
        self.session, self.index = copy.deepcopy(session), index
        self.baseline = copy.deepcopy(session)
        self.original = copy.deepcopy(session.clips[index])
        self.bands, self.cache_dir = bands, cache_dir
        self.start_job, self.apply_draft = start_job, apply_draft
        self.save_content = save_content or (lambda results: None)
        self.output_preference = output_preference
        self.volume = volume
        self.comparison_dialog = None
        self.help_window = None
        self.busy, self.ready, self.closing_after_job = False, False, False
        self.cancel = None
        self.overlap_harmony = None
        self.setWindowTitle(f'Blend {index} → {index + 1}' + (' · before final mastering' if session.mastering_enabled else ''))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(760, 590)
        self.resize(980, 730)
        self.player = BlendTransport(self)
        self.alignment_mode = False
        self.full_listen_label = 'Listen'
        self.normal_details = []
        self.devices = QMediaDevices(self)
        self.player.set_volume(volume)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 20)
        outer.setSpacing(12)
        self.details_scroll = QScrollArea()
        self.details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.details_scroll.setAccessibleName('Blend details and draft settings')
        details = QWidget()
        box = QVBoxLayout(details)
        box.setContentsMargins(0, 0, 6, 0)
        box.setSpacing(12)
        box.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.details_scroll.setWidget(details)
        outer.addWidget(self.details_scroll, 1)
        title = QLabel(f'Blend {index} → {index + 1}')
        title.setProperty('role', 'title')
        box.addWidget(title)
        harmony = transition_harmony(session, index)
        keys = ' → '.join(display_key(key) or 'Unknown key' for key in harmony['keys'])
        self.advice = QLabel(f'Song key labels: {keys}. {harmony["reason"]}')
        self.advice.setTextFormat(Qt.TextFormat.PlainText)
        self.advice.setWordWrap(True)
        self.advice.setProperty('role', 'secondary')
        box.addWidget(self.advice)
        self.local_advice = QLabel('Actual overlap not yet checked. Listen prepares the blend and checks its local harmony.')
        self.local_advice.setWordWrap(True)
        self.local_advice.setTextFormat(Qt.TextFormat.PlainText)
        self.local_advice.setToolTip('Analyzes the two rendered sides only where they overlap, including the applied pitch, tempo, fades, bass handoff and tonal tails. Estimates can be ambiguous and do not detect competing vocals. It never changes the source key labels or marks a passage as drums only.')
        self.harmony_tooltip = self.local_advice.toolTip()
        if session.mastering_enabled:
            self.harmony_tooltip += ' This editor monitors the decks before final mastering. Use main Listen to blend or Play mix to hear the finished output.'
            self.local_advice.setToolTip(self.harmony_tooltip)
        self.local_advice.setAccessibleDescription(self.harmony_tooltip)
        box.addWidget(self.local_advice)
        self.view = TransitionView()
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.view.setFixedHeight(200)
        box.addWidget(self.view, 1)
        self.view.seekRequested.connect(lambda seconds: self.seek_to(round(float(
            self.tempo_curve.to_output(self.start / djmix.SR + seconds) - self.output_origin) * 1000)))
        from studio_alignment_ui import BeatAlignment
        self.alignment = BeatAlignment(self); box.addWidget(self.alignment)
        self.voices = OverlapVoices(self); box.addWidget(self.voices)
        self.crowding = OverlapCrowding(self); box.addWidget(self.crowding)
        self.choice_options = alternatives(session, index, max_shift)
        self.choice = QComboBox()
        self.choice.setPlaceholderText('Custom draft')
        self.choice.setAccessibleName('Blend alternative to audition')
        self.choice.setToolTip('Try a suggested starting point. It stays a draft until Apply changes. '
                               'Pitch suggestions consider both neighboring blends and your pitch limit.')
        for option in self.choice_options:
            self.choice.addItem(option['label'])
        choice_row = QHBoxLayout(); choice_row.addWidget(QLabel('Try an alternative')); choice_row.addWidget(self.choice, 1)
        box.addLayout(choice_row)
        self.choice_reason = QLabel(); self.choice_reason.setWordWrap(True)
        self.choice_reason.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(self.choice_reason)
        self.settings = QWidget()
        settings_box = QVBoxLayout(self.settings)
        settings_box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel('Overlap'))
        limit = overlap_limit(self.session, index)
        self.length = QSpinBox()
        self.length.setRange(1, limit['bars'])
        self.length.setSuffix(' bars')
        self.length.setValue(self.original.blend_bars)
        self.length.setMinimumWidth(100)
        self.length.setAccessibleName('Draft overlap length in bars')
        self.length.setToolTip(f'One bar is four beats. Maximum {limit["bars"]} bars, limited by {limit["song"]}. '
                               + (f'Keep protected “{limit["protected"]}” clear. ' if limit.get('protected') else '') +
                               'Changes are only used in the mix after Apply changes.')
        row.addWidget(self.length)
        self.seconds = QLabel()
        row.addWidget(self.seconds)
        row.addStretch(1)
        self.bass = QCheckBox('Swap bass at the middle')
        self.bass.setChecked(self.original.bass_swap)
        self.bass.setToolTip('Hand the low frequencies from one song to the other over one bar at the middle. '
                             'Turn off to crossfade bass throughout the overlap. Applies only to this blend.')
        row.addWidget(self.bass)
        settings_box.addLayout(row)
        row = QHBoxLayout()
        self.balance = QCheckBox('Balance levels in this blend')
        self.balance.setChecked(self.original.balance_levels)
        self.balance.setToolTip('Measure each song inside this overlap after tempo and pitch processing. '
                                'Gently lower the louder side by at most 3 dB with smooth ramps. '
                                'Leaves audio outside the overlap unchanged, never boosts quiet passages, '
                                'and does not compress the music. Compare Current / Draft before applying.')
        row.addWidget(self.balance)
        self.level_advice = QLabel(level_report_text([], index, self.original.balance_levels))
        self.level_advice.setWordWrap(True)
        self.level_advice.setTextFormat(Qt.TextFormat.PlainText)
        self.level_advice.setProperty('role', 'secondary')
        row.addWidget(self.level_advice, 1)
        settings_box.addLayout(row)
        box.addWidget(self.settings)
        row = QHBoxLayout(); row.addWidget(QLabel('Outgoing finish'))
        self.effect = QComboBox(); self.effect.setAccessibleName('Outgoing transition effect')
        self.effect.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for kind, (title, _) in EFFECTS.items(): self.effect.addItem(title, kind)
        self.effect.setCurrentIndex(self.effect.findData(self.original.transition_effect))
        self.effect.setToolTip('Optional effect on the outgoing song inside this overlap. Nothing changes until Apply. '
                               'Compare current / draft to hear the choice at matched levels. Protected passages stay outside effects.')
        row.addWidget(self.effect)
        self.effect_hint = QLabel(EFFECTS[self.original.transition_effect][1]); self.effect_hint.setWordWrap(True)
        self.effect_hint.setProperty('role', 'secondary'); row.addWidget(self.effect_hint, 1)
        settings_box.addLayout(row)
        self.length.valueChanged.connect(self.change_draft)
        self.bass.toggled.connect(self.change_draft)
        self.balance.toggled.connect(self.change_draft)
        self.effect.currentIndexChanged.connect(self.change_draft)
        self.view.overlapChanged.connect(self.length.setValue)
        explanation = QLabel('The grid follows your cues and project BPM; it does not prove the beats or phrases are correct. '
                             'Preview includes up to four bars either side, stopping before other blends. '
                             'Click a waveform to seek; arrow keys move a beat, Shift moves a bar.')
        explanation.setWordWrap(True)
        explanation.setProperty('role', 'hint')
        box.addWidget(explanation)
        # Playback and commit controls stay reachable when long explanations need scrolling.
        box = outer
        row = QHBoxLayout()
        self.hear_label = QLabel('Hear'); row.addWidget(self.hear_label)
        self.variants = QButtonGroup(self)
        for text, variant in [('Outgoing', 'outgoing'), ('Blend', 'blend'), ('Incoming', 'incoming')]:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(variant == 'blend')
            button.setMinimumHeight(32)
            button.setToolTip('Switch without restarting. Solo keeps this song’s mix volume and fades; silence outside '
                              'its part of the preview is expected. Nothing here changes the exported mix.')
            button.toggled.connect(lambda checked, v=variant: self.player.select(v) if checked else None)
            self.variants.addButton(button)
            row.addWidget(button)
        row.addStretch(1)
        self.more_blend_controls = QPushButton('Show all blend controls')
        self.more_blend_controls.setToolTip('Return to the full blend editor, keeping this timing draft.')
        self.more_blend_controls.clicked.connect(self.show_all_controls)
        self.more_blend_controls.hide(); row.addWidget(self.more_blend_controls)
        self.loop = QCheckBox('Loop')
        self.loop.setChecked(True)
        self.loop.setToolTip('Repeat this two-song preview on the same audio clock. No effect on export.')
        self.loop.toggled.connect(self.player.set_loop)
        self.player.set_loop(True)
        row.addWidget(self.loop)
        box.addLayout(row)
        row = QHBoxLayout()
        self.play = QPushButton('Listen')
        self.play.setMinimumHeight(32)
        self.play.setToolTip('Prepare this draft if needed, then play. Unchanged blends reuse cached audio.')
        self.play.clicked.connect(self.toggle)
        row.addWidget(self.play)
        self.compare = QPushButton('Compare current / draft')
        self.compare.setToolTip('Prepare a level-matched A/B comparison. Switch versions while playback keeps running. '
                                'Your mix stays unchanged until you apply the draft.')
        self.compare.clicked.connect(self.compare_draft)
        row.addWidget(self.compare)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName('Blend playback position')
        self.seek.setSingleStep(1000); self.seek.setPageStep(5000)
        self.seek.setToolTip('Seek within this blend. Left/Right moves one second; Page Up/Down moves five seconds; Home/End goes to either end. Outgoing, Blend and Incoming share the same position. Seeking keeps paused audio paused.')
        self.seek.sliderMoved.connect(self.seek_to)
        # Keyboard changes commit immediately; programmatic position updates block signals.
        self.seek.valueChanged.connect(lambda ms: self.seek_to(ms) if not self.seek.isSliderDown() else None)
        row.addWidget(self.seek, 1)
        self.time = QLabel()
        self.time.setMinimumWidth(90)
        row.addWidget(self.time)
        box.addLayout(row)
        self.output = QLabel()
        self.output.setProperty('role', 'secondary')
        self.output.setWordWrap(True)
        box.addWidget(self.output)
        self.status = QLabel('Listen to prepare this pair. Changes stay in this window until you apply them.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close | QDialogButtonBox.StandardButton.Help)
        self.buttons.helpRequested.connect(self.show_help)
        self.buttons.button(QDialogButtonBox.StandardButton.Help).setToolTip('Explain overlap, bass handling, levels, filter/echo and comparing drafts in the offline guide.')
        self.help_shortcut = QShortcut(QKeySequence('F1'), self)
        self.help_shortcut.activated.connect(self.show_help)
        self.apply = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply.setText('Apply changes')
        self.apply.setToolTip('Use this overlap length, bass handling, outgoing effect, level balance, incoming pitch and incoming Start in the mix as one undoable change.')
        self.apply.clicked.connect(self.apply_changes)
        self.close_button = self.buttons.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.setToolTip('Close without applying this draft. The mix stays as it was.')
        self.buttons.rejected.connect(self.reject)
        box.addWidget(self.buttons)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playingChanged.connect(lambda playing: self.play.setText('Pause' if playing else self.full_listen_label))
        self.player.errorOccurred.connect(self.status.setText)
        self.devices.audioOutputsChanged.connect(self.refresh_output)
        self.finished.connect(lambda _: self.player.close())
        self.choice.currentIndexChanged.connect(self.choose_alternative)
        self.refresh_output()
        self.refresh_draft()
        for widget in self.findChildren(QWidget):
            if widget.toolTip():
                widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton):
                widget.setAutoDefault(False)

    def show_help(self):
        from studio_help import HelpDialog
        if self.help_window is None: self.help_window = HelpDialog(self)
        self.help_window.open_topic('beat-alignment' if self.alignment_mode else 'transitions'); self.help_window.show()
        self.help_window.raise_(); self.help_window.activateWindow()

    def focus_alignment(self):
        """The direct entry point shows one task, with full-blend listening retained."""
        if self.alignment_mode: return
        self.alignment_mode = True
        def hide_others(layout):
            for index in range(layout.count()):
                item = layout.itemAt(index)
                widget = item.widget()
                if widget is not None and widget is not self.alignment:
                    self.normal_details.append((widget, not widget.isHidden()))
                    widget.hide()
                elif item.layout() is not None:
                    hide_others(item.layout())
        hide_others(self.details_scroll.widget().layout())
        self.setWindowTitle(f'Align beats {self.index} → {self.index + 1}')
        self.full_listen_label = 'Listen to full blend'
        self.hear_label.setText('Full blend:')
        self.play.setText('Pause' if self.player.playing else self.full_listen_label)
        self.more_blend_controls.show()

    def show_all_controls(self):
        for widget, visible in self.normal_details: widget.setVisible(visible)
        self.normal_details.clear(); self.alignment_mode = False
        self.more_blend_controls.hide()
        self.full_listen_label = 'Listen'; self.play.setText('Pause' if self.player.playing else 'Listen')
        self.hear_label.setText('Hear')
        self.setWindowTitle(f'Blend {self.index} → {self.index + 1}')

    def refresh_output(self):
        device = QMediaDevices.defaultAudioOutput() if self.output_preference == 'system' else next(
            (d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == self.output_preference), None)
        self.player.set_device(device)
        self.alignment.player.set_device(device)
        available = self.player.sink is not None
        self.output.setText('Output: ' + device.description() if available else
                            'Output unavailable. Reconnect it, or close this window and choose another output.')
        self.play.setEnabled(available and not self.busy)

    def refresh_draft(self):
        self.start, self.end = pair_range(self.session, self.index)
        limit = overlap_limit(self.session, self.index)
        self.length.blockSignals(True); self.length.setMaximum(limit['bars']); self.length.blockSignals(False)
        self.length.setToolTip(f'One bar is four beats. Maximum {limit["bars"]} bars, limited by {limit["song"]}. '
                               + (f'Keep protected “{limit["protected"]}” clear. ' if limit.get('protected') else '') +
                               'Changes are only used in the mix after Apply changes.')
        self.length.setAccessibleDescription(self.length.toolTip())
        self.voices.refresh()
        self.crowding.refresh()
        self.alignment.refresh()
        self.view.set_data(self.session, self.index, self.bands, self.start / djmix.SR, self.end / djmix.SR)
        self.seek.blockSignals(True)
        from studio_tempo_curve import session_curve, render_span
        self.tempo_curve = session_curve(self.session)
        self.output_origin = int(self.tempo_curve.output_frame(self.start)) / djmix.SR
        out_start, out_end = render_span(self.session, (self.start, self.end))
        self.seek.setRange(0, round((out_end - out_start) * 1000 / djmix.SR))
        self.seek.setValue(0)
        self.seek.blockSignals(False)
        self.position_changed(0)
        from studio_engine import layout
        pos = layout(self.session)[self.index]
        a, b = render_span(self.session, (pos['start'], pos['start'] + pos['incoming']))
        suffix = 'following the tempo curve' if self.session.tempo_enabled else f'at {self.session.target_bpm:g} BPM'
        self.seconds.setText(f'{(b - a) / djmix.SR:.1f} seconds {suffix}')
        changed = self.session.clips[self.index] != self.original
        self.apply.setEnabled(changed and not self.busy)
        different_sound = sound_settings(self.session.clips[self.index]) != sound_settings(self.original)
        self.compare.setEnabled(different_sound and not self.busy)
        harmony = transition_harmony(self.session, self.index)
        keys = ' → '.join(display_key(key) or 'Unknown key' for key in harmony['keys'])
        self.advice.setText(f'Song key labels: {keys}. {harmony["reason"]}')
        selected = next((i for i, option in enumerate(self.choice_options)
                         if sound_settings(option['clip']) == sound_settings(self.session.clips[self.index])), -1)
        self.choice.blockSignals(True); self.choice.setCurrentIndex(selected); self.choice.blockSignals(False)
        reason = self.choice_options[selected]['reason'] if selected >= 0 else 'Custom draft. Compare it with the current mix before applying.'
        self.choice_reason.setText(reason + '\n' + change_summary(self.baseline, self.index, self.session.clips[self.index]))
        self.close_button.setText('Discard draft' if changed else 'Close')

    def choose_alternative(self, index):
        if index < 0 or self.busy:
            return
        chosen = self.choice_options[index]['clip']
        clip = self.session.clips[self.index]
        for name in DRAFT_FIELDS:
            setattr(clip, name, getattr(chosen, name))
        self.length.blockSignals(True); self.length.setValue(chosen.blend_bars); self.length.blockSignals(False)
        self.bass.blockSignals(True); self.bass.setChecked(chosen.bass_swap); self.bass.blockSignals(False)
        self.balance.blockSignals(True); self.balance.setChecked(chosen.balance_levels); self.balance.blockSignals(False)
        self.effect.blockSignals(True); self.effect.setCurrentIndex(self.effect.findData(chosen.transition_effect)); self.effect.blockSignals(False)
        self.change_draft()

    def change_draft(self, *_):
        clip = self.session.clips[self.index]
        clip.blend_bars, clip.bass_swap = self.length.value(), self.bass.isChecked()
        clip.balance_levels = self.balance.isChecked()
        clip.transition_effect = self.effect.currentData()
        self.effect_hint.setText(EFFECTS[clip.transition_effect][1])
        self.level_advice.setText(level_report_text([], self.index, clip.balance_levels))
        self.player.close()
        self.ready = False
        self.overlap_harmony = None
        self.crowding.clear()
        self.local_advice.setText('Overlap changed. Listen again to update its local harmony check.')
        self.local_advice.setToolTip(self.harmony_tooltip)
        self.local_advice.setAccessibleDescription(self.harmony_tooltip)
        self.refresh_draft()
        self.status.setText('Draft changed. Listen to hear it, then Apply changes to keep it.')

    def position_changed(self, milliseconds):
        if not self.seek.isSliderDown():
            self.seek.blockSignals(True)
            self.seek.setValue(milliseconds)
            self.seek.blockSignals(False)
        self.time.setText(f'{clock(milliseconds / 1000)} / {clock(self.seek.maximum() / 1000)}')
        self.view.set_playhead(float(self.tempo_curve.to_reference(self.output_origin + milliseconds / 1000)) - self.start / djmix.SR)

    def seek_to(self, milliseconds):
        if self.busy:
            return
        if self.ready:
            self.player.seek(milliseconds)
        self.position_changed(milliseconds)

    def set_busy(self, busy):
        self.busy = busy
        self.voices.setEnabled(not busy)
        self.crowding.setEnabled(not busy)
        self.alignment.setEnabled(not busy)
        self.settings.setEnabled(not busy)
        self.choice.setEnabled(not busy)
        self.view.setEnabled(not busy)
        self.seek.setEnabled(not busy)
        self.apply.setEnabled(not busy and self.session.clips[self.index] != self.original)
        self.compare.setEnabled(not busy and sound_settings(self.session.clips[self.index]) != sound_settings(self.original))
        self.play.setEnabled(not busy and self.player.sink is not None)
        self.close_button.setText('Stop and close' if busy else
                                  'Discard draft' if self.session.clips[self.index] != self.original else 'Close')

    def toggle(self):
        if self.busy:
            return
        self.alignment.player.pause()
        if self.ready:
            self.player.pause() if self.player.playing else self.player.play()
            return
        snapshot = copy.deepcopy(self.session)
        position = self.seek.value()
        self.set_busy(True)
        self.status.setText('Preparing both songs and their blend…')

        def work(progress, emit):
            report = prepare_blend(snapshot, self.index, self.cache_dir, progress)
            try:
                report['harmony'] = analyze_overlap(snapshot, self.index, report, self.cache_dir, progress)
            except Exception as error:
                if str(error) == 'Operation cancelled.':
                    raise
                report['harmony_error'] = str(error)
            try:
                report['voice_content'] = analyze_pair_voices(snapshot, self.index, self.cache_dir.parent / 'structure-cache', progress)
            except Exception as error:
                if str(error) == 'Operation cancelled.':
                    raise
                report['voice_error'] = str(error)
            return report

        def finished(report):
            self.set_busy(False)
            if self.closing_after_job:
                super(TransitionEditor, self).reject()
                return
            try:
                self.receive_voice_content(report.get('voice_content', {}))
                if report.get('voice_error'):
                    self.voices.summary.setText('Voices could not be fully checked. ' + report['voice_error'] + ' Solo each side and listen.')
                self.level_advice.setText(level_report_text(report.get('level_balance', []), self.index,
                                                            snapshot.clips[self.index].balance_levels))
                self.overlap_harmony = report.get('harmony')
                if self.overlap_harmony:
                    self.local_advice.setText(advice_text(self.overlap_harmony, snapshot, self.index))
                    detail = '\n'.join(name.capitalize() + ': ' + self.overlap_harmony[name]['reason']
                                       for name in ('outgoing', 'incoming'))
                else:
                    self.local_advice.setText('Overlap harmony could not be checked. Listen to judge it; song labels remain estimates.')
                    detail = report.get('harmony_error', 'No local analysis result. Close and reopen this editor to retry.')
                self.local_advice.setToolTip(self.harmony_tooltip + '\n\n' + detail)
                self.local_advice.setAccessibleDescription(self.local_advice.toolTip())
                self.player.load(report['folder'])
                self.ready = True
                self.player.seek(position)
                self.refresh_output()
                self.status.setText('Ready. Switch Outgoing / Blend / Incoming while it plays. Solo keeps the mix fades.')
                self.player.play()
            except Exception as error:
                self.failed(str(error))

        self.cancel = self.start_job(work, finished, rendering=True, cancellable=True,
                                     failed_callback=self.failed, progress_callback=self.status.setText)

    def receive_voice_content(self, results):
        if results:
            self.save_content(results)
            for identity, result in results.items():
                self.session.tracks[identity].content = copy.deepcopy(result)
                self.baseline.tracks[identity].content = copy.deepcopy(result)
        self.voices.refresh()

    def check_voices(self):
        if self.busy:
            return
        snapshot = copy.deepcopy(self.session)
        self.player.pause(); self.set_busy(True)
        self.status.setText('Checking both songs for possible overlapping voices…')
        def work(progress, emit):
            return analyze_pair_voices(snapshot, self.index, self.cache_dir.parent / 'structure-cache', progress)
        def finished(results):
            self.set_busy(False)
            if self.closing_after_job:
                super(TransitionEditor, self).reject(); return
            try:
                self.receive_voice_content(results)
                self.status.setText('Voice observations ready. Listen to both sides; cue suggestions remain optional drafts.')
            except Exception as error:
                self.failed(str(error))
        self.cancel = self.start_job(work, finished, rendering=True, cancellable=True,
                                     failed_callback=self.failed, progress_callback=self.status.setText)

    def try_voice_cue(self, candidate):
        if self.busy:
            return
        if candidate not in vocal_cue_choices(self.session, self.index):
            self.status.setText('That cue no longer fits the current observations or draft. Check voices again or reopen this blend.')
            return
        clip = self.session.clips[self.index]
        clip.cue_in = candidate['time']; clip.intro_percussive = False
        self.change_draft()
        self.status.setText('Incoming Start changed in the draft; drums-only intro label cleared. Compare current / draft before applying.')

    def compare_draft(self):
        if self.busy or sound_settings(self.session.clips[self.index]) == sound_settings(self.original):
            return
        self.alignment.player.pause()
        from studio_blend_comparison import BlendComparison
        snapshot = copy.deepcopy(self.session)
        self.player.close(); self.ready = False
        self.set_busy(True)
        self.status.setText('Preparing a matched Current / Draft comparison…')

        def work(progress, emit):
            return prepare_choice_comparison(self.baseline, self.index, snapshot.clips[self.index], self.cache_dir, progress)

        def finished(report):
            self.set_busy(False)
            if self.closing_after_job:
                super(TransitionEditor, self).reject()
                return
            try:
                self.level_advice.setText(level_report_text(report.get('draft_level_balance', []), self.index,
                                                            snapshot.clips[self.index].balance_levels))
                self.comparison_dialog = BlendComparison(self.baseline, snapshot, self.index, self.bands, report,
                                                        self.output_preference, self.volume, self.apply_draft, self)
                self.comparison_dialog.accepted.connect(self.accept)
                self.comparison_dialog.finished.connect(lambda _: setattr(self, 'comparison_dialog', None))
                self.comparison_dialog.open()
                self.comparison_dialog.player.play()
            except Exception as error:
                self.failed(str(error))

        self.cancel = self.start_job(work, finished, rendering=True, cancellable=True,
                                     failed_callback=self.failed, progress_callback=self.status.setText)

    def failed(self, message):
        self.set_busy(False)
        self.ready = False
        if self.closing_after_job:
            super().reject()
        else:
            self.status.setText(message)

    def apply_changes(self):
        self.alignment.player.pause()
        if not self.busy and self.apply_draft(self.session.clips[self.index]):
            self.accept()

    def reject(self):
        if self.busy:
            self.closing_after_job = True
            if self.cancel is not None:
                self.cancel.set()
            self.status.setText('Stopping after the current audio step…')
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
