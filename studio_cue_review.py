"""Native review of detected bar starts, section changes and optional cue edits."""
from __future__ import annotations

import copy

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout, QLabel,
                              QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

import djmix
import studio_theme as T
from studio_engine import layout
from studio_beat_controls import BeatControls, GridAudition
from studio_beat_grid import display_grid, grid_settings, manual_settings
from studio_structure import analyze_song, current_analysis, current_tempo_estimate, current_opening_tempo
from studio_widgets import CueEditor, clock


class CueReviewWave(CueEditor):
    seekRequested = Signal(float)
    ACCESSIBLE_LABEL = 'Song waveform and draft cues'
    KEYBOARD_HELP = ('Left/Right seeks one beat; Shift seeks one bar. Home/End seeks the source boundaries. '
                     'Up selects Start; Down selects End. Alt/Option+Left/Right moves that marker by 10 milliseconds. '
                     'Use the Start and End number boxes for larger cue edits. Apply changes keeps the draft in the mix. '
                     '+/− zooms; 0 fits the song. Tab moves to the next control.')

    def __init__(self, bands, parent=None):
        super().__init__(bands, parent)
        self.cursor = 0.
        self.setMinimumHeight(150)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleDescription(self.KEYBOARD_HELP)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.track:
            return
        painter = QPainter(self)
        x = self.x_of(self.cursor)
        painter.setPen(QPen(QColor(T.TEXT), 1))
        painter.drawLine(QPointF(x, self.RULER), QPointF(x, self.height()))
        for boundary in current_analysis(self.track).get('boundaries', []):
            x = self.x_of(boundary['time'])
            if 0 <= x <= self.width():
                painter.setPen(QPen(T.color(T.GOOD, .65), 1, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(x, self.RULER), QPointF(x, self.height()))
        from studio_song_sections import current_sections, LABELS
        from studio_song_sections_ui import COLORS
        # A narrow annotation strip leaves the real waveform visible.
        for region in current_sections(self.track).get('regions', []):
            left, right = max(0, self.x_of(region['start'])), min(self.width(), self.x_of(region['end']))
            if right <= left: continue
            painter.fillRect(round(left), self.height() - 20, round(right - left), 20, T.color(COLORS[region['label']], .30))
            if right - left > 65:
                painter.setPen(QColor(T.TEXT))
                painter.drawText(QPointF(left + 4, self.height() - 5), LABELS[region['label']] + '?')
        manual = manual_settings(self.track, check_source=True)
        if manual:
            x = self.x_of(manual['anchor'])
            if 0 <= x <= self.width():
                painter.setPen(QPen(QColor('#5CCBEA'), 2))
                painter.drawLine(QPointF(x, self.RULER), QPointF(x, self.height()))
                painter.drawText(QPointF(min(x + 5, self.width() - 55), self.RULER + 16), 'Beat 1')

    def mousePressEvent(self, event):
        self.setFocus()
        if (event.button() == Qt.MouseButton.LeftButton and self.track and
                not self._handle_at(event.position().x()) and event.position().y() >= self.RULER):
            self.seekRequested.emit(self.time_at(event.position().x()))
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if (self.isEnabled() and self.track and not self.dragging
                and not (event.modifiers() & ~Qt.KeyboardModifier.ShiftModifier)
                and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End)):
            step = (240 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 60) / self.track.bpm
            seconds = self.cursor + step * (-1 if event.key() == Qt.Key.Key_Left else 1)
            if event.key() == Qt.Key.Key_Home:
                seconds = 0
            elif event.key() == Qt.Key.Key_End:
                seconds = self.track.duration
            self.seekRequested.emit(max(0, min(seconds, self.track.duration)))
            event.accept()
            return
        super().keyPressEvent(event)


class CueReview(QDialog):
    def __init__(self, session, index, bands, cache_dir, output_preference, volume,
                 start_job, save_analysis, apply_cues, parent=None, *, protect=False, save_content=None, save_sections=None):
        super().__init__(parent)
        self.session, self.index = copy.deepcopy(session), index
        self.clip = self.session.clips[index]
        self.track = self.session.tracks[self.clip.track_id]
        if not current_analysis(self.track, check_source=True):
            self.track.structure = {}
        self.original = self.clip.cue_in, self.clip.cue_out
        self.original_drums = self.clip.intro_percussive, self.clip.outro_percussive
        self.original_protection = copy.deepcopy(self.clip.protected_moments)
        self.original_overlaps = {c.id: c.blend_bars for c in self.session.clips}
        self.original_bpm, self.original_grid = self.track.bpm, copy.deepcopy(self.track.beat_grid)
        current_grid = display_grid(self.track)
        self.anchor = manual_settings(self.track, check_source=True).get('anchor', next(iter(current_grid.get('downbeats', [])), self.clip.cue_in))
        self.cache_dir, self.output_preference = cache_dir, output_preference
        self.start_job, self.save_analysis, self.apply_cues = start_job, save_analysis, apply_cues
        self.save_content = save_content or (lambda result: None)
        self.save_sections = save_sections or (lambda result: None)
        self.busy, self.closing_after_job, self.cancel = False, False, None
        self.setWindowTitle('Find musical cues')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(820, 650)
        self.resize(940, 760)
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 20, 20, 20)
        box.setSpacing(10)
        heading = QLabel(self.track.name)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        heading.setProperty('role', 'title')
        box.addWidget(heading)
        row = QHBoxLayout()
        self.evidence = QLabel()
        self.evidence.setWordWrap(True)
        row.addWidget(self.evidence, 1)
        self.analyze_button = QPushButton('Analyze song')
        self.analyze_button.setToolTip('Find beats, bar starts and changes in energy or texture locally. Reuses unchanged analysis. '
                                       'It never changes your source BPM, pitch or cue points.')
        self.analyze_button.clicked.connect(self.analyze)
        row.addWidget(self.analyze_button)
        box.addLayout(row)
        self.wave = CueReviewWave(bands)
        self.wave.setToolTip('Real source waveform. Orange ruler marks are supported detected bar starts; green dashed lines '
                             'are estimated section changes. Drag Start/End to try cues; Alt/Option bypasses snapping. '
                             'Before analysis, the ruler is only a guide based on your current cue and BPM. '
                             + CueReviewWave.KEYBOARD_HELP)
        self.wave.cuesChanged.connect(self.change_cues)
        box.addWidget(self.wave, 1)
        row = QHBoxLayout()
        self.start, self.end = QDoubleSpinBox(), QDoubleSpinBox()
        for field, text in ((self.start, 'Start'), (self.end, 'End')):
            field.setRange(0, self.track.duration)
            field.setDecimals(3)
            field.setSuffix(' s')
            field.setSingleStep(240 / self.track.bpm)
            field.setMinimumWidth(115)
            field.setAccessibleName(f'Draft {text.lower()} in source seconds')
            field.setToolTip('Source seconds before tempo changes. Only this clip changes when you Apply changes. '
                             'You can also drag the orange waveform marker.')
            row.addWidget(QLabel(text))
            row.addWidget(field)
        self.start.valueChanged.connect(lambda value: self.change_cues(value, self.clip.cue_out))
        self.end.valueChanged.connect(lambda value: self.change_cues(self.clip.cue_in, value))
        row.addStretch(1)
        for text, callback in [('Start cue', self.wave.focus_start), ('End cue', self.wave.focus_end),
                               ('−', self.wave.zoom_out), ('+', self.wave.zoom_in), ('Fit', self.wave.fit)]:
            button = QPushButton(text)
            button.setToolTip({'−': 'Zoom out', '+': 'Zoom in', 'Fit': 'Show the whole song'}.get(text, f'Zoom to the {text.lower()}'))
            button.clicked.connect(lambda checked=False, f=callback: f())
            row.addWidget(button)
        box.addLayout(row)
        self.audition = GridAudition(self.track)
        self.audition.player.set_volume(volume)
        self.audition.player.positionChanged.connect(self.move_cursor)
        self.audition.prepareRequested.connect(self.prepare_source)
        self.wave.seekRequested.connect(lambda seconds: self.audition.seek_to(round(seconds * 1000)))
        box.addWidget(self.audition)
        self.output = QLabel()
        self.output.setProperty('role', 'secondary')
        box.addWidget(self.output)
        self.choices = QScrollArea()
        self.choices.setWidgetResizable(True)
        self.choices.setMinimumHeight(150)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.choices, 'Cue suggestions')
        self.beat_controls = BeatControls(self.track.duration)
        self.beat_controls.bpmChanged.connect(lambda bpm: self.change_grid(bpm=bpm))
        self.beat_controls.estimateRequested.connect(self.try_tempo_estimate)
        self.beat_controls.openingRequested.connect(self.try_opening_estimate)
        self.beat_controls.anchorChanged.connect(lambda anchor: self.change_grid(anchor=anchor))
        self.beat_controls.manualChanged.connect(lambda manual: self.change_grid(manual=manual))
        self.beat_controls.markRequested.connect(self.mark_beat)
        self.tabs.addTab(self.beat_controls, 'Check beats and BPM')
        from studio_protection_ui import ProtectedPassages
        self.protection = ProtectedPassages(self)
        self.tabs.addTab(self.protection, 'Protect passages')
        from studio_passages_ui import PassageSuggestions
        self.passages = PassageSuggestions(self)
        self.tabs.addTab(self.passages, 'Drum-led passages')
        from studio_song_sections_ui import SongSections
        self.sections = SongSections(self)
        self.tabs.addTab(self.sections, 'Builds and drops')
        if protect: self.tabs.setCurrentWidget(self.protection)
        box.addWidget(self.tabs, 1)
        self.status = QLabel('Changes stay in this window until you apply them.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        self.audition.failed.connect(self.status.setText)
        footer = QLabel('Listen plays the original song. Try a cue, apply it if useful, then listen to the actual blend. '
                        'Detections can be wrong. Analyze song finds bars and changes in sound; Builds and drops offers tentative musical labels. Listen to confirm.')
        footer.setWordWrap(True)
        footer.setProperty('role', 'hint')
        box.addWidget(footer)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close)
        self.apply = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply.setText('Apply changes')
        self.apply.setToolTip('Apply this draft as one undoable edit. Start/End affect this clip; source BPM and beat grid affect all uses of this source song.')
        self.apply.clicked.connect(self.accept_cues)
        self.close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.setToolTip('Close without applying draft cues. Computed analysis stays available for reuse.')
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.devices = QMediaDevices(self)
        self.devices.audioOutputsChanged.connect(self.refresh_output)
        self.finished.connect(lambda _: self.audition.close_audio())
        self.refresh_draft()
        self.show_analysis()
        self.refresh_output()
        for widget in self.findChildren(QWidget):
            if widget.toolTip():
                widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton):
                widget.setAutoDefault(False)
        self.auto_analyze = QTimer(self)
        self.auto_analyze.setSingleShot(True)
        self.auto_analyze.timeout.connect(self.analyze)
        if not self.track.structure and not protect:
            self.auto_analyze.start(0)

    def refresh_output(self):
        chosen = QMediaDevices.defaultAudioOutput() if self.output_preference == 'system' else next(
            (d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == self.output_preference), None)
        available = chosen is not None and not chosen.isNull()
        self.output_available = available
        self.audition.setEnabled(available and not self.busy)
        for button in self.choices.findChildren(QPushButton, 'listenCue'):
            button.setEnabled(available)
        for button in self.passages.findChildren(QPushButton, 'listenPassage'):
            button.setEnabled(available)
        if available:
            self.audition.player.set_device(chosen)
            self.output.setText('Output: ' + chosen.description())
        else:
            self.audition.stop()
            self.audition.player.set_device(None)
            self.output.setText('Output unavailable. Reconnect it, or close and choose another output.')
        self.protection.refresh()
        self.sections.refresh()

    def move_cursor(self, ms):
        self.wave.cursor = ms / 1000
        self.wave.update()

    def refresh_draft(self):
        for field, value in ((self.start, self.clip.cue_in), (self.end, self.clip.cue_out)):
            field.blockSignals(True)
            field.setValue(value)
            field.blockSignals(False)
        next_bars = self.session.clips[self.index + 1].blend_bars if self.index + 1 < len(self.session.clips) else 0
        self.wave.set_clip(self.track, self.clip, self.clip.blend_bars if self.index else 0, next_bars)
        changed = ((self.clip.cue_in, self.clip.cue_out) != self.original or
                   (self.clip.intro_percussive, self.clip.outro_percussive) != self.original_drums or
                   self.track.bpm != self.original_bpm or self.track.beat_grid != self.original_grid or
                   self.clip.protected_moments != self.original_protection or
                   {c.id: c.blend_bars for c in self.session.clips} != self.original_overlaps)
        valid = True
        try: layout(self.session)
        except djmix.MixError as error:
            valid = False
            self.status.setText(str(error) + ' The mix has not changed. Use Protect passages → Shorten blends to fit, or adjust the draft cues.')
        self.apply.setEnabled(changed and not self.busy and valid)
        self.close_button.setText('Stop and close' if self.busy else 'Discard draft' if changed else 'Close')
        self.beat_controls.sync(self.track.bpm, self.anchor, bool(manual_settings(self.track, check_source=True)))
        self.beat_controls.show_tempo_estimate(current_tempo_estimate(self.track), self.track.bpm,
                                               current_opening_tempo(self.track))
        self.audition.set_grid(display_grid(self.track))
        self.protection.refresh()
        self.passages.refresh()
        self.sections.refresh()

    def change_cues(self, start, end):
        previous = self.clip.cue_in, self.clip.cue_out
        self.clip.cue_in, self.clip.cue_out = float(start), float(end)
        try:
            layout(self.session, check_protection=False)
        except djmix.MixError as error:
            self.clip.cue_in, self.clip.cue_out = previous
            self.status.setText(f'{error} Keep enough audio for this song’s incoming and outgoing overlaps.')
        else:
            self.status.setText(f'Draft: {clock(start, True)} to {clock(end, True)}. Apply changes to keep it; then preview the blend.')
        self.refresh_draft()

    def change_grid(self, bpm=None, anchor=None, manual=True):
        previous = self.track.bpm, copy.deepcopy(self.track.beat_grid), self.anchor
        try:
            if bpm is not None:
                self.track.bpm = float(bpm)
            if anchor is not None:
                self.anchor = float(anchor)
            self.track.beat_grid = grid_settings(self.track, self.track.bpm, self.anchor) if manual else {}
            layout(self.session, check_protection=False)
        except (djmix.MixError, ValueError, OSError) as error:
            self.track.bpm, self.track.beat_grid, self.anchor = previous
            self.status.setText(f'{error} Draft not changed. Keep enough audio for every affected overlap.')
        else:
            self.status.setText('Draft updated. Listen with Click at several places. The original audio keeps its speed until you apply and preview the mix.')
        self.show_analysis()
        self.refresh_draft()

    def mark_beat(self):
        self.audition.stop()
        self.change_grid(anchor=min(self.wave.cursor, self.track.duration - .001))

    def try_tempo_estimate(self):
        if self.busy:
            return
        estimate = current_tempo_estimate(self.track)
        if estimate.get('bpm') is None:
            self.status.setText('This beat estimate is no longer current. Analyze the song again; your mix has not changed.')
            self.refresh_draft()
            return
        self.change_grid(bpm=estimate['bpm'])

    def try_opening_estimate(self):
        if self.busy:
            return
        estimate = current_opening_tempo(self.track)
        if not estimate:
            self.status.setText('This opening estimate is no longer current. Reanalyze the source; your mix has not changed.')
            self.refresh_draft()
            return
        self.change_grid(bpm=estimate['bpm'])

    def show_analysis(self):
        result = current_analysis(self.track)
        estimate = current_tempo_estimate(self.track).get('bpm')
        opening = current_opening_tempo(self.track).get('bpm')
        if not result:
            text = 'Find bar starts and section changes, then listen before choosing cues.'
        elif result.get('tempo_relation') == 'half-double':
            text = 'Detected pulse may be half or double the source BPM. Open Check beats and BPM to compare the estimates before using bar-based cues.'
        elif estimate is not None and opening is not None and abs(estimate / opening - 1) > .02:
            text = (f'Tempo estimates differ: beat {estimate:.2f} and opening passage {opening:.2f} BPM. '
                    'Open Check beats and BPM to compare them before deciding.')
        elif estimate is not None and abs(estimate / self.track.bpm - 1) > .02:
            text = (f'Tempo estimates differ: {self.track.bpm:g} and {estimate:.2f} BPM. '
                    'Open Check beats and BPM to compare them. Your saved BPM has not changed.')
        elif not result.get('bars'):
            text = 'No sufficiently consistent four-beat bars found. Keep your cues, or check the source BPM and listen manually.'
        else:
            text = f"Consistent four-beat bars cover {result['coverage']:.0%} of the song. "
            text += ('Section changes suggest an eight-bar phrase pattern. Listen to confirm.' if result.get('phrase') else
                     'Phrase pattern is inconclusive; section changes are still available to audition.')
        if manual_settings(self.track, check_source=True):
            text = (f'Manual regular grid · {self.track.bpm:g} BPM · beat 1 at {self.anchor:.3f} s. '
                    'A listening reference; drifting audio stays as it is. Detected cue suggestions may not align with this grid.')
        self.evidence.setText(text)
        content = QWidget()
        rows = QVBoxLayout(content)
        rows.setContentsMargins(10, 6, 10, 6)
        for cue in result.get('cues', []):
            row = QHBoxLayout()
            text = QLabel(f"{cue['kind'].title()} · {clock(cue['time'], True)} · {cue['label']}\n{cue['reason']}")
            text.setWordWrap(True)
            text.setTextFormat(Qt.TextFormat.PlainText)
            row.addWidget(text, 1)
            listen = QPushButton('Listen here')
            listen.setObjectName('listenCue')
            listen.setEnabled(getattr(self, 'output_available', False))
            listen.setToolTip('Hear the original song from four beats before this point, without changing any cues.')
            listen.setAutoDefault(False)
            listen.clicked.connect(lambda checked=False, c=cue: self.audition.listen_at(max(0, c['time'] - 240 / self.track.bpm)))
            row.addWidget(listen)
            choose = QPushButton('Try ' + cue['kind'])
            choose.setToolTip('Put this suggestion in the draft. Apply changes is required to change the mix.')
            choose.setAutoDefault(False)
            choose.clicked.connect(lambda checked=False, c=cue: self.try_cue(c))
            row.addWidget(choose)
            rows.addLayout(row)
        rows.addStretch(1)
        self.choices.setWidget(content)
        self.wave.update()

    def try_cue(self, cue):
        self.change_cues(cue['time'] if cue['kind'] == 'start' else self.clip.cue_in,
                         cue['time'] if cue['kind'] == 'end' else self.clip.cue_out)
        self.wave.focus_start() if cue['kind'] == 'start' else self.wave.focus_end()

    def set_busy(self, busy):
        self.busy = busy
        for control in (self.analyze_button, self.start, self.end, self.wave, self.tabs):
            control.setEnabled(not busy)
        self.audition.setEnabled(not busy and getattr(self, 'output_available', False))
        self.refresh_draft()

    def prepare_source(self, seconds, play):
        if self.busy:
            return
        from studio_grid_audio import prepare_source
        self.set_busy(True)
        path = self.track.path

        def work(progress, emit):
            messages = {'decode': 'Preparing original audio for beat checking',
                        'headroom': 'Leaving room for the reference click', 'done': 'Original audio ready'}
            return prepare_source(path, self.cache_dir.parent / 'audio-cache' / 'grid-auditions',
                                  lambda stage: progress(messages.get(stage, stage)))

        def finished(report):
            self.set_busy(False)
            if self.closing_after_job:
                super(CueReview, self).reject()
                return
            try:
                self.audition.load(report)
                self.audition.seek_to(round(seconds * 1000))
                if play and self.output_available:
                    self.audition.player.play()
                self.status.setText('Original audio ready. Click is a listening reference and is never exported.')
            except Exception as error:
                self.failed(str(error))

        self.cancel = self.start_job(work, finished, rendering=True, cancellable=True,
                                     failed_callback=self.failed, progress_callback=self.status.setText)

    def analyze_passages(self):
        if self.busy: return
        from studio_content import current_content
        from studio_downloads import ensure_models
        if not current_content(self.track) and not ensure_models(self, ['content'], self.analyze_passages): return
        from studio_content import analyze_content
        self.audition.stop(); self.set_busy(True)
        track = copy.deepcopy(self.track)

        def work(progress, emit):
            return analyze_content(track.path, track.duration, self.cache_dir, progress)

        def finished(result):
            self.set_busy(False)
            if self.closing_after_job:
                super(CueReview, self).reject(); return
            try:
                self.save_content(result)
                self.track.content = result
                self.protection.refresh()
                self.passages.refresh()
                self.status.setText('Passage observations ready. Listen to confirm possible voices or drums before changing protection or cues. Key clashes can still occur.')
            except Exception as error: self.failed(str(error))

        self.cancel = self.start_job(work, finished, rendering=True, cancellable=True,
                                     failed_callback=self.failed, progress_callback=self.status.setText)

    def try_passage(self, candidate):
        from studio_content import current_content, cue_candidates
        if self.busy: return
        if candidate not in cue_candidates(self.session, self.index, current_content(self.track)):
            self.status.setText('This passage no longer fits the current draft. Refresh the suggestions and choose again.'); return
        setattr(self.clip, 'cue_in' if candidate['kind'] == 'start' else 'cue_out', candidate['time'])
        setattr(self.clip, 'intro_percussive' if candidate['kind'] == 'start' else 'outro_percussive', False)
        if candidate['overlap']: self.session.clips[candidate['pair']].blend_bars = candidate['overlap']
        self.refresh_draft()
        self.wave.focus_start() if candidate['kind'] == 'start' else self.wave.focus_end()
        overlap = f' with a {candidate["overlap"]}-bar blend' if candidate['overlap'] else ''
        self.status.setText(f'Draft {candidate["kind"]} at {clock(candidate["time"], True)}{overlap}. '
                            f'Omits {candidate["removed_seconds"]:.1f} source seconds. Drums-only label off for this passage. Apply changes to keep it, then listen to the blend.')

    def analyze_sections(self):
        if self.busy: return
        from studio_song_sections import analyze_sections, current_sections
        if current_sections(self.track):
            self.sections.refresh()
            self.status.setText('Reusing unchanged section observations. Listen before applying any changes.'); return
        from studio_downloads import ensure_models
        if not ensure_models(self, ['sections'], self.analyze_sections): return
        self.audition.stop(); self.set_busy(True)
        track = copy.deepcopy(self.track)

        def work(progress, emit):
            return analyze_sections(track.path, track.duration, self.cache_dir, progress)

        def finished(result):
            self.set_busy(False)
            if self.closing_after_job:
                super(CueReview, self).reject(); return
            try:
                self.save_sections(result)
                self.track.song_sections = result
                self.refresh_draft(); self.wave.update()
                self.status.setText('Section suggestions ready. Listen around their boundaries, then review a protection range or try an intro/outro cue. Your mix has not changed.')
            except Exception as error: self.failed(str(error))

        self.cancel = self.start_job(work, finished, rendering=True, cancellable=True,
                                     failed_callback=self.failed, progress_callback=self.status.setText)

    def try_section_cue(self, candidate):
        from studio_song_sections import section_cues
        if self.busy: return
        if candidate not in section_cues(self.session, self.index):
            self.status.setText('That section cue no longer fits the current draft. Review the current suggestions.'); return
        setattr(self.clip, 'cue_in' if candidate['kind'] == 'start' else 'cue_out', candidate['time'])
        setattr(self.clip, 'intro_percussive' if candidate['kind'] == 'start' else 'outro_percussive', False)
        if candidate['overlap']: self.session.clips[candidate['pair']].blend_bars = candidate['overlap']
        self.refresh_draft()
        self.wave.focus_start() if candidate['kind'] == 'start' else self.wave.focus_end()
        self.status.setText(f'Draft {candidate["kind"]} at {clock(candidate["time"], True)}. '
                            f'Omits {candidate["omitted"]:.1f} source seconds; drums-only label cleared. Apply changes, then preview the actual blend.')

    def analyze(self):
        if self.busy:
            return
        from studio_downloads import ensure_models
        if not current_analysis(self.track, check_source=True) and not ensure_models(self, ['beats'], self.analyze): return
        self.set_busy(True)
        track = copy.deepcopy(self.track)

        def work(progress, emit):
            return analyze_song(track.path, track.bpm, track.duration, self.cache_dir, progress)

        def finished(result):
            self.set_busy(False)
            if self.closing_after_job:
                super(CueReview, self).reject()
                return
            try:
                if self.track.bpm == self.original_bpm:
                    self.save_analysis(result)
                self.track.structure = result
                self.show_analysis()
                self.refresh_draft()
                self.status.setText('Analysis ready. Your mix has not changed. Listen to a suggestion or try it in the draft.')
            except Exception as error:
                self.failed(str(error))

        self.cancel = self.start_job(work, finished, rendering=True, cancellable=True,
                                     failed_callback=self.failed, progress_callback=self.status.setText)

    def failed(self, message):
        self.set_busy(False)
        if self.closing_after_job:
            super().reject()
        else:
            self.status.setText(message)

    def accept_cues(self):
        try: layout(self.session)
        except djmix.MixError as error: self.status.setText(str(error)); return
        if not self.busy and self.apply_cues(self.clip.cue_in, self.clip.cue_out,
                                            self.track.bpm, self.track.beat_grid, self.track.structure,
                                            {'moments': self.clip.protected_moments,
                                             'drums': (self.clip.intro_percussive, self.clip.outro_percussive),
                                             'overlaps': {c.id: c.blend_bars for c in self.session.clips
                                                          if c.blend_bars != self.original_overlaps[c.id]}}):
            self.accept()

    def reject(self):
        self.auto_analyze.stop()
        if self.busy:
            self.closing_after_job = True
            if self.cancel is not None:
                self.cancel.set()
            self.status.setText('Stopping after the current analysis step…')
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
