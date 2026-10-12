"""Native listening review for experimental EDMFormer section predictions.

Usage: .venv/bin/python prototype_section_review.py path/to/sections.json
Reviews are saved alongside predictions; no DJ project is opened or modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QHBoxLayout,
                              QHeaderView, QLabel, QPushButton, QTableWidget,
                              QTableWidgetItem, QVBoxLayout, QWidget, QSlider)

import djmix
from studio_cache import source_signature
from studio_engine import Track
from studio_listening import SourceAudition
import studio_theme as T
from studio_waveform import BINS_PER_SECOND, band_envelope
from studio_widgets import band_columns, clock, paint_bands

LABELS = {'intro': 'Intro', 'buildup': 'Build', 'drop': 'Drop',
          'breakdown': 'Breakdown', 'outro': 'Outro', 'silence': 'Silence'}
RATINGS = {'yes': 'Sounds right', 'no': 'Needs correction', 'unsure': 'Not sure'}


def review_regions(sections, duration):
    """Combine touching equal labels without bridging gaps or inventing ranges."""
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid source duration.')
    regions, previous_end = [], 0.0
    for item in sections:
        start, end, kind = float(item['start']), float(item['end']), item['label']
        if (kind not in LABELS or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start < end <= duration + .15 or start < previous_end - 1e-6):
            raise ValueError('Invalid or overlapping section predictions.')
        # Model frames can extend fractionally past the decoded recording.
        end = min(end, duration)
        if end <= start:
            raise ValueError('Section starts after the recording ends.')
        if regions and kind == regions[-1]['label'] and abs(start - previous_end) < 1e-6:
            regions[-1]['end'] = end
        else:
            regions.append({'label': kind, 'start': start, 'end': end})
        previous_end = end
    return regions


class SectionWave(QWidget):
    seek = Signal(float)

    def __init__(self, bands, duration, regions, parent=None):
        super().__init__(parent)
        self.bands, self.duration, self.regions = bands, duration, regions
        self.selected, self.position = 0, 0.0
        self.setMinimumHeight(130)
        self.setAccessibleName('Original song waveform and suggested sections')
        self.setToolTip('Real source waveform. The highlighted range is the selected suggestion. Click to listen at a time; use the section list for keyboard navigation.')

    def paintEvent(self, event):
        p = QPainter(self); p.fillRect(self.rect(), QColor(T.FIELD))
        area = QRectF(0, 28, self.width(), self.height() - 51)
        columns = band_columns(self.bands, 0, self.duration * BINS_PER_SECOND, max(1, self.width()))
        if columns is not None:
            paint_bands(p, area, columns, T.clip_bands(T.clip_color('section-review')))
        p.setFont(T.font(11))
        for i, region in enumerate(self.regions):
            left, right = [t / self.duration * self.width() for t in (region['start'], region['end'])]
            if i == self.selected:
                p.fillRect(QRectF(left, 0, right - left, self.height()), QColor(255, 193, 82, 45))
            p.setPen(QColor(T.TEXT_3)); p.drawLine(round(left), 22, round(left), self.height() - 23)
            text = p.fontMetrics().elidedText(LABELS[region['label']], Qt.TextElideMode.ElideRight, max(0, round(right-left-8)))
            p.drawText(QRectF(left+4, 0, right-left-8, 23), Qt.AlignmentFlag.AlignVCenter, text)
        p.setPen(QColor(T.TEXT_2))
        for t in range(0, math.ceil(self.duration), 30):
            x = t / self.duration * self.width()
            if x < self.width() - 35:
                p.drawText(QRectF(x+2, self.height()-22, 50, 20), clock(t))
        p.setPen(QPen(QColor(T.TEXT), 2))
        x = round(self.position / self.duration * self.width())
        p.drawLine(x, 22, x, self.height()-23)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.seek.emit(max(0, min(self.duration, event.position().x()/max(1, self.width())*self.duration)))


class SectionReview(QDialog):
    def __init__(self, report_file, bands=None):
        super().__init__()
        self.report_file = Path(report_file).resolve()
        self.report = json.loads(self.report_file.read_text())
        self.signature = self.report['source']
        self.source = Path(self.signature[0])
        if source_signature(self.source) != self.signature:
            raise ValueError('The song has changed since analysis. Run the trial again before reviewing it.')
        duration = djmix.probe(self.source)['duration']
        self.regions = review_regions(self.report['sections'], duration)
        self.key = hashlib.sha256(json.dumps([self.signature, self.regions], sort_keys=True).encode()).hexdigest()
        self.feedback_file = self.report_file.with_name('listening-review.json')
        self.feedback = {}
        if self.feedback_file.is_file():
            saved = json.loads(self.feedback_file.read_text())
            if saved.get('prediction_key') == self.key:
                self.feedback = {k: v for k, v in saved.get('ratings', {}).items() if v in RATINGS}
        self.setWindowTitle('DJ Studio · Build & drop listening trial')
        self.setMinimumSize(750, 650); self.resize(950, 760)
        box = QVBoxLayout(self); box.setContentsMargins(18, 18, 18, 18); box.setSpacing(10)
        heading = QLabel('Build & drop listening trial'); heading.setProperty('role', 'title'); box.addWidget(heading)
        note = QLabel('These are experimental suggestions. Listen for rising tension in a build, then the main beat and bass arriving at a drop. '
                      'Choose a row to inspect it. Your ratings are saved separately from the mix.')
        note.setWordWrap(True); box.addWidget(note)
        if bands is None:
            bands = band_envelope(self.source, self.report_file.parent, 'review')
        self.wave = SectionWave(bands, duration, self.regions); box.addWidget(self.wave)
        self.table = QTableWidget(len(self.regions), 4)
        self.table.setHorizontalHeaderLabels(['Suggestion', 'From', 'Through', 'Your review'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setAccessibleName('Suggested song sections')
        self.table.setToolTip('Select a suggested section, then hear its beginning and ending. A rating covers both its label and its approximate range.')
        for row, region in enumerate(self.regions):
            values = [LABELS[region['label']], clock(region['start'], True), clock(region['end'], True),
                      RATINGS.get(self.feedback.get(str(row)), 'Not reviewed')]
            for col, value in enumerate(values): self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.currentCellChanged.connect(self.selected)
        box.addWidget(self.table, 1)
        self.summary = QLabel(); self.summary.setWordWrap(True); box.addWidget(self.summary)
        row = QHBoxLayout()
        self.begin = QPushButton('Hear beginning'); self.end = QPushButton('Hear ending')
        self.begin.setToolTip('Listen from four seconds before the selected section begins. Playback continues until you pause.')
        self.end.setToolTip('Listen from four seconds before the selected section ends. Playback continues until you pause.')
        self.begin.clicked.connect(lambda: self.listen('start')); self.end.clicked.connect(lambda: self.listen('end'))
        row.addWidget(self.begin); row.addWidget(self.end); row.addStretch(); box.addLayout(row)
        self.audition = SourceAudition()
        self.audition.output.setVolume(.7)
        self.audition.set_track(Track('section-review', str(self.source), self.source.stem, duration))
        self.audition.failed.connect(self.show_error)
        self.wave.seek.connect(self.audition.listen_at)
        self.audition.player.positionChanged.connect(self.position_changed)
        box.addWidget(self.audition)
        self.devices = QMediaDevices(self)
        self.devices.audioOutputsChanged.connect(self.refresh_output)
        output_row = QHBoxLayout()
        self.output_label = QLabel(); output_row.addWidget(self.output_label, 1)
        output_row.addWidget(QLabel('Volume'))
        self.volume = QSlider(Qt.Orientation.Horizontal); self.volume.setRange(0, 100); self.volume.setValue(70)
        self.volume.setMaximumWidth(140); self.volume.setAccessibleName('Listening trial volume')
        self.volume.setToolTip('Adjust playback volume for this trial. Does not change the audio or your mix volume.')
        self.volume.valueChanged.connect(lambda value: self.audition.output.setVolume(value / 100))
        output_row.addWidget(self.volume); box.addLayout(output_row)
        row = QHBoxLayout(); row.addWidget(QLabel('Does the selected suggestion fit?'))
        self.rating_buttons = []
        for key, title in RATINGS.items():
            button = QPushButton(title); button.clicked.connect(lambda checked=False, k=key: self.rate(k))
            button.setToolTip('Save your listening assessment for this label and approximate range. Does not change audio, cues or protection.')
            row.addWidget(button); self.rating_buttons.append(button)
        box.addLayout(row)
        self.status = QLabel('Nothing has been rated yet.' if not self.feedback else f'{len(self.feedback)} ratings loaded.')
        self.status.setWordWrap(True); box.addWidget(self.status)
        for widget in self.findChildren(QWidget):
            if widget.toolTip(): widget.setAccessibleDescription(widget.toolTip())
            if isinstance(widget, QPushButton): widget.setAutoDefault(False)
        self.table.selectRow(0)
        self.refresh_output()

    def show_error(self, message):
        self.status.setText(message)

    def position_changed(self, ms):
        self.wave.position = ms/1000; self.wave.update()

    def selected(self, row, *_):
        valid = 0 <= row < len(self.regions)
        self.wave.selected = row; self.wave.update()
        if not valid:
            self.summary.setText('Select a section to listen and review.'); return
        r = self.regions[row]
        self.summary.setText(f'{LABELS[r["label"]]} candidate · {clock(r["start"], True)} – {clock(r["end"], True)} · '
                             'Listen across both boundaries before rating it.')

    def listen(self, boundary):
        row = self.table.currentRow()
        if 0 <= row < len(self.regions) and not QMediaDevices.defaultAudioOutput().isNull():
            self.audition.listen_at(max(0, self.regions[row][boundary] - 4))

    def refresh_output(self, *_):
        device = QMediaDevices.defaultAudioOutput()
        available = not device.isNull()
        if not available: self.audition.stop()
        self.audition.output.setDevice(device); self.audition.setEnabled(available)
        self.begin.setEnabled(available); self.end.setEnabled(available); self.wave.setEnabled(available)
        self.output_label.setText('System output · ' + device.description() if available else 'No audio output. Connect headphones or speakers to listen.')

    def rate(self, rating):
        row = self.table.currentRow()
        if not 0 <= row < len(self.regions): return
        try:
            unchanged = source_signature(self.source) == self.signature
        except OSError:
            unchanged = False
        if not unchanged:
            self.status.setText('The source has changed. Run analysis again before reviewing it.'); return
        feedback = {**self.feedback, str(row): rating}
        value = {'prediction_key': self.key, 'source': self.signature, 'regions': self.regions, 'ratings': feedback}
        temporary = self.feedback_file.with_suffix('.pending')
        try:
            temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
            os.replace(temporary, self.feedback_file)
        except OSError as error:
            self.status.setText('Could not save this rating: ' + str(error)); return
        self.feedback = feedback
        self.table.item(row, 3).setText(RATINGS[rating])
        self.status.setText(f'Saved · {len(self.feedback)} of {len(self.regions)} suggestions reviewed. Your mix is unchanged.')

    def closeEvent(self, event):
        self.audition.stop(); super().closeEvent(event)

    def done(self, result):
        self.audition.stop(); super().done(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('report', type=Path, nargs='?')
    args = parser.parse_args()
    if args.report is None:
        root = Path(__file__).resolve().parent / 'outputs/edmformer-research/results'
        candidates = list(root.glob('*/sections.json'))
        if not candidates:
            parser.error('No experimental section results found. Run prototype_edm_sections.py on a song first.')
        args.report = max(candidates, key=lambda p: p.stat().st_mtime_ns)
    app = QApplication(sys.argv); app.setApplicationName('DJ Section Review'); app.setStyleSheet(T.stylesheet())
    window = SectionReview(args.report); window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
