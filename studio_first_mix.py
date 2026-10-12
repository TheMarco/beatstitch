"""A guided native path from selected songs to a reviewable first arrangement."""
from __future__ import annotations

import copy
import math
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout,
                              QInputDialog, QLabel, QLineEdit, QPushButton, QScrollArea,
                              QSpinBox, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

import djmix
import studio_theme as T
from studio_engine import Clip, Session, layout
from studio_listening import SourceAudition
from studio_order_dialog import OrderDialog
from studio_recommendations import tempo_advice
from studio_widgets import clock, display_key


def starter_session(tracks, identities, tempo, requested_bars=8, name='Untitled mix'):
    """Use library cues and original keys; fit overlaps without shortening a clip."""
    if len(identities) < 2 or len(set(identities)) != len(identities):
        raise djmix.MixError('Choose at least two different songs for this mix.')
    if not math.isfinite(tempo) or not 20 <= tempo <= 300:
        raise djmix.MixError('Choose a mix tempo between 20 and 300 BPM.')
    if type(requested_bars) is not int or not 1 <= requested_bars <= 32:
        raise djmix.MixError('Choose an overlap between 1 and 32 bars.')
    session = Session(name=name, target_bpm=tempo, tracks=copy.deepcopy(tracks))
    capacity = []
    for i, identity in enumerate(identities):
        track = session.tracks.get(identity)
        if track is None or track.bpm is None or not math.isfinite(track.bpm) or not 20 <= track.bpm <= 300:
            raise djmix.MixError('A selected song needs a BPM. Enter it or choose another song.')
        start, end = track.cue_in, track.cue_out or track.duration
        if not all(math.isfinite(v) for v in (start, end, track.duration)) or not 0 <= start < end <= track.duration:
            raise djmix.MixError(f'Check the library start and end cues for {track.name}.')
        clip = Clip(identity, start, end, blend_bars=requested_bars)
        session.clips.append(clip)
        frames = round((end - start) * track.bpm / tempo * djmix.SR)
        sides = 1 if i in (0, len(identities) - 1) else 2
        capacity.append(max((bars for bars in range(1, 33)
                             if sides * round(bars * 240 / tempo * djmix.SR) < frames), default=0))
        if capacity[-1] < 1:
            raise djmix.MixError(f'{track.name} has too little kept audio for its blends. Choose a longer song or widen its library cues.')
    notes = []
    for i, clip in enumerate(session.clips[1:], 1):
        clip.blend_bars = min(requested_bars, capacity[i - 1], capacity[i])
        if clip.blend_bars < requested_bars:
            notes.append(f'{i} → {i + 1}: {clip.blend_bars} bars fit the available audio instead of {requested_bars}.')
    layout(session)
    return session, notes


def text(value):
    result = QLabel(value); result.setWordWrap(True); result.setTextFormat(Qt.TextFormat.PlainText)
    return result


class FirstMixGuide(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.revision = host.revision
        self.tracks = copy.deepcopy(host.session.tracks)
        self.edited_bpms, self.rows = {}, {}
        self.busy = self.closing_after_job = self.created = False
        self.cancel = self.planner = None
        self.help_window = None
        self.setWindowTitle('Make a mix · guided setup')
        self.setFont(T.font(14))
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(740, 610); self.resize(960, 740)
        box = QVBoxLayout(self); box.setContentsMargins(22, 20, 22, 20); box.setSpacing(10)
        title = text('Make a mix'); title.setStyleSheet('font-size: 22px; font-weight: 600'); box.addWidget(title)
        self.step = text('Step 1 of 4 · Choose songs'); box.addWidget(self.step)
        box.addWidget(text('Choose songs → review settings → hear a draft → listen and export. You decide what to keep.'))
        self.pages = QStackedWidget(); box.addWidget(self.pages, 1)
        self.build_songs(); self.build_settings(); self.build_finish()
        self.status = text(''); box.addWidget(self.status)
        buttons = QHBoxLayout()
        self.help = QPushButton('Help'); self.help.clicked.connect(self.show_help)
        self.help.setToolTip('Open the offline guide to this workflow. F1 opens the same help.')
        self.help_shortcut = QShortcut(QKeySequence('F1'), self); self.help_shortcut.activated.connect(self.help.click)
        buttons.addWidget(self.help); buttons.addStretch()
        self.back = QPushButton('Back to songs'); self.back.clicked.connect(self.back_to_songs); buttons.addWidget(self.back)
        self.back.setToolTip('Return to the song selection. Nothing has been applied to the arrangement.')
        self.close_button = QPushButton('Cancel'); self.close_button.clicked.connect(self.reject); buttons.addWidget(self.close_button)
        self.close_button.setToolTip('Close the guide. Only an explicitly applied draft changes the arrangement; imported songs stay in the library.')
        self.next_button = QPushButton('Next: mix settings'); self.next_button.setProperty('role', 'primary')
        self.next_button.clicked.connect(self.next_step); buttons.addWidget(self.next_button); box.addLayout(buttons)
        self.next_button.setToolTip('Continue to a reviewable draft. The arrangement changes only after Create mix or Replace arrangement.')
        self.devices = QMediaDevices(self); self.devices.audioOutputsChanged.connect(self.refresh_output)
        self.audition.failed.connect(self.status.setText)
        self.finished.connect(lambda _: self.audition.stop())
        self.refresh_songs(); self.refresh_output(); self.update_step()
        for button in self.findChildren(QPushButton):
            button.setMinimumHeight(30); button.setAutoDefault(False)
        for widget in self.findChildren(QWidget):
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())

    def show_help(self):
        from studio_help import HelpDialog
        if self.help_window is None:
            self.help_window = HelpDialog(self)
        self.help_window.open_topic('first-mix')
        self.help_window.show(); self.help_window.raise_()

    def build_songs(self):
        page = QWidget(); box = QVBoxLayout(page); box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(text('Choose at least two songs. Listen to a song before including it, or import your own audio.'))
        row = QHBoxLayout()
        for title, folder in (('Add files…', False), ('Add folder…', True)):
            button = QPushButton(title); button.clicked.connect(lambda checked=False, f=folder: self.import_audio(f))
            button.setToolTip('Add audio to the library and estimate BPM and key locally. Imported songs remain in the library if you cancel the guide.')
            row.addWidget(button)
        refresh = QPushButton('Refresh')
        refresh.setToolTip('Check again after restoring a missing file or reconnecting a drive. Your available song selections stay selected.')
        refresh.clicked.connect(self.refresh_songs); row.addWidget(refresh)
        self.filter = QLineEdit(); self.filter.setPlaceholderText('Find a song'); self.filter.setAccessibleName('Find a song in the guide')
        self.filter.setToolTip('Filter by song name. Songs you already checked stay selected when hidden by this filter.')
        self.filter.textChanged.connect(self.filter_songs); row.addWidget(self.filter, 1); box.addLayout(row)
        self.count = text(''); box.addWidget(self.count)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        self.song_fields = QWidget(); self.song_box = QVBoxLayout(self.song_fields); self.song_box.setContentsMargins(4, 4, 4, 4)
        self.song_box.addStretch(); scroll.setWidget(self.song_fields); box.addWidget(scroll, 1)
        self.audition = SourceAudition(); self.audition.aboutToPlay.connect(self.host.stop)
        box.addWidget(self.audition)
        box.addWidget(text('BPM and key are estimates. Songs with no BPM need a value before they can be included. Imported files stay untouched.'))
        self.pages.addWidget(page)

    def refresh_songs(self):
        for identity, track in self.tracks.items():
            if identity not in self.rows:
                field = QWidget(); row = QHBoxLayout(field); row.setContentsMargins(2, 5, 2, 5)
                chosen = QCheckBox(); chosen.toggled.connect(self.selection_changed); row.addWidget(chosen)
                name = text(''); row.addWidget(name, 1)
                bpm = QPushButton('BPM…'); bpm.clicked.connect(lambda checked=False, i=identity: self.enter_bpm(i))
                bpm.setToolTip('Enter this song’s source BPM if you know it. This remains a draft until Apply.'); row.addWidget(bpm)
                listen = QPushButton('Listen'); listen.clicked.connect(lambda checked=False, i=identity: self.listen_song(i))
                listen.setToolTip('Audition the original song without adding it to the mix. Use the position slider below to seek.'); row.addWidget(listen)
                self.song_box.insertWidget(self.song_box.count() - 1, field)
                self.rows[identity] = field, chosen, name, bpm, listen
            field, chosen, name, bpm, listen = self.rows[identity]
            valid = track.bpm is not None and math.isfinite(track.bpm) and 20 <= track.bpm <= 300
            available = Path(track.path).is_file()
            details = (f'{track.bpm:.1f} BPM · {display_key(track.key) or "Key unknown"} · {clock(track.duration)}'
                       if valid else f'BPM needed · {clock(track.duration)}')
            if not available:
                details += '\nSource file missing. Restore it and click Refresh, or close this guide and use File → Reconnect source audio…'
            name.setText(f'{track.name}\n{details}')
            chosen.setAccessibleName('Include ' + track.name)
            chosen.setEnabled(valid and available)
            if not chosen.isEnabled(): chosen.setChecked(False)
            chosen.setToolTip('Include this song in the draft.' if valid and available else
                              'Restore the missing source file and click Refresh, or close this guide and use File → Reconnect source audio…' if not available else
                              'Enter a source BPM using BPM…, or choose another song.')
            chosen.setAccessibleDescription(chosen.toolTip())
            for button, action in ((bpm, 'Edit source BPM for '), (listen, 'Listen to ')):
                button.setAccessibleName(action + track.name)
            listen.setEnabled(available); bpm.setEnabled(available)
        self.filter_songs(); self.selection_changed()

    def filter_songs(self, *_):
        query = self.filter.text().casefold().strip()
        for identity, row in self.rows.items(): row[0].setVisible(query in self.tracks[identity].name.casefold())

    def selected(self):
        return [identity for identity, row in self.rows.items() if row[1].isChecked()]

    def selection_changed(self, *_):
        self.count.setText(f'{len(self.selected())} songs selected · {len(self.tracks)} in the library')
        if hasattr(self, 'next_button'): self.next_button.setEnabled(not self.busy and len(self.selected()) >= 2)

    def enter_bpm(self, identity):
        track = self.tracks[identity]
        value, ok = QInputDialog.getDouble(self, 'Source BPM', f'Original BPM of {track.name}. Enter it only if known:',
                                            track.bpm or 128, 20, 300, 2)
        if ok:
            track.bpm = value; self.edited_bpms[identity] = value; self.refresh_songs()

    def refresh_output(self):
        chosen = QMediaDevices.defaultAudioOutput() if self.host.output_preference == 'system' else next(
            (d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == self.host.output_preference), None)
        valid = chosen is not None and not chosen.isNull()
        self.audition.output.setMuted(not valid); self.audition.setEnabled(valid and not self.busy)
        self.audition.output.setVolume(self.host.volume.value() / 100)
        if valid: self.audition.output.setDevice(chosen)
        else: self.audition.stop()
        return valid

    def listen_song(self, identity):
        if not self.refresh_output():
            self.status.setText('Audio output unavailable. Reconnect it or select another output in the main window.'); return
        self.audition.set_track(self.tracks[identity]); self.audition.listen_at(0)

    def import_audio(self, folder=False):
        if self.busy: return
        if folder:
            path = QFileDialog.getExistingDirectory(self, 'Choose a music folder')
            paths = self.host.audio_files([Path(path)]) if path else []
        else:
            paths, _ = QFileDialog.getOpenFileNames(self, 'Choose songs', filter='Audio (*.wav *.mp3 *.flac *.aif *.aiff *.m4a *.ogg *.aac)')
            paths = [Path(p) for p in paths]
        if not paths: return
        self.set_busy(True)
        def completed(result):
            self.finish_import('Songs are ready. Choose which ones to include.' if not result[1] else 'Some files could not be imported. Choose from the available songs.')
        self.cancel = self.host.import_paths(paths, finished_callback=completed,
                                            failed_callback=self.finish_import, progress_callback=self.status.setText,
                                            cancellable=True)
        if self.cancel is None: self.finish_import('Import could not start. Wait for the current job to finish.')

    def finish_import(self, message):
        self.set_busy(False)
        if self.closing_after_job:
            super().reject(); return
        self.tracks = copy.deepcopy(self.host.session.tracks)
        for identity, bpm in self.edited_bpms.items():
            if identity in self.tracks: self.tracks[identity].bpm = bpm
        self.refresh_songs(); self.status.setText(message)

    def set_busy(self, busy):
        self.busy = busy; self.pages.setEnabled(not busy); self.audition.stop()
        self.back.setEnabled(not busy); self.next_button.setEnabled(not busy and len(self.selected()) >= 2)
        self.close_button.setText('Stop and close' if busy else 'Cancel')

    def build_settings(self):
        page = QWidget(); box = QVBoxLayout(page); box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(text('Every song follows one mix tempo. Review how much each source will speed up or slow down.'))
        row = QHBoxLayout(); row.addWidget(text('Mix BPM'))
        self.tempo = QDoubleSpinBox(); self.tempo.setRange(20, 300); self.tempo.setDecimals(1); self.tempo.setValue(self.host.session.target_bpm)
        self.tempo.setAccessibleName('Guided mix tempo in BPM'); self.tempo.setToolTip('The common tempo of the new draft. Original pitches are preserved. You can still adjust this later.')
        row.addWidget(self.tempo)
        self.balance = QPushButton('Use suggested tempo'); self.balance.clicked.connect(self.balance_tempo); row.addWidget(self.balance); row.addStretch(); box.addLayout(row)
        self.balance.setToolTip('Balance the largest percentage speed change between the fastest and slowest selected songs. Review the changes and listen before applying.')
        self.tempo_note = text(''); box.addWidget(self.tempo_note)
        self.changes = QTextEdit(); self.changes.setReadOnly(True); self.changes.setAccessibleName('Tempo changes for selected songs'); box.addWidget(self.changes, 1)
        row = QHBoxLayout(); row.addWidget(text('Starting overlap'))
        self.overlap = QSpinBox(); self.overlap.setRange(1, 32); self.overlap.setValue(8); self.overlap.setSuffix(' bars')
        self.overlap.setAccessibleName('Starting overlap length in bars')
        self.overlap.setToolTip('One bar is four beats. This is a starting point for all blends; short clips receive a smaller overlap, shown below. Each blend remains editable.')
        row.addWidget(self.overlap); self.overlap_note = text(''); row.addWidget(self.overlap_note, 1); box.addLayout(row)
        box.addWidget(text('Original keys are kept. The next step suggests an order, lets you choose an opener or closer, and previews individual blends. You can try pitch alternatives in the blend editor afterward.'))
        box.addWidget(text('The draft uses the library’s estimated start/end cues and a bass handoff at each blend. Review the music before exporting.'))
        self.tempo.valueChanged.connect(self.update_settings); self.overlap.valueChanged.connect(self.update_settings)
        self.pages.addWidget(page)

    def draft(self):
        self.require_sources(self.selected())
        return starter_session(self.tracks, self.selected(), self.tempo.value(), self.overlap.value(), self.host.session.name)

    def require_sources(self, identities):
        missing = [self.tracks[identity].name if identity in self.tracks else 'a selected song' for identity in identities
                   if identity not in self.tracks or not Path(self.tracks[identity].path).is_file()]
        if missing:
            names = ', '.join(missing[:3]) + (f' and {len(missing) - 3} more' if len(missing) > 3 else '')
            raise djmix.MixError(f'Source audio is missing for {names}. Restore the files or reconnect the drive, then try again. '
                                 'If the files moved, close this guide and use File → Reconnect source audio… '
                                 'Your arrangement has not changed.')

    def balance_tempo(self):
        try:
            session, _ = self.draft(); advice = tempo_advice(session, self.host.mix_review.tempo_limit.value())
            if advice.get('available'): self.tempo.setValue(advice['target'])
            else: self.status.setText(advice['reason'])
        except djmix.MixError as error: self.status.setText(str(error))

    def update_settings(self, *_):
        if self.pages.currentIndex() != 1: return
        try:
            session, notes = self.draft(); advice = tempo_advice(session, self.host.mix_review.tempo_limit.value())
            self.tempo_note.setText(advice['reason']); self.balance.setEnabled(advice.get('available', False))
            rows = [f'{self.tracks[i].name}: {self.tracks[i].bpm:.1f} → {session.target_bpm:g} BPM ({(session.target_bpm / self.tracks[i].bpm - 1) * 100:+.1f}%)'
                    for i in self.selected()]
            self.changes.setPlainText('\n'.join(rows + notes))
            self.overlap_note.setText(f'{self.overlap.value() * 240 / session.target_bpm:.1f} seconds · individual blends stay editable')
            self.next_button.setEnabled(True); self.status.setText('Review these settings, then hear a draft before applying it.')
        except djmix.MixError as error:
            self.status.setText(str(error)); self.next_button.setEnabled(False)

    def back_to_songs(self):
        self.pages.setCurrentIndex(0); self.update_step(); self.selection_changed(); self.status.clear()

    def update_step(self):
        stage = self.pages.currentIndex()
        self.step.setText(('Step 1 of 4 · Choose songs', 'Step 2 of 4 · Mix settings', 'Step 4 of 4 · Listen and export')[stage])
        self.back.setVisible(stage == 1); self.next_button.setVisible(stage != 2)
        self.next_button.setText('Next: mix settings' if stage == 0 else 'Next: review draft')
        self.close_button.setText('Explore the main controls' if stage == 2 else 'Cancel')

    def next_step(self):
        if self.busy: return
        self.audition.stop()
        try: self.require_sources(self.selected())
        except djmix.MixError as error:
            self.status.setText(str(error)); return
        if self.pages.currentIndex() == 0:
            self.pages.setCurrentIndex(1); self.update_step(); self.balance_tempo(); self.update_settings(); return
        try: session, notes = self.draft()
        except djmix.MixError as error: self.status.setText(str(error)); return
        self.step.setText('Step 3 of 4 · Review and listen to the draft')
        count = len(self.host.session.clips)
        notice = (f'Apply will replace the current {count}-song arrangement. One Undo restores it. '
                  if count else 'Apply creates the arrangement. One Undo restores the empty mix. ')
        notice += ('This draft uses library cues and original keys, not edits from the existing arrangement. '
                   'Imported songs stay in the library.')
        if any(c.protected_moments for c in self.host.session.clips):
            notice += ' Replacing this arrangement also removes its passage protections; use Suggest order in the main window to keep them.'
        if self.host.session.fade_in_bars or self.host.session.fade_out_bars:
            notice += ' This fresh draft has opening and ending fades off; use Start & ending after applying to set them again.'
        self.planner = OrderDialog(session, self.host.bands, self.host.data / 'audio-cache', self.host.output_preference,
                                   self.host.volume.value() / 100, self.host.job, self.apply_plan, self,
                                   new_mix=True, review_notice=notice)
        self.planner.apply.setText('Replace arrangement' if count else 'Create mix')
        self.planner.apply.setToolTip('Apply the reviewed songs, tempo, library cues, overlaps and planning preferences as one Undo action. Existing clip edits are replaced.')
        self.planner.apply.setAccessibleDescription(self.planner.apply.toolTip())
        def finished(_):
            self.planner = None
            if self.created:
                self.pages.setCurrentIndex(2); self.status.clear()
            self.update_step()
        self.planner.finished.connect(finished)
        self.planner.open(); QTimer.singleShot(0, self.planner.suggest)

    def apply_plan(self, draft):
        if self.host.revision != self.revision:
            self.planner.status.setText('The arrangement changed. Close the guide and start again before applying.'); return False
        try: self.require_sources([clip.track_id for clip in draft.clips])
        except djmix.MixError as error:
            self.planner.status.setText(str(error)); return False
        def mutate():
            for identity in self.selected():
                if identity not in self.host.session.tracks: raise djmix.MixError('A selected song is no longer in the library.')
            for identity, bpm in self.edited_bpms.items():
                if identity in self.selected():
                    self.host.session.tracks[identity].bpm = bpm
                    self.host.session.tracks[identity].status = 'BPM entered in Mix guide'
            self.host.session.clips = copy.deepcopy(draft.clips)
            self.host.session.target_bpm = draft.target_bpm
            self.host.session.tempo_enabled = draft.tempo_enabled
            self.host.session.tempo_points = copy.deepcopy(draft.tempo_points)
            self.host.session.planning = copy.deepcopy(draft.planning)
            self.host.session.fade_in_bars, self.host.session.fade_out_bars = draft.fade_in_bars, draft.fade_out_bars
        if not self.host.edit(mutate, action='Create mix with guide'): return False
        self.host.touched.update(set(self.edited_bpms).intersection(self.selected()))
        self.host.selected_id = draft.clips[0].id; self.host.refresh(); self.host.timeline.fit(); self.host.load_bands()
        self.host.rewind()
        self.created = True
        self.finish_note.setText(f'{len(draft.clips)} songs · {clock(layout(draft)[-1]["end"] / djmix.SR)} at {draft.target_bpm:g} BPM. '
                                 'The draft is now in the arrangement. Listen before exporting; Undo restores the previous mix.')
        return True

    def build_finish(self):
        page = QWidget(); box = QVBoxLayout(page); box.setContentsMargins(0, 0, 0, 0)
        self.finish_note = text(''); box.addWidget(self.finish_note)
        for title, callback, explanation in (
            ('Review each blend', self.host.review_transitions, 'Recommended: listen to each transition and choose Keep, Change or Next.'),
            ('Start && ending…', self.host.show_finishing, 'Preview fades and include the original source ending so a recorded tail is not cut off.'),
            ('Play the whole mix', self.host.render_full, 'Listen to the overall flow. Audio is prepared automatically.'),
            ('Export audio…', self.host.export_mix, 'Create a WAV, FLAC or MP3 to share. Export opens choices; it does not save a file immediately.')):
            button = QPushButton(title); button.setMinimumHeight(42); button.setToolTip(explanation)
            button.clicked.connect(lambda checked=False, action=callback: self.finish_with(action))
            box.addWidget(button); box.addWidget(text(explanation))
        box.addWidget(text('Save in the main window keeps an editable project. Export makes an audio file. Use Help or hover over a control whenever you need an explanation.'))
        box.addStretch(); self.pages.addWidget(page)

    def finish_with(self, callback):
        self.accept(); QTimer.singleShot(0, callback)

    def reject(self):
        if self.busy:
            self.closing_after_job = True
            if self.cancel is not None: self.cancel.set()
            self.status.setText('Stopping import. Songs already imported remain in the library.')
            return
        super().reject()
