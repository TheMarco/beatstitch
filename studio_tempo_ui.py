"""Native, reviewable set-tempo controls; no changes until Apply."""
import copy
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

import djmix
import studio_theme as T
from studio_engine import layout
from studio_tempo_curve import session_curve
from studio_widgets import clock


class TempoGraph(QWidget):
    def __init__(self):
        super().__init__(); self.curve = None; self.beats = 4
        self.setMinimumHeight(140)
        self.setAccessibleName('Set tempo curve')

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(T.BASE))
        if self.curve is None: return
        points = self.curve.points
        low, high = min(p['bpm'] for p in points) - 2, max(p['bpm'] for p in points) + 2
        rect = QRectF(58, 14, max(1, self.width() - 80), max(1, self.height() - 44))
        def xy(beat, bpm):
            return QPointF(rect.left() + beat / self.beats * rect.width(),
                           rect.bottom() - (bpm - low) / (high - low) * rect.height())
        painter.setPen(QColor(T.TEXT_2)); painter.setFont(T.font(11))
        painter.drawText(QRectF(0, 6, 52, 20), Qt.AlignmentFlag.AlignRight, f'{high:g}')
        painter.drawText(QRectF(0, rect.bottom() - 10, 52, 20), Qt.AlignmentFlag.AlignRight, f'{low:g}')
        painter.drawText(QRectF(rect.left(), rect.bottom() + 8, 70, 22), 'Bar 1')
        painter.drawText(QRectF(rect.right() - 110, rect.bottom() + 8, 110, 22),
                         Qt.AlignmentFlag.AlignRight, 'Bar ' + f'{1 + self.beats / 4:.2f}'.rstrip('0').rstrip('.'))
        visible = [(p['beat'], p['bpm']) for p in points if p['beat'] <= self.beats]
        visible.append((self.beats, float(self.curve.bpm_at(self.beats * 60 / self.curve.reference_bpm))))
        painter.setPen(QPen(QColor(T.ACCENT), 2))
        painter.drawPolyline(QPolygonF([xy(*p) for p in visible]))
        painter.setBrush(QColor(T.ACCENT))
        for point in visible[:-1]: painter.drawEllipse(xy(*point), 3, 3)


class TempoDialog(QDialog):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Set tempo curve'); self.resize(560, 580)
        self.draft = copy.deepcopy(session); self.busy = False
        positions = layout(session)
        self.end = positions[-1]['end'] / djmix.SR if positions else 0
        self.beats = max(4., self.end * session.target_bpm / 60)
        root = QVBoxLayout(self)
        intro = QLabel('Let the mix gradually speed up or slow down. Both songs follow the same tempo during a blend; keys stay unchanged unless you choose a pitch shift.')
        intro.setWordWrap(True); root.addWidget(intro)
        self.enabled = QCheckBox('Use gradual tempo changes'); self.enabled.setChecked(session.tempo_enabled)
        self.enabled.setToolTip('Off uses the main project BPM. Your curve points are remembered. This is separate from correcting drift inside a song.')
        root.addWidget(self.enabled)
        self.graph = TempoGraph(); self.graph.beats = self.beats; root.addWidget(self.graph)
        self.rows = []
        self.points = QWidget(); self.point_rows = QVBoxLayout(self.points)
        self.point_rows.setContentsMargins(0, 0, 0, 0); self.point_rows.addStretch(1)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setWidget(self.points)
        self.scroll.setMinimumHeight(140)
        self.scroll.setAccessibleName('Tempo points')
        self.scroll.setToolTip('The first point is at bar 1. BPM changes gradually between points and holds steady after the last point. Positions stay at their musical bars when you add or reorder songs.')
        root.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        self.add = QPushButton('Add point')
        self.add.setToolTip('Add a point 16 bars after the last one, initially at the same BPM.')
        row.addWidget(self.add); row.addStretch(1); root.addLayout(row)
        self.message = QLabel(); self.message.setWordWrap(True); root.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Help)
        self.apply = buttons.button(QDialogButtonBox.StandardButton.Apply); self.apply.setText('Apply tempo settings')
        self.apply.setToolTip('Use these settings in previews and exports. One Undo restores the previous tempo settings.')
        self.apply.clicked.connect(self.accept); buttons.rejected.connect(self.reject)
        buttons.helpRequested.connect(lambda: parent.show_help('tempo-curve') if parent else None)
        root.addWidget(buttons)
        points = session.tempo_points or [{'beat': 0, 'bpm': session.target_bpm},
                                        {'beat': max(4, math.floor(self.beats / 4) * 4), 'bpm': session.target_bpm}]
        self.busy = True
        for point in points: self.add_row(point['beat'] / 4 + 1, point['bpm'])
        self.busy = False
        self.add.clicked.connect(self.add_point)
        self.enabled.toggled.connect(self.refresh)
        for button in self.findChildren(QPushButton): button.setAutoDefault(False)
        self.refresh()

    def add_row(self, bar, bpm):
        row = len(self.rows)
        widget = QWidget(); line = QHBoxLayout(widget); line.setContentsMargins(6, 4, 6, 4)
        fields = {'widget': widget}
        for column, value, low, high, decimals in [(0, bar, 1, 2500001, 2), (1, bpm, 20, 300, 2)]:
            box = QDoubleSpinBox(); box.setRange(low, high); box.setDecimals(decimals); box.setValue(value)
            box.setSingleStep(1); box.setMinimumHeight(32)
            box.setAccessibleName(f'Tempo point {row + 1} ' + ('bar' if column == 0 else 'BPM'))
            box.setToolTip('Bar 1 starts the mix. Later bars must increase.' if column == 0 else 'Target tempo here. Small gradual changes usually sound more natural. Listen before exporting.')
            if row == 0 and column == 0: box.setReadOnly(True); box.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
            name = 'bar' if column == 0 else 'bpm'
            title = QLabel('Bar' if column == 0 else 'BPM'); title.setBuddy(box)
            line.addWidget(title); line.addWidget(box, 1); fields[name] = box
            box.valueChanged.connect(self.refresh)
        remove = QPushButton('Remove'); remove.setAutoDefault(False); remove.setEnabled(row > 0)
        remove.setToolTip('Remove this point. The first point must stay at bar 1.')
        remove.clicked.connect(lambda: self.remove_point(fields))
        line.addWidget(remove); fields['remove'] = remove
        self.rows.append(fields); self.point_rows.insertWidget(row, widget)

    def add_point(self):
        self.busy = True
        self.add_row(self.rows[-1]['bar'].value() + 16, self.rows[-1]['bpm'].value())
        self.busy = False; self.refresh()
        self.rows[-1]['bar'].setFocus(); self.rows[-1]['bar'].selectAll()
        self.scroll.ensureWidgetVisible(self.rows[-1]['widget'])

    def remove_point(self, fields):
        if fields in self.rows[1:]:
            self.rows.remove(fields); self.point_rows.removeWidget(fields['widget']); fields['widget'].deleteLater()
        self.refresh()

    def refresh(self, *_):
        if self.busy: return
        self.draft.tempo_enabled = self.enabled.isChecked()
        self.draft.tempo_points = [{'beat': (r['bar'].value() - 1) * 4, 'bpm': r['bpm'].value()} for r in self.rows]
        self.points.setEnabled(self.draft.tempo_enabled)
        self.add.setEnabled(self.draft.tempo_enabled and len(self.rows) < 256)
        for i, fields in enumerate(self.rows):
            fields['bar'].setAccessibleName(f'Tempo point {i + 1} bar')
            fields['bpm'].setAccessibleName(f'Tempo point {i + 1} BPM')
            fields['remove'].setAccessibleName(f'Remove tempo point {i + 1}')
        try:
            for i in range(1, len(self.rows)):
                previous = self.rows[i - 1]['bar'].value()
                if self.rows[i]['bar'].value() <= previous:
                    raise djmix.MixError(f'Point {i + 1} needs a later bar than point {i} (bar {previous:g}). Increase its bar number or remove it.')
            curve = session_curve(self.draft)
            self.graph.curve = curve; self.graph.update(); self.apply.setEnabled(True)
            duration = float(curve.to_output(self.end))
            text = (f'Mix length: {clock(duration)}. Changes apply only when you press Apply. Listen to the mix afterward.'
                    if self.draft.tempo_enabled else f'Fixed tempo: {self.draft.target_bpm:g} BPM. Your curve is remembered.')
            if self.draft.tempo_enabled and all(p['bpm'] == curve.points[0]['bpm'] for p in curve.points):
                text = (f"Steady at {curve.points[0]['bpm']:g} BPM: all points have the same tempo. "
                        'Choose different BPM values for a gradual change. ' + text)
            if self.draft.tempo_enabled and any(p['beat'] > self.beats for p in curve.points):
                text += ' Points after the current ending still shape the ramp; they remain available if you add songs.'
            self.message.setText(text)
            self.graph.setAccessibleDescription(text + ' Points: ' + ', '.join(f"bar {p['beat'] / 4 + 1:g}: {p['bpm']:g} BPM" for p in curve.points))
        except djmix.MixError as error:
            self.message.setText(str(error)); self.apply.setEnabled(False)
