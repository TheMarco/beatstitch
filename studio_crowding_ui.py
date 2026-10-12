"""Optional, explicitly experimental crowding check for the blend draft."""
import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import djmix
from studio_cache import source_signature
from studio_crowding_choices import choice_fingerprint, cue_candidates, draft_for_cue, search_cues
from studio_engine import layout
from studio_overlap_content import overlap_voices
from studio_overlap_crowding import analyze_crowding, crowding_advice, shorter_choice
from studio_transition_audio import prepare_blend
from studio_widgets import clock


class OverlapCrowding(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor, self.result = editor, None
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.summary = QLabel(); self.summary.setWordWrap(True); self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setToolTip('Experimental listening aid, separate from key and voice advice. Looks for different changing pitched patterns that are audible at the same time in the processed overlap. Chords, arpeggios or voices can trigger it; it does not identify instruments or prove that melodies clash. Subtle, slow or masked parts can be missed. Bass harmonics and quiet fade tails are suppressed conservatively. No confidence percentage is implied.')
        self.summary.setAccessibleDescription(self.summary.toolTip())
        row.addWidget(self.summary, 1)
        self.check = QPushButton('Check musical crowding'); self.check.setObjectName('checkCrowding')
        self.check.setToolTip('Prepare this draft if necessary, then inspect only its actual overlap. Uses the rendered pitch, tempo, fades and effects. Runs locally, reuses unchanged results, and changes no settings. Stop and close cancels the check.')
        self.check.clicked.connect(self.check_audio); row.addWidget(self.check); box.addLayout(row)
        actions = QHBoxLayout()
        self.listen = QPushButton('Listen to flagged part'); self.listen.setObjectName('listenCrowding')
        self.listen.setToolTip('Listen from two seconds before the first flagged window. The range is approximate. Switch Outgoing / Blend / Incoming to hear what each side contributes; playback continues until paused.')
        self.listen.clicked.connect(self.listen_here); actions.addWidget(self.listen)
        self.shorter = QPushButton(); self.shorter.setObjectName('tryCrowdingShorter')
        self.shorter.setToolTip('Try less time with both songs playing. This is an unscored alternative, not a promised improvement. Keeps both song cues, pitch and other settings; checks protected passages and both neighbors. Compare current / draft before Apply. Undo restores the original mix.')
        self.shorter.clicked.connect(self.try_shorter); actions.addWidget(self.shorter)
        self.search = QPushButton('Find another entrance'); self.search.setObjectName('findCrowdingCue')
        self.search.setToolTip('Compare up to eight detected or manually checked bar starts, spread up to 32 bars earlier/later. Renders each valid candidate with the same overlap, tempo, pitch and effects. Reuses prepared audio and checks. Rejects silent entrances, protected-passage conflicts and worse known vocal collisions. Keeps four bars outside the blends, or your original clear duration if shorter. This can take longer than checking one blend. Stop and close cancels it; no cues change automatically.')
        self.search.clicked.connect(self.find_entrances); actions.addWidget(self.search)
        actions.addStretch(); box.addLayout(actions)
        self.search_note = QLabel(); self.search_note.setWordWrap(True); self.search_note.setTextFormat(Qt.TextFormat.PlainText)
        box.addWidget(self.search_note)
        self.choice_rows = []
        for index in range(2):
            row = QWidget(); content = QHBoxLayout(row); content.setContentsMargins(0, 0, 0, 0)
            label = QLabel(); label.setWordWrap(True); label.setTextFormat(Qt.TextFormat.PlainText); content.addWidget(label, 1)
            button = QPushButton(); button.setObjectName('tryCrowdingCue'); button.setAutoDefault(False)
            button.setToolTip('Put this measured entrance in the draft. Keeps the End, overlap length, pitch and other settings. Clears the drums-only intro label. Compare current / draft before applying; listen for words, phrases and key clashes too. Apply is one undoable edit.')
            button.setAccessibleDescription(button.toolTip())
            button.clicked.connect(lambda checked=False, i=index: self.try_entrance(i)); content.addWidget(button)
            self.choice_rows.append((row, label, button)); box.addWidget(row)
        for button in (self.check, self.listen, self.shorter, self.search):
            button.setAutoDefault(False); button.setAccessibleDescription(button.toolTip())
        self.clear()

    def clear(self):
        self.result = None
        self.search_result = None
        self.summary.setText('Musical crowding not checked. Two melodies can compete even when their keys fit.')
        self.refresh()

    def refresh(self):
        flagged = bool(self.result and self.result['state'] == 'possible_crowding')
        self.listen.setVisible(flagged)
        self.search.setVisible(flagged)
        bars = shorter_choice(self.editor.session, self.editor.index) if flagged else None
        self.shorter.setVisible(bars is not None)
        if bars is not None:
            self.shorter.setText(f'Try shorter overlap · {bars} bars')
        self.search_note.setVisible(self.search_result is not None)
        if self.search_result:
            self.search_note.setText(self.search_result['reason'])
        choices = self.search_result['choices'] if self.search_result else []
        for index, (row, label, button) in enumerate(self.choice_rows):
            row.setVisible(index < len(choices))
            if index < len(choices):
                option = choices[index]
                direction = 'Omits' if option['source_delta'] > 0 else 'Restores'
                label.setText(f'Flagged activity: about {option["before_seconds"]:.1f} → {option["seconds"]:.1f} seconds at the same overlap length. '
                              f'{direction} {abs(option["source_delta"]):.1f} source seconds at the beginning; '
                              f'the mix becomes {abs(option["mix_delta"]):.1f} seconds {"longer" if option["mix_delta"] > 0 else "shorter"}.')
                button.setText('Try incoming start ' + clock(option['time'], True))
        self.setEnabled(not self.editor.busy)

    def check_audio(self):
        e = self.editor
        if e.busy: return
        snapshot = copy.deepcopy(e.session)
        self.search_result = None
        e.player.pause(); e.set_busy(True)
        e.status.setText('Checking whether moving musical parts overlap…')

        def work(progress, emit):
            paths = [snapshot.tracks[c.track_id].path for c in snapshot.clips[e.index - 1:e.index + 1]]
            before = [source_signature(p) for p in paths]
            report = prepare_blend(snapshot, e.index, e.cache_dir, progress)
            result = analyze_crowding(snapshot, e.index, report, e.cache_dir, progress)
            if before != [source_signature(p) for p in paths]:
                raise djmix.MixError('A source changed during the check. Reopen this blend.')
            return result

        def finished(result):
            e.set_busy(False)
            if e.closing_after_job:
                e.reject(); return
            if e.session.to_dict() != snapshot.to_dict():
                self.clear(); e.status.setText('The draft changed. Check musical crowding again.'); return
            self.result = result; self.summary.setText(crowding_advice(result)); self.refresh()
            e.status.setText('Crowding check ready. Listen to decide; no mix settings changed.')

        def failed(message):
            self.clear()
            self.summary.setText('Musical crowding could not be checked. You can retry or listen to judge.')
            e.failed(message)

        e.cancel = e.start_job(work, finished, rendering=True, cancellable=True,
                               failed_callback=failed, progress_callback=e.status.setText)

    def listen_here(self):
        e = self.editor
        if e.busy or not self.result: return
        part = next((p for p in self.result['parts'] if p['flagged']), None)
        if part is None: return
        from studio_tempo_curve import preview_overlap
        offset = preview_overlap(e.session, e.index, e.start / djmix.SR)[0] / djmix.SR
        e.seek_to(round(max(0, offset + part['start'] - 2) * 1000))
        if e.ready: e.player.play()
        else: e.toggle()

    def try_shorter(self):
        e = self.editor
        if e.busy or not self.result or self.result['state'] != 'possible_crowding': return
        bars = shorter_choice(e.session, e.index)
        if bars is None: return
        e.length.setValue(bars)
        e.status.setText('Shorter overlap in the draft. Its crowding has not been checked yet. Compare current / draft, then apply only if you prefer it.')

    def find_entrances(self):
        e = self.editor
        if e.busy or not self.result or self.result['state'] != 'possible_crowding': return
        snapshot = copy.deepcopy(e.session)
        e.player.pause(); e.set_busy(True)
        e.status.setText('Comparing nearby entrances. Your current mix stays unchanged…')
        def work(progress, emit):
            return search_cues(snapshot, e.index, e.cache_dir, progress)
        def finished(result):
            e.set_busy(False)
            if e.closing_after_job:
                e.reject(); return
            try:
                if result.get('fingerprint') and result['fingerprint'] != choice_fingerprint(e.session, e.index):
                    raise djmix.MixError('The draft or source changed. Search entrances again.')
                self.search_result = result
                if result.get('baseline'):
                    self.result = result['baseline']; self.summary.setText(crowding_advice(self.result))
                self.refresh()
                e.status.setText(f'Checked {result["checked"]} nearby entrances. Try a suggestion as a draft, then compare by listening.')
            except (OSError, djmix.MixError) as error:
                self.clear(); e.status.setText(str(error))
        def failed(message):
            self.clear(); e.failed(message)
        e.cancel = e.start_job(work, finished, rendering=True, cancellable=True,
                               failed_callback=failed, progress_callback=e.status.setText)

    def try_entrance(self, index):
        e = self.editor
        if e.busy or not self.search_result or not 0 <= index < len(self.search_result['choices']): return
        option = self.search_result['choices'][index]
        try:
            if (self.search_result.get('fingerprint') != choice_fingerprint(e.session, e.index)
                    or option['time'] not in cue_candidates(e.session, e.index)):
                raise djmix.MixError('This entrance no longer matches the draft, source or bar grid. Search again.')
            candidate = draft_for_cue(e.session, e.index, option['time'])
            voices = overlap_voices(e.session, e.index)
            if (voices['state'] != 'not_checked'
                    and overlap_voices(candidate, e.index)['seconds'] > voices['seconds'] + .05):
                raise djmix.MixError('New voice observations show more voices together at this entrance. Search again or choose your own cue.')
        except (OSError, djmix.MixError) as error:
            self.clear(); e.status.setText(str(error)); return
        e.session.clips[e.index].cue_in = candidate.clips[e.index].cue_in
        e.session.clips[e.index].intro_percussive = False
        e.change_draft()
        self.result = option['analysis']; self.summary.setText(crowding_advice(self.result)); self.refresh()
        e.status.setText('Measured entrance in the draft; drums-only intro label cleared. Compare current / draft before applying. Less flagged activity does not guarantee a better blend.')
