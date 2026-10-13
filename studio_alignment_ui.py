"""Show measured kick hits and audition B's timing on one live clock."""
from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QHBoxLayout, QLabel, QPushButton,
                              QVBoxLayout, QWidget)

import djmix
import studio_theme as T
from studio_alignment_audio import (AlignmentMixer, AlignmentTransport, RATE, alignment_identity,
                                    cue_for_offset, offset_for_cue, prepare_alignment)
from studio_beat_alignment import estimate_alignment, kick_markers
from studio_blend_choices import draft_session


class KickView(QWidget):
    offsetRequested = Signal(float)
    nudgeRequested = Signal(float)
    seekRequested = Signal(float)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(520, 156)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Kick alignment: A above B')
        self.setAccessibleDescription('Measured kick hits in two rows. Drag the B row left to play it earlier or right '
                                      'to play it later. Arrow keys move B by 10 milliseconds; Shift moves one beat. '
                                      'These are estimated kick hits, not confirmed first beats of bars.')
        self.data = None; self.offset = 0.; self.center = 0.; self.span_beats = 4
        self.cursor = None; self.drag_origin = None

    def set_data(self, data):
        self.data = data; self.offset = 0.; self.cursor = None
        duration = data['frames'] / RATE
        candidates = [t for t in data['markers'][0] if .5 < t < duration - .5]
        self.center = candidates[len(candidates) // 2] if candidates else duration / 2
        self.update()

    def set_offset(self, seconds): self.offset = seconds; self.update()

    def window(self):
        if not self.data: return 0., 1.
        width = min(self.data['frames'] / RATE, self.span_beats * 60 / self.data['bpm'])
        start = max(0., min(self.center - width / 2, self.data['frames'] / RATE - width))
        return start, start + width

    def zoom(self, multiplier):
        self.span_beats = max(1., min(16., self.span_beats * multiplier)); self.update()

    def pan(self, direction):
        if self.data:
            a, b = self.window()
            self.center = max(0., min(self.data['frames'] / RATE, self.center + direction * (b - a) / 2))
            self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(T.FIELD))
        if not self.data:
            p.setPen(QColor(T.TEXT_3)); p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                                                 'Align beats loads the real audio and detected kick hits.')
            return
        a, b = self.window(); width = max(1., self.width() - 70.)
        def x(t): return 58 + (t - a) / (b - a) * width
        colors = [QColor(T.ACCENT), QColor('#bc88ee')]
        for row in range(2):
            center = 44 + row * 65
            p.setPen(colors[row]); p.drawText(QRectF(8, center - 10, 43, 22), Qt.AlignmentFlag.AlignVCenter,
                                             'A' if row == 0 else 'B')
            p.setPen(QColor(T.GUTTER)); p.drawLine(QPointF(58, center), QPointF(self.width() - 12, center))
            times, envelope = self.data['envelopes'][row]
            offset = self.offset if row else 0.
            display_times = np.linspace(a, b, max(1, int(width)))
            values = np.interp(display_times - offset, times, envelope, left=0, right=0)
            height = values / max(float(np.percentile(envelope, 99)), 1e-8) * 21
            p.setPen(QPen(colors[row], 1))
            for t, h in zip(display_times, np.clip(height, 0, 25)):
                p.drawLine(QPointF(x(t), center - h), QPointF(x(t), center + h))
            for hit in self.data['markers'][row]:
                t = hit + offset
                if a <= t <= b:
                    p.setPen(QPen(colors[row], 2)); p.drawLine(QPointF(x(t), center - 29), QPointF(x(t), center + 29))
                    flash = self.cursor is not None and abs(t - self.cursor) < .04
                    p.setBrush(colors[row] if flash else Qt.BrushStyle.NoBrush)
                    p.drawEllipse(QPointF(x(t), center - 30), 5 if flash else 2.5, 5 if flash else 2.5)
                    p.setBrush(Qt.BrushStyle.NoBrush)
        # Connect nearest actual attacks, making small residual offsets visible.
        other = np.array(self.data['markers'][1]) + self.offset
        if len(other):
            for hit in self.data['markers'][0]:
                nearest = other[np.argmin(abs(other - hit))]
                if a <= hit <= b and a <= nearest <= b and abs(nearest - hit) < .5 * 60 / self.data['bpm']:
                    p.setPen(QPen(QColor('#6fb794') if abs(nearest - hit) <= .015 else QColor(T.ACCENT), 1,
                                  Qt.PenStyle.DashLine))
                    p.drawLine(QPointF(x(hit), 75), QPointF(x(nearest), 79))
        if self.cursor is not None and a <= self.cursor <= b:
            p.setPen(QPen(QColor(T.TEXT), 1)); p.drawLine(QPointF(x(self.cursor), 12), QPointF(x(self.cursor), 139))
        p.setPen(QColor(T.TEXT_3))
        p.drawText(QRectF(58, 138, width, 17), Qt.AlignmentFlag.AlignLeft,
                   f'{a:.2f}–{b:.2f} sec · estimated kick hits · drag B to adjust')

    def mousePressEvent(self, event):
        if not self.data or event.button() != Qt.MouseButton.LeftButton: return
        if event.position().y() >= 79:
            self.drag_origin = event.position().x(), self.offset
        else:
            a, b = self.window()
            self.seekRequested.emit(max(0., min(self.data['frames'] / RATE,
                                                a + (event.position().x() - 58) / max(1, self.width() - 70) * (b - a))))
        self.setFocus()

    def mouseMoveEvent(self, event):
        if self.drag_origin:
            a, b = self.window()
            self.offsetRequested.emit(self.drag_origin[1] + (event.position().x() - self.drag_origin[0]) *
                                       (b - a) / max(1, self.width() - 70))

    def mouseReleaseEvent(self, _): self.drag_origin = None

    def keyPressEvent(self, event):
        if self.data and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            direction = -1 if event.key() == Qt.Key.Key_Left else 1
            self.nudgeRequested.emit(direction * (60 / self.data['bpm'] if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else .01))
            event.accept()
        else: super().keyPressEvent(event)


def visual_evidence(report):
    margin = report['margin'] / RATE
    report['markers'] = [np.asarray(kick_markers(side['full'], RATE, report['bpm'])) - margin
                         for side in report['sides']]
    envelopes = []
    for side in report['sides']:
        step = round(.005 * RATE)
        audio = side['low'][:len(side['low']) // step * step]
        peaks = np.max(abs(audio).reshape(-1, step, 2), axis=(1, 2))
        envelopes.append((np.arange(len(peaks)) * step / RATE - margin, peaks))
    report['envelopes'] = envelopes
    return report


class BeatAlignment(QWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor; self.data = None; self.result = None; self.offset = 0.
        self.player = AlignmentTransport(self); self.player.set_volume(editor.volume)
        self.setProperty('helpTopic', 'beat-alignment')
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(6)
        box.setAlignment(Qt.AlignmentFlag.AlignTop)
        title = QLabel('Beat alignment'); title.setProperty('role', 'section'); box.addWidget(title)
        pair = [editor.session.tracks[editor.session.clips[i].track_id].name for i in (editor.index - 1, editor.index)]
        names = QLabel(f'A: {pair[0]}\nB: {pair[1]}'); names.setTextFormat(Qt.TextFormat.PlainText)
        names.setWordWrap(True); names.setProperty('role', 'secondary'); box.addWidget(names)
        self.summary = QLabel('Same BPM can still leave the kicks apart. Align beats suggests a timing correction for B.')
        self.summary.setWordWrap(True); self.summary.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(self.summary)
        row = QHBoxLayout()
        self.auto = QPushButton('Align beats'); self.auto.setProperty('role', 'primary')
        self.auto.setToolTip('Find repeated kick hits near this blend and try a timing correction in the draft. '
                             'A pulse correction is within half a beat; clear repeated strong accents can suggest a whole-beat move. '
                             'Equally accented kicks cannot identify bar 1. '
                             'An uncertain check leaves the timing unchanged. Nothing enters your mix until Apply changes.')
        self.auto.clicked.connect(self.align); row.addWidget(self.auto)
        self.listen = QPushButton('Hear beats'); self.listen.clicked.connect(self.toggle)
        self.listen.setToolTip('Loop both songs together at even volume before blend fades and bass handoff. '
                               'The check uses the project reference BPM. Moving B updates this audio immediately; '
                               'use Listen in the full editor to hear the final blend.')
        row.addWidget(self.listen)
        self.focus = QCheckBox('Focus on kicks'); self.focus.setChecked(True)
        self.focus.setToolTip('Hear the low frequencies to make double kicks easier to hear. '
                              'This is a listening aid and never changes export.')
        self.focus.toggled.connect(lambda value: self.player.set_focus(value)); row.addWidget(self.focus)
        row.addStretch(); box.addLayout(row)
        self.details = QWidget()
        details = QVBoxLayout(self.details); details.setContentsMargins(0, 0, 0, 0); details.setSpacing(6)
        details.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.view = KickView(); details.addWidget(self.view)
        self.view.offsetRequested.connect(self.move_to); self.view.nudgeRequested.connect(self.nudge)
        self.view.seekRequested.connect(lambda seconds: self.player.seek(round(seconds * 1000)))
        row = QHBoxLayout()
        self.earlier = QPushButton('B earlier'); self.later = QPushButton('B later')
        self.earlier.clicked.connect(lambda: self.nudge(-self.step()))
        self.later.clicked.connect(lambda: self.nudge(self.step()))
        for button, direction in ((self.earlier, 'earlier'), (self.later, 'later')):
            button.setToolTip(f'Play B {direction} while A stays fixed. Each click is 10 milliseconds, or one beat '
                              'when By one beat is checked. The waveform and beat-check audio update immediately; '
                              'the full mix changes only after Apply changes.')
            row.addWidget(button)
        self.coarse = QCheckBox('By one beat')
        self.coarse.setToolTip('Use whole-beat steps to compare bar accents after individual kicks line up. '
                               'Unchecked uses small 10-millisecond steps. No cue-time calculations needed.')
        row.addWidget(self.coarse)
        self.reset = QPushButton('Reset alignment'); self.reset.clicked.connect(lambda: self.move_to(0.))
        self.reset.setToolTip('Restore B’s timing from before this beat check, keeping your other blend edits.')
        row.addWidget(self.reset); row.addStretch(); details.addLayout(row)
        row = QHBoxLayout(); row.addWidget(QLabel('Compare timing'))
        self.versions = QButtonGroup(self)
        self.before, self.after = QPushButton('Before'), QPushButton('Adjusted')
        for button in (self.before, self.after):
            button.setCheckable(True); self.versions.addButton(button); row.addWidget(button)
        self.after.setChecked(True)
        self.before.setToolTip('Hear and see the timing from before this check without changing the draft. Switch while playback keeps running.')
        self.after.setToolTip('Hear and see the current timing draft without restarting. Apply changes keeps it in the mix.')
        self.versions.buttonToggled.connect(lambda *_: self.monitor())
        row.addStretch()
        for label, callback in (('←', lambda: self.view.pan(-1)), ('→', lambda: self.view.pan(1)),
                                ('−', lambda: self.view.zoom(2)), ('+', lambda: self.view.zoom(.5))):
            button = QPushButton(label); button.setMaximumWidth(36); button.clicked.connect(callback); row.addWidget(button)
            button.setToolTip({'←': 'Show earlier kick hits', '→': 'Show later kick hits',
                               '−': 'Zoom out to show more beats', '+': 'Zoom into the kick hits'}[label])
        details.addLayout(row)
        hint = QLabel('Hear beats uses even levels and no blend fades. Listen below or Compare current / draft checks the actual blend. '
                      'Beat 1 of a bar may still need checking; equally strong kicks do not identify it.')
        hint.setWordWrap(True); hint.setProperty('role', 'hint'); details.addWidget(hint)
        box.addWidget(self.details)
        self.player.playingChanged.connect(lambda playing: self.listen.setText('Pause beats' if playing else 'Hear beats'))
        self.player.positionChanged.connect(self.position_changed)
        self.player.errorOccurred.connect(lambda text: editor.status.setText(text))
        editor.finished.connect(lambda _: self.player.close())
        self.refresh()
        for widget in self.findChildren(QWidget):
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton): widget.setAutoDefault(False)

    def step(self): return 60 / self.data['bpm'] if self.coarse.isChecked() and self.data else .01

    def position_changed(self, milliseconds):
        self.view.cursor = milliseconds / 1000.
        if self.data and not self.view.drag_origin:
            a, b = self.view.window()
            if not a <= self.view.cursor < b:
                self.view.center = self.view.cursor + (b - a) * .4
        self.view.update()

    def refresh(self):
        if self.data:
            try:
                actual_offset = offset_for_cue(self.editor.session, self.editor.index, self.data['cue'])
                valid = (self.data['identity'] == alignment_identity(self.editor.session, self.editor.index)
                         and abs(actual_offset) <= self.data['margin'] / RATE)
            except (OSError, ValueError, djmix.MixError): valid = False
            if not valid: self.clear()
            else:
                self.offset = actual_offset
                self.monitor()
        self.details.setVisible(self.data is not None)
        for control in (self.listen, self.earlier, self.later, self.reset, self.coarse, self.focus,
                        self.before, self.after, self.view):
            control.setEnabled(self.data is not None and not self.editor.busy)

    def clear(self):
        self.player.close(); self.data = None; self.result = None; self.offset = 0.
        self.player.mixer = None
        self.view.data = None; self.view.update()
        self.summary.setText('Audio settings changed. Align beats checks the current draft; your last cue adjustment is retained.')
        self.refresh()

    def monitor(self):
        if self.data:
            offset = self.offset if self.after.isChecked() else 0.
            self.player.set_offset(offset); self.view.set_offset(offset)

    def set_result(self, report, result):
        self.data, self.result = report, result
        self.offset = offset_for_cue(self.editor.session, self.editor.index, report['cue'])
        self.view.set_data(report); self.refresh(); self.monitor()
        self.player.load_report(report); self.player.set_focus(self.focus.isChecked()); self.monitor()
        if result['state'] == 'suggested':
            if not self.move_to(self.offset + result['offset_seconds']):
                self.summary.setText('The suggested move could not be made. ' + self.editor.status.text()
                                     + ' Timing is unchanged; compare the visible hits and try another move.')
        elif result['state'] == 'aligned':
            self.summary.setText('The repeated kick hits already line up closely. No new timing correction was made. Listen and check bar accents too.')
        else:
            self.summary.setText('No reliable automatic correction. ' + result['reason'] + ' Use the visible hits and listen while moving B.')
        self.editor.player.pause()
        self.editor.details_scroll.ensureWidgetVisible(self.view, 20, 90)
        self.player.play()

    def align(self):
        e = self.editor
        if e.busy: return
        snapshot = copy.deepcopy(e.session)
        old_data = self.data
        e.player.pause(); e.set_busy(True)
        e.status.setText('Finding repeated kick hits for A and B…')
        def work(progress, emit):
            report = old_data or visual_evidence(prepare_alignment(snapshot, e.index, e.cache_dir, progress))
            progress('Comparing kick positions across independent passages')
            mixer = AlignmentMixer(report)
            offset = offset_for_cue(snapshot, e.index, report['cue'])
            n = np.arange(report['frames']) + report['margin']
            out = mixer.sample(report['sides'][0]['full'], n)
            inc = mixer.sample(report['sides'][1]['full'], n - offset * RATE)
            result = estimate_alignment(out, inc, RATE, report['bpm'])
            return report, result
        def finished(value):
            e.set_busy(False)
            if e.closing_after_job: e.reject(); return
            report, result = value
            try:
                if e.session.to_dict() != snapshot.to_dict() or alignment_identity(e.session, e.index) != report['identity']:
                    self.clear(); e.status.setText('The draft or source changed. Align beats again.'); return
                if old_data:
                    self.result = result
                    if result['state'] == 'suggested':
                        if not self.move_to(self.offset + result['offset_seconds']):
                            self.summary.setText('The suggested move could not be made. ' + e.status.text() + ' Timing is unchanged.')
                    else: self.summary.setText(result['reason'] + ' Check the visible hits and listen.')
                    self.monitor(); self.player.play()
                else: self.set_result(report, result)
                e.status.setText('Beat check ready. Before / Adjusted switches continuously. Listen to the actual blend before Apply changes.')
            except Exception as error: failed(str(error))
        def failed(message):
            self.clear(); e.failed(message)
        e.cancel = e.start_job(work, finished, rendering=True, cancellable=True,
                               failed_callback=failed, progress_callback=e.status.setText,
                               title='Aligning the beats', reason='Checking repeated kick hits near this blend. The result stays a draft.')

    def move_to(self, offset):
        e = self.editor
        if e.busy or not self.data: return False
        if abs(offset) > self.data['margin'] / RATE:
            e.status.setText('The beat check allows up to one bar either way. Reset or align again for a new check.'); return False
        try:
            clip = copy.deepcopy(e.session.clips[e.index])
            clip.cue_in = cue_for_offset(e.session, e.index, self.data['cue'], offset)
            if abs(offset) < .00001:
                clip.intro_percussive = self.data.get('intro_percussive', False)
            elif clip.cue_in != e.session.clips[e.index].cue_in:
                clip.intro_percussive = False
            draft_session(e.session, e.index, clip)
        except (ValueError, djmix.MixError) as error:
            e.status.setText(str(error)); return False
        live = e.session.clips[e.index]
        if (live.cue_in, live.intro_percussive) != (clip.cue_in, clip.intro_percussive):
            live.cue_in, live.intro_percussive = clip.cue_in, clip.intro_percussive
            e.change_draft()
        self.offset = offset
        self.after.setChecked(True); self.monitor()
        self.summary.setText('B timing unchanged.' if abs(offset) < .00001 else
                             f'B plays {abs(offset) * 1000:.0f} ms {"later" if offset > 0 else "earlier"} in this draft. '
                             'Compare Before / Adjusted, then check the full blend below.')
        self.refresh()
        return True

    def nudge(self, delta): self.move_to(self.offset + delta)

    def toggle(self):
        if self.editor.busy or self.data is None: return
        if self.player.playing: self.player.pause()
        else:
            self.editor.player.pause(); self.player.play()
