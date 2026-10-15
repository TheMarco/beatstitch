"""Standalone native fade-controls widget (PySide6).

Parent owns authoritative gain curves and timeline preview; this panel only
edits linked/unlinked fade lengths and curve shapes within an overlap.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

_CURVES = (
    ("Smooth (equal power)", "smooth"),
    ("Linear", "linear"),
    ("Fast (exponential)", "fast"),
    ("Slow (exponential)", "slow"),
)
_CURVE_VALUES = tuple(value for _, value in _CURVES)


class _ResetButton(QPushButton):
    """Button that is also callable, so panel.reset() and panel.reset.click() both work."""

    def __call__(self):
        self.click()


class FadesPanel(QWidget):
    """Compact fade controls for one overlap."""

    changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._overlap_bars = 8.0
        self._updating = False

        self.setAccessibleName("Fade controls")
        self.setToolTip("Fade lengths and shapes for the overlap between two songs.")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Link + reset row
        top = QHBoxLayout()
        top.setSpacing(8)
        self._link_checkbox = QCheckBox("Link fades", self)
        self._link_checkbox.setChecked(True)
        self._link_checkbox.setAccessibleName("Link fades")
        self._link_checkbox.setToolTip(
            "Link fades so the outgoing and incoming fades share the same length and shape. "
            "Editing either row updates the other. Turn this off to set them independently."
        )
        top.addWidget(self._link_checkbox)

        self.reset = _ResetButton("Reset fades", self)
        self.reset.setAccessibleName("Reset fades")
        self.reset.setToolTip(
            "Reset fades: link the fades, set both lengths to the full overlap, and use Smooth (equal power) shapes."
        )
        top.addStretch(1)
        top.addWidget(self.reset)
        # Alias for integration code that prefers an explicit name.
        self.reset_button = self.reset
        root.addLayout(top)

        # Fade rows in a compact grid
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 1)

        out_label = QLabel("Outgoing fade", self)
        in_label = QLabel("Incoming fade", self)

        self._out_spin = self._make_spin("out")
        self._in_spin = self._make_spin("in")
        self._out_combo = self._make_combo("out")
        self._in_combo = self._make_combo("in")

        out_label.setBuddy(self._out_spin)
        in_label.setBuddy(self._in_spin)

        grid.addWidget(out_label, 0, 0)
        grid.addWidget(self._out_spin, 0, 1)
        grid.addWidget(self._out_combo, 0, 2)
        grid.addWidget(in_label, 1, 0)
        grid.addWidget(self._in_spin, 1, 1)
        grid.addWidget(self._in_combo, 1, 2)
        root.addLayout(grid)

        # Discoverable widget aliases
        self.out_spin = self._out_spin
        self.in_spin = self._in_spin
        self.out_combo = self._out_combo
        self.in_combo = self._in_combo
        self.outgoing_spin = self._out_spin
        self.incoming_spin = self._in_spin
        self.outgoing_combo = self._out_combo
        self.incoming_combo = self._in_combo
        self.link_checkbox = self._link_checkbox
        self.link_fades_checkbox = self._link_checkbox

        # Hints
        hints = (
            "Overlap is how long both songs play together. Incoming fades up from its start; "
            "outgoing fades down into its end. Unlink to edit them separately. "
            "Drag the white timeline fade handles to adjust lengths.",
            "With bass swap on, bass keeps its separate middle handoff. Turn it off to fade the whole sound.",
        )
        for text in hints:
            label = QLabel(text, self)
            label.setWordWrap(True)
            label.setProperty("role", "hint")
            label.setAccessibleName("Fade hint")
            root.addWidget(label)

        root.addStretch(1)
        self.setMaximumWidth(460)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        # Connections (after hints so findChildren order for spins/combos is stable)
        self._link_checkbox.toggled.connect(self._on_link_toggled)
        self._out_spin.valueChanged.connect(self._on_out_length_changed)
        self._in_spin.valueChanged.connect(self._on_in_length_changed)
        self._out_combo.currentIndexChanged.connect(self._on_out_curve_changed)
        self._in_combo.currentIndexChanged.connect(self._on_in_curve_changed)
        self.reset.clicked.connect(self._do_reset)

        # Default display: full overlap, smooth
        self._apply_values(overlap_bars=8.0, linked=True,
                           in_bars=None, out_bars=None,
                           in_curve="smooth", out_curve="smooth")

    # -- widget factories -------------------------------------------------
    def _make_spin(self, which: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(0.25, self._overlap_bars)
        spin.setSingleStep(0.25)
        spin.setDecimals(2)
        spin.setSuffix(" bars")
        spin.setValue(self._overlap_bars)
        spin.setKeyboardTracking(False)
        spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        spin.installEventFilter(self)
        if which == "out":
            spin.setAccessibleName("Outgoing fade length")
            spin.setToolTip(
                "Length of the outgoing fade in bars, from 0.25 bars up to the full overlap. "
                "The fade cannot be longer than the overlap."
            )
        else:
            spin.setAccessibleName("Incoming fade length")
            spin.setToolTip(
                "Length of the incoming fade in bars, from 0.25 bars up to the full overlap. "
                "The fade cannot be longer than the overlap."
            )
        return spin

    def _make_combo(self, which: str) -> QComboBox:
        combo = QComboBox(self)
        combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        combo.installEventFilter(self)
        for label, value in _CURVES:
            combo.addItem(label, value)
        combo.setCurrentIndex(0)
        if which == "out":
            combo.setAccessibleName("Outgoing fade shape")
            combo.setToolTip("Smooth uses an equal-power fade. Linear changes evenly. Fast drops volume early; Slow holds it longer.")
        else:
            combo.setAccessibleName("Incoming fade shape")
            combo.setToolTip("Smooth uses an equal-power fade. Linear changes evenly. Fast brings volume up early; Slow builds it later.")
        return combo

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and not watched.hasFocus():
            event.ignore()
            return True
        return super().eventFilter(watched, event)

    # -- public attributes --------------------------------------------------
    @property
    def linked(self) -> bool:
        return bool(self._link_checkbox.isChecked())

    @linked.setter
    def linked(self, value: bool):
        with QSignalBlocker(self._link_checkbox):
            self._link_checkbox.setChecked(bool(value))

    @property
    def in_length(self) -> float:
        return float(self._in_spin.value())

    @in_length.setter
    def in_length(self, value: float):
        with QSignalBlocker(self._in_spin):
            self._in_spin.setValue(self._clamp_length(value))

    @property
    def out_length(self) -> float:
        return float(self._out_spin.value())

    @out_length.setter
    def out_length(self, value: float):
        with QSignalBlocker(self._out_spin):
            self._out_spin.setValue(self._clamp_length(value))

    @property
    def in_curve(self) -> str:
        data = self._in_combo.currentData()
        return str(data) if data in _CURVE_VALUES else "smooth"

    @in_curve.setter
    def in_curve(self, value: str):
        self._set_combo_quiet(self._in_combo, value)

    @property
    def out_curve(self) -> str:
        data = self._out_combo.currentData()
        return str(data) if data in _CURVE_VALUES else "smooth"

    @out_curve.setter
    def out_curve(self, value: str):
        self._set_combo_quiet(self._out_combo, value)

    @property
    def overlap_bars(self) -> float:
        return float(self._overlap_bars)

    # -- public API ---------------------------------------------------------
    def set_values(self, overlap_bars, *, linked=True, in_bars=None, out_bars=None,
                   in_curve="smooth", out_curve="smooth"):
        """Set all values without emitting changed."""
        self._apply_values(overlap_bars=overlap_bars, linked=linked,
                           in_bars=in_bars, out_bars=out_bars,
                           in_curve=in_curve, out_curve=out_curve)

    def reset_fades(self):
        self._do_reset()

    # -- internals ----------------------------------------------------------
    def _clamp_length(self, value) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = float(self._overlap_bars)
        high = max(0.25, float(self._overlap_bars))
        return min(max(0.25, number), high)

    @staticmethod
    def _normalise_curve(value) -> str:
        return value if value in _CURVE_VALUES else "smooth"

    def _set_combo_quiet(self, combo: QComboBox, value: str):
        target = self._normalise_curve(value)
        index = combo.findData(target)
        if index < 0:
            index = 0
        with QSignalBlocker(combo):
            combo.setCurrentIndex(index)

    def _apply_values(self, *, overlap_bars, linked, in_bars, out_bars, in_curve, out_curve):
        try:
            overlap = float(overlap_bars)
        except (TypeError, ValueError):
            overlap = 8.0
        if overlap <= 0:
            overlap = 0.25
        self._overlap_bars = overlap
        high = max(0.25, overlap)
        # Effective: None => full overlap; otherwise clamp to overlap/range.
        effective_in = overlap if in_bars is None else self._clamp_length_for(in_bars, high)
        effective_out = overlap if out_bars is None else self._clamp_length_for(out_bars, high)
        # If overlap itself < 0.25, spin minimum would exceed it; clamp handles via high.
        effective_in = min(max(0.25, effective_in), high)
        effective_out = min(max(0.25, effective_out), high)

        self._updating = True
        try:
            blockers = [QSignalBlocker(w) for w in
                        (self._link_checkbox, self._in_spin, self._out_spin,
                         self._in_combo, self._out_combo)]
            for spin in (self._in_spin, self._out_spin):
                spin.setRange(0.25, high)
                spin.setMaximum(high)
            self._link_checkbox.setChecked(bool(linked))
            self._out_spin.setValue(effective_out)
            self._in_spin.setValue(effective_in)
            self._set_combo_quiet(self._in_combo, self._normalise_curve(in_curve))
            self._set_combo_quiet(self._out_combo, self._normalise_curve(out_curve))
            del blockers
        finally:
            self._updating = False

    @staticmethod
    def _clamp_length_for(value, high: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = high
        return min(max(0.25, number), high)

    def _payload(self) -> dict:
        return {
            "linked": bool(self._link_checkbox.isChecked()),
            "in_bars": float(self._in_spin.value()),
            "out_bars": float(self._out_spin.value()),
            "in_curve": self.in_curve,
            "out_curve": self.out_curve,
        }

    def _emit_changed(self):
        self.changed.emit(self._payload())

    # -- user-gesture handlers (each emits exactly once) ---------------------
    def _on_out_length_changed(self, value):
        if self._updating:
            return
        if self._link_checkbox.isChecked():
            self._updating = True
            try:
                with QSignalBlocker(self._in_spin):
                    self._in_spin.setValue(float(value))
            finally:
                self._updating = False
        self._emit_changed()

    def _on_in_length_changed(self, value):
        if self._updating:
            return
        if self._link_checkbox.isChecked():
            self._updating = True
            try:
                with QSignalBlocker(self._out_spin):
                    self._out_spin.setValue(float(value))
            finally:
                self._updating = False
        self._emit_changed()

    def _on_out_curve_changed(self, _index):
        if self._updating:
            return
        if self._link_checkbox.isChecked():
            self._updating = True
            try:
                self._set_combo_quiet(self._in_combo, self.out_curve)
            finally:
                self._updating = False
        self._emit_changed()

    def _on_in_curve_changed(self, _index):
        if self._updating:
            return
        if self._link_checkbox.isChecked():
            self._updating = True
            try:
                self._set_combo_quiet(self._out_combo, self.in_curve)
            finally:
                self._updating = False
        self._emit_changed()

    def _on_link_toggled(self, checked):
        if self._updating:
            return
        if checked:
            # Copy incoming row settings to outgoing.
            self._updating = True
            try:
                with QSignalBlocker(self._out_spin):
                    self._out_spin.setValue(float(self._in_spin.value()))
                self._set_combo_quiet(self._out_combo, self.in_curve)
            finally:
                self._updating = False
        self._emit_changed()

    def _do_reset(self):
        before = self._payload()
        after = {
            "linked": True,
            "in_bars": float(self._overlap_bars),
            "out_bars": float(self._overlap_bars),
            "in_curve": "smooth",
            "out_curve": "smooth",
        }
        if before == after:
            return
        self._updating = True
        try:
            with QSignalBlocker(self._link_checkbox):
                self._link_checkbox.setChecked(True)
            with QSignalBlocker(self._in_spin):
                self._in_spin.setValue(after["in_bars"])
            with QSignalBlocker(self._out_spin):
                self._out_spin.setValue(after["out_bars"])
            self._set_combo_quiet(self._in_combo, "smooth")
            self._set_combo_quiet(self._out_combo, "smooth")
        finally:
            self._updating = False
        self._emit_changed()
