"""Review tentative vocal and drum-led passages before changing the mix."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from studio_content import current_content, cue_candidates, drum_regions, voice_regions
from studio_widgets import clock


class VocalSuggestions(QWidget):
    """Listening candidates inside Protect passages; selecting only fills a form."""
    def __init__(self, review, choose):
        super().__init__(review)
        self.review, self.choose = review, choose
        self.limit, self.last_key = 40, None
        self.box = QVBoxLayout(self); self.box.setContentsMargins(0, 6, 0, 0)
        self.summary = QLabel(); self.summary.setWordWrap(True); self.box.addWidget(self.summary)
        self.body = QWidget(); self.box.addWidget(self.body)

    def refresh(self):
        r = self.review; result = current_content(r.track)
        available = bool(getattr(r, 'output_available', False))
        key = (id(result) if result else None, r.clip.cue_in, r.clip.cue_out, available, self.limit)
        if key == self.last_key: return
        self.last_key = key
        regions = [v for v in voice_regions(result) if v['end'] > r.clip.cue_in and v['start'] < r.clip.cue_out]
        self.summary.setText('Possible vocals: not checked yet. Use Find possible vocals, or mark a range yourself.' if not result else
                             f'{len(regions)} possible vocal {"passage" if len(regions) == 1 else "passages"} in the kept audio. Ranges are approximate; listen to confirm and adjust them.' if regions else
                             'No possible vocals flagged in the kept audio. This does not prove it is vocal-free. Listen and mark passages manually.')
        content = QWidget(); column = QVBoxLayout(content); column.setContentsMargins(0, 0, 0, 0)
        for candidate in regions[:self.limit]:
            title = f'{clock(candidate["start"], True)} – {clock(candidate["end"], True)}'
            row = QHBoxLayout()
            count = candidate['observations']
            label = QLabel(f'{title} · Possible voice\n{count} positive {"check" if count == 1 else "checks"}. '
                           'Voice-like sound in a ten-second scan window; not a precise vocal boundary or an accuracy score.')
            label.setWordWrap(True); label.setTextFormat(Qt.TextFormat.PlainText); row.addWidget(label, 1)
            listen = QPushButton('Listen here'); listen.setObjectName('listenVoice'); listen.setEnabled(available)
            listen.setAccessibleName('Listen to possible vocals at ' + title)
            listen.setToolTip('Play the original song from this range’s start. Playback continues afterward; Pause stops it. Listen for actual vocal boundaries.')
            listen.clicked.connect(lambda checked=False, at=candidate['start']: r.audition.listen_at(at))
            choose = QPushButton('Use this range'); choose.setObjectName('useVoiceRange')
            choose.setAccessibleName('Review possible vocal range ' + title)
            choose.setToolTip('Fill From, Through and the Vocal label above. Review the range, choose Protect this passage, then Apply changes to keep it. Nothing is protected by this button alone.')
            choose.clicked.connect(lambda checked=False, c=candidate: self.choose(c))
            for button in (listen, choose):
                button.setAutoDefault(False); button.setAccessibleDescription(button.toolTip()); row.addWidget(button)
            column.addLayout(row)
            if candidate['start'] < r.clip.cue_in or candidate['end'] > r.clip.cue_out:
                note = QLabel('Part of this suggested range is outside Start/End. Keep the whole passage, or review a shorter range before protecting it.')
                note.setWordWrap(True); column.addWidget(note)
        if len(regions) > self.limit:
            more = QPushButton(f'Show more vocal suggestions ({len(regions) - self.limit} remaining)')
            more.setAutoDefault(False); more.clicked.connect(self.show_more); column.addWidget(more)
        self.box.removeWidget(self.body)
        self.body.hide(); self.body.setParent(None); self.body.deleteLater()
        self.body = content; self.box.addWidget(content)

    def show_more(self):
        self.limit += 40; self.refresh()


class PassageSuggestions(QWidget):
    def __init__(self, review):
        super().__init__(review)
        self.review = review
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        explanation = QLabel('Look for passages where drums stand out and voices or instruments appear less prominent. '
                             'These are listening suggestions. Pitched bass, melodies, quiet voices and tails may remain.')
        explanation.setWordWrap(True); row.addWidget(explanation, 1)
        self.find = QPushButton('Find drum-led passages'); self.find.setAutoDefault(False)
        self.find.setToolTip('Analyze this source song locally with the bundled passage detector. '
                             'Reuses unchanged observations. Does not change cues, pitch, BPM or key warnings.')
        self.find.clicked.connect(review.analyze_passages); row.addWidget(self.find)
        box.addLayout(row)
        self.summary = QLabel(); self.summary.setWordWrap(True); box.addWidget(self.summary)
        self.rows = QScrollArea(); self.rows.setWidgetResizable(True); box.addWidget(self.rows, 1)

    def refresh(self):
        r = self.review; result = current_content(r.track)
        regions = drum_regions(result); candidates = cue_candidates(r.session, r.index, result) if result else []
        self.summary.setText('Not checked yet. Find passages, listen, then try a cue as a draft.' if not result else
                             f'{len(regions)} drum-led candidate passages. Consistent observations are evidence, not an accuracy score.' if regions else
                             'No consistent drum-led passage found. This does not prove there are none. Keep your cues or listen manually.')
        content = QWidget(); column = QVBoxLayout(content)
        for region in regions:
            row = QHBoxLayout()
            text = QLabel(f'{clock(region["start"], True)} – {clock(region["end"], True)} · Drum-led candidate\n'
                          f'{region["observations"]} overlapping checks support prominent drums, with weaker voice and instrument signals. '
                          'Uncertain whether it is drums only; listen before mixing across unrelated keys.')
            text.setWordWrap(True); text.setTextFormat(Qt.TextFormat.PlainText); row.addWidget(text, 1)
            listen = QPushButton('Listen here'); listen.setObjectName('listenPassage'); listen.setAutoDefault(False)
            listen.setEnabled(getattr(r, 'output_available', False))
            listen.setToolTip('Play the original song from this passage. Playback continues after the passage; Pause stops it. No cues change.')
            listen.clicked.connect(lambda checked=False, at=region['start']: r.audition.listen_at(at)); row.addWidget(listen)
            column.addLayout(row)
            choices = [c for c in candidates if c['start'] == region['start'] and c['end'] == region['end']]
            for candidate in choices:
                text = f'Try {candidate["kind"].title()} at {clock(candidate["time"], True)}'
                if candidate['overlap']: text += f' · {candidate["overlap"]}-bar blend'
                choose = QPushButton(text); choose.setAutoDefault(False)
                choose.setToolTip(f'Put this cue and overlap in the draft. Omits {candidate["removed_seconds"]:.1f} source seconds. '
                                  'Uses your detected or manual bar grid. Clears any previous drums-only label for this passage. '
                                  'Apply changes is required; one Undo restores the mix.')
                choose.clicked.connect(lambda checked=False, c=candidate: r.try_passage(c)); column.addWidget(choose)
            if not choices:
                note = QLabel('No cue fits the current bar grid, song position and protected passages. '
                              'Analyze song or check beats/BPM, then review the cues and neighboring blends.')
                note.setWordWrap(True); column.addWidget(note)
        column.addStretch(1); self.rows.setWidget(content)
        for control in self.findChildren(QWidget):
            if control.toolTip(): control.setAccessibleDescription(control.toolTip())
