"""Draft, audition and apply the opening and ending of a mix."""
import copy

import numpy as np
from PySide6.QtCore import QPointF, QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                              QPushButton, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

import djmix
import studio_theme as T
from studio_engine import layout
from studio_finishing import envelope, limits, preview_region
from studio_timing import corrected_time
from studio_widgets import CueEditor, clock


class EdgeWave(CueEditor):
    """Read-only source waveform with the project fade mapped back to source time."""
    def __init__(self, bands):
        super().__init__(bands)
        self.session = None
        self.side = 'in'
        self.setMinimumHeight(140)
        self.setToolTip('Real source waveform. The gold curve shows fade volume. Change the fade using the stepper above; source cues can also be edited in Song cues.')
        self.setAccessibleName('Opening or ending source waveform and fade curve')

    def mousePressEvent(self, event): event.ignore()
    def mouseMoveEvent(self, event): event.ignore()
    def mouseReleaseEvent(self, event): event.ignore()
    def wheelEvent(self, event): event.ignore()

    def show_edge(self, session, side):
        self.session, self.side = session, side
        clip = session.clips[0 if side == 'in' else -1]
        track = session.tracks[clip.track_id]
        self.set_clip(track, clip, 0, 0)
        bars = session.fade_in_bars if side == 'in' else session.fade_out_bars
        span = max(8, bars + 4) * 240 / track.bpm
        if side == 'in': self._set_view(clip.cue_in, min(track.duration, clip.cue_in + span))
        else: self._set_view(max(0, clip.cue_out - span), clip.cue_out)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.session is None: return
        try: positions = layout(self.session)
        except djmix.MixError: return
        pos = positions[0 if self.side == 'in' else -1]
        times = np.linspace(max(self.clip.cue_in, self.view_start), min(self.clip.cue_out, self.view_end), 180)
        origin = corrected_time(self.track, self.clip, self.clip.cue_in)
        frames = np.array([pos['start'] + (corrected_time(self.track, self.clip, t) - origin)
                           * self.track.bpm / self.session.target_bpm * djmix.SR for t in times])
        gains = envelope(self.session, positions, frames)
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(T.ACCENT), 2))
        wave = self.wave_rect().adjusted(0, 5, 0, -5); path = QPainterPath()
        for i, (t, gain) in enumerate(zip(times, gains)):
            point = QPointF(self.x_of(t), wave.bottom() - gain * wave.height())
            if i: path.lineTo(point)
            else: path.moveTo(point)
        painter.drawPath(path)


class FinishingDialog(QDialog):
    def __init__(self, studio):
        super().__init__(studio)
        self.studio = studio
        self.baseline = copy.deepcopy(studio.session)
        self.draft = copy.deepcopy(studio.session)
        self.busy = self.closing = self.pending_play = False
        self.cancel = None; self.ready_side = None; self.preview_path = None
        self.help_window = None
        self.setWindowTitle('Start & ending')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(650, 540); self.resize(760, 610)
        self.player = QMediaPlayer(self); self.audio = QAudioOutput(self)
        self.audio.setVolume(studio.volume.value() / 100)
        self.player.setAudioOutput(self.audio); self.devices = QMediaDevices(self)
        box = QVBoxLayout(self); box.setContentsMargins(22, 20, 22, 20); box.setSpacing(10)
        title = QLabel('Give your mix a clean start and ending'); title.setProperty('role', 'title'); box.addWidget(title)
        hint = QLabel('Try a fade, or include the original recording’s beginning or ending. Listen before applying; these are project choices.')
        hint.setWordWrap(True); box.addWidget(hint)
        self.tabs = QTabWidget(); box.addWidget(self.tabs, 1)
        self.spins, self.keep, self.details, self.waves, self.names = {}, {}, {}, {}, {}
        for side, label, index, field in [('in', 'Opening', 0, 'fade_in_bars'), ('out', 'Ending', -1, 'fade_out_bars')]:
            pane = QWidget(); column = QVBoxLayout(pane)
            clip = self.baseline.clips[index]; track = self.baseline.tracks[clip.track_id]
            name = QLabel(track.name); name.setTextFormat(Qt.TextFormat.PlainText); name.setWordWrap(True)
            name.setProperty('role', 'heading'); column.addWidget(name); self.names[side] = name
            row = QHBoxLayout(); row.addWidget(QLabel('Fade in' if side == 'in' else 'Fade out'))
            spin = QSpinBox(); spin.setRange(0, 32); spin.setSuffix(' bars'); spin.setSpecialValueText('Off')
            spin.setValue(getattr(self.draft, field)); spin.setMinimumWidth(110)
            spin.setAccessibleName('Opening fade-in length in bars' if side == 'in' else 'Ending fade-out length in bars')
            spin.setToolTip('Fade smoothly over this many four-beat bars at the project tempo. Off keeps the source level. Fades stay outside blends and protected passages.')
            row.addWidget(spin); row.addStretch(); column.addLayout(row); self.spins[side] = spin
            keep = QCheckBox('Include remaining source beginning' if side == 'in' else 'Include remaining source ending')
            extra = clip.cue_in if side == 'in' else max(0, track.duration - clip.cue_out)
            keep.setEnabled(extra > 1 / djmix.SR)
            keep.setToolTip('Extend the kept audio to the start/end of the original file. This includes all remaining audio, not just reverb. Uncheck to restore the current mix cue; it cannot recreate audio missing from the source file.')
            column.addWidget(keep); self.keep[side] = keep
            detail = QLabel(); detail.setWordWrap(True); detail.setTextFormat(Qt.TextFormat.PlainText)
            column.addWidget(detail); self.details[side] = detail
            wave = EdgeWave(studio.bands); self.waves[side] = wave; column.addWidget(wave, 1)
            caption = QLabel('Source waveform · gold curve = fade volume'); caption.setProperty('role', 'secondary'); column.addWidget(caption)
            self.tabs.addTab(pane, label)
            spin.valueChanged.connect(self.change_draft); keep.toggled.connect(self.change_draft)
        self.tabs.currentChanged.connect(self.stop_preview)
        row = QHBoxLayout()
        self.listen = QPushButton('Listen to opening'); self.listen.clicked.connect(self.listen_edge)
        self.listen.setToolTip('Prepare and listen to this draft opening or ending, including its fade and source-cue choices. The main mix remains unchanged until Apply changes.')
        row.addWidget(self.listen)
        self.stop = QPushButton('Stop'); self.stop.clicked.connect(self.stop_preview); row.addWidget(self.stop)
        self.stop.setToolTip('Stop the draft preview and return its listening position to the beginning.')
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.setAccessibleName('Opening or ending preview position')
        self.seek.setToolTip('Seek within this opening or ending preview. Use Left/Right arrows when focused.')
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.seek.valueChanged.connect(lambda n: self.player.setPosition(n) if not self.seek.isSliderDown() else None)
        row.addWidget(self.seek, 1); self.time = QLabel('0:00'); row.addWidget(self.time); box.addLayout(row)
        self.output = QLabel(); self.output.setWordWrap(True); box.addWidget(self.output)
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close | QDialogButtonBox.StandardButton.Help)
        self.buttons.helpRequested.connect(self.show_help)
        self.help_shortcut = QShortcut(QKeySequence('F1'), self)
        self.help_shortcut.activated.connect(self.show_help)
        self.apply = self.buttons.button(QDialogButtonBox.StandardButton.Apply); self.apply.setText('Apply changes')
        self.apply.setToolTip('Keep both tabs’ fade and source-cue choices in the mix. One Undo restores the previous choices.')
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setToolTip('Close without applying draft changes.')
        self.buttons.button(QDialogButtonBox.StandardButton.Help).setToolTip('Explain fades, recorded tails and duration limits in the offline guide.')
        self.apply.clicked.connect(self.apply_changes); self.buttons.rejected.connect(self.reject); box.addWidget(self.buttons)
        self.player.positionChanged.connect(self.position_changed)
        self.player.mediaStatusChanged.connect(self.media_ready)
        self.player.playbackStateChanged.connect(lambda _: self.update_controls())
        self.player.errorOccurred.connect(lambda *_: self.failed('Preview could not play: ' + self.player.errorString()))
        self.devices.audioOutputsChanged.connect(self.refresh_output)
        self.finished.connect(self.cleanup)
        for control in self.findChildren(QWidget):
            control.setProperty('helpTopic', 'finishing')
            if control.toolTip(): control.setAccessibleDescription(control.toolTip())
            if isinstance(control, (QPushButton, QCheckBox, QSpinBox)): control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            if isinstance(control, QPushButton): control.setAutoDefault(False)
        self.refresh_output(); self.change_draft()

    def show_help(self):
        from studio_help import HelpDialog
        if self.help_window is None: self.help_window = HelpDialog(self)
        self.help_window.open_topic('finishing'); self.help_window.show()
        self.help_window.raise_(); self.help_window.activateWindow()

    def refresh_output(self):
        preferred = self.studio.output_preference
        device = QMediaDevices.defaultAudioOutput() if preferred == 'system' else next(
            (d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == preferred), None)
        self.available = device is not None and not device.isNull()
        self.audio.setMuted(not self.available)
        if self.available:
            self.audio.setDevice(device); self.output.setText('Output: ' + device.description())
        else:
            self.player.pause(); self.pending_play = False
            self.output.setText('Output unavailable. Reconnect it, or close this window and choose an output.')
        self.update_controls()

    def change_draft(self, *_):
        self.stop_preview()
        self.draft = copy.deepcopy(self.baseline)
        self.draft.fade_in_bars = self.spins['in'].value(); self.draft.fade_out_bars = self.spins['out'].value()
        if self.keep['in'].isChecked(): self.draft.clips[0].cue_in = 0
        if self.keep['out'].isChecked():
            last = self.draft.clips[-1]; last.cue_out = self.draft.tracks[last.track_id].duration
        # Obtain limits from the same cues with fades temporarily off, without silently clamping the draft.
        clean = copy.deepcopy(self.draft); clean.fade_in_bars = clean.fade_out_bars = 0
        positions = layout(clean); allowed = limits(clean, positions)
        for side, index, field in [('in', 0, 'cue_in'), ('out', -1, 'cue_out')]:
            clip = self.draft.clips[index]; track = self.draft.tracks[clip.track_id]
            current = getattr(self.baseline.clips[index], field)
            extra = current if side == 'in' else max(0, track.duration - current)
            max_bars, reason = allowed[side]
            self.spins[side].setMaximum(max(max_bars, self.spins[side].value()))
            seconds = self.spins[side].value() * 240 / self.draft.target_bpm
            from studio_tempo_curve import session_curve
            curve = session_curve(self.draft)
            total = positions[-1]['end'] / djmix.SR
            seconds = float(curve.to_output(seconds) if side == 'in' else curve.to_output(total) - curve.to_output(total - seconds))
            added = f'Includes {extra:.2f} extra source seconds.' if self.keep[side].isChecked() else f'{extra:.2f} source seconds remain outside the cue.'
            if extra <= 1 / djmix.SR: added = 'Already includes the full source beginning.' if side == 'in' else 'Already includes the full source ending.'
            self.details[side].setText(f'{seconds:.1f} second fade · up to {max_bars} bars {reason}.\n'
                                      f'Kept source: {clock(clip.cue_in, True)} – {clock(clip.cue_out, True)}. {added}')
            self.waves[side].show_edge(self.draft, side)
        try: layout(self.draft); self.valid = True; self.status.setText('Draft only. Listen, then Apply changes to use it in the mix.')
        except djmix.MixError as error: self.valid = False; self.status.setText(str(error))
        self.update_controls()

    def update_controls(self):
        ready = not self.busy and getattr(self, 'valid', False)
        self.apply.setEnabled(ready and self.draft.to_dict() != self.baseline.to_dict())
        self.listen.setEnabled(ready and getattr(self, 'available', False))
        self.stop.setEnabled(self.preview_path is not None)
        self.seek.setEnabled(self.preview_path is not None and not self.busy)
        self.tabs.setEnabled(not self.busy)
        side = 'opening' if self.tabs.currentIndex() == 0 else 'ending'
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.listen.setText('Pause' if playing else 'Listen to ' + side)

    def position_changed(self, ms):
        with QSignalBlocker(self.seek): self.seek.setValue(ms)
        self.time.setText(f'{clock(ms / 1000)} / {clock(self.seek.maximum() / 1000)}')

    def stop_preview(self, *_):
        self.pending_play = False; self.player.stop(); self.player.setSource(QUrl())
        self.preview_path = None; self.ready_side = None
        with QSignalBlocker(self.seek): self.seek.setRange(0, 0)
        self.position_changed(0); self.studio.update_storage_pins(); self.update_controls()

    def listen_edge(self):
        if self.busy or not self.valid or not self.available: return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause(); return
        if self.preview_path:
            if self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia: self.player.setPosition(0)
            self.player.play(); return
        from studio_render_cache import prepare_listening
        snapshot = copy.deepcopy(self.draft); side = 'in' if self.tabs.currentIndex() == 0 else 'out'
        region = preview_region(snapshot, side)
        self.busy = True; self.status.setText('Preparing the draft ' + ('opening…' if side == 'in' else 'ending…'))
        self.update_controls()
        def work(progress, emit):
            return prepare_listening(snapshot, self.studio.data / 'renders', region, progress, self.studio.data / 'audio-cache')
        def finished(report):
            self.busy = False
            if self.closing:
                super(FinishingDialog, self).reject(); return
            self.preview_path = report['path']; self.ready_side = side
            self.studio.update_storage_pins()
            with QSignalBlocker(self.seek): self.seek.setRange(0, round(report['duration'] * 1000))
            self.pending_play = True; self.player.setSource(QUrl.fromLocalFile(report['path']))
            self.status.setText(f'Draft {"opening" if side == "in" else "ending"} ready · {report["duration"]:.1f} seconds. '
                                f'{report["reused_tracks"]} source(s) reused, {report["processed_tracks"]} processed. Apply changes to keep these choices.')
            self.refresh_output(); self.update_controls()
        self.cancel = self.studio.job(work, finished, rendering=True, cancellable=True,
                                      failed_callback=self.failed, progress_callback=self.status.setText)

    def media_ready(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) and self.pending_play:
            self.pending_play = False
            if self.available and not self.closing: self.player.play()

    def failed(self, message):
        self.busy = False; self.pending_play = False; self.status.setText(message); self.update_controls()
        if self.closing: super().reject()

    def apply_changes(self):
        if self.busy or not self.valid: return
        if self.studio.session.to_dict() != self.baseline.to_dict():
            self.status.setText('The mix changed. Close and reopen Start & ending before applying.'); return
        draft = self.draft
        def mutate():
            self.studio.session.fade_in_bars, self.studio.session.fade_out_bars = draft.fade_in_bars, draft.fade_out_bars
            self.studio.session.clips[0].cue_in = draft.clips[0].cue_in
            self.studio.session.clips[-1].cue_out = draft.clips[-1].cue_out
        if self.studio.edit(mutate, action='Change mix opening and ending'):
            self.studio.timeline.fit(); self.accept()

    def reject(self):
        self.stop_preview()
        if self.busy:
            self.closing = True
            if self.cancel: self.cancel.set()
            self.status.setText('Cancelling the preview before closing…')
            return
        super().reject()

    def cleanup(self, *_):
        if self.help_window is not None: self.help_window.close()
        self.pending_play = False; self.player.stop(); self.player.setSource(QUrl()); self.preview_path = None
        self.studio.update_storage_pins()
