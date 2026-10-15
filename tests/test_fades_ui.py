"""Focused widget tests for the standalone fade-controls panel."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QPushButton

from studio_fades_ui import FadesPanel


class FadesPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, overlap=8.0, **kwargs):
        panel = FadesPanel()
        self.addCleanup(panel.close)
        panel.set_values(overlap, **kwargs)
        events = []
        panel.changed.connect(events.append)
        return panel, events

    # -- linked edits: exactly one emission, mirrored ---------------------
    def test_linked_length_edit_mirrors_and_emits_once(self):
        panel, events = self.make_panel(8.0, linked=True, in_bars=4.0, out_bars=4.0)
        panel.out_spin.setValue(2.5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], {"linked": True, "in_bars": 2.5, "out_bars": 2.5,
                                     "in_curve": "smooth", "out_curve": "smooth"})
        self.assertEqual(panel.in_length, 2.5)
        self.assertEqual(panel.out_length, 2.5)

        events.clear()
        panel.in_spin.setValue(6.0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["in_bars"], 6.0)
        self.assertEqual(events[0]["out_bars"], 6.0)

    def test_linked_curve_edit_mirrors_and_emits_once(self):
        panel, events = self.make_panel(8.0, linked=True)
        panel.out_combo.setCurrentIndex(panel.out_combo.findData("linear"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["in_curve"], "linear")
        self.assertEqual(events[0]["out_curve"], "linear")
        self.assertEqual(panel.in_curve, "linear")
        self.assertEqual(panel.out_curve, "linear")

        events.clear()
        panel.in_combo.setCurrentIndex(panel.in_combo.findData("fast"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["in_curve"], "fast")
        self.assertEqual(events[0]["out_curve"], "fast")

    def test_rows_stay_editable_when_linked(self):
        panel, _ = self.make_panel(8.0, linked=True)
        self.assertTrue(panel.out_spin.isEnabled())
        self.assertTrue(panel.in_spin.isEnabled())
        self.assertTrue(panel.out_combo.isEnabled())
        self.assertTrue(panel.in_combo.isEnabled())

    # -- linking toggle -----------------------------------------------------
    def test_turning_link_on_copies_incoming_to_outgoing_once(self):
        panel, events = self.make_panel(8.0, linked=False, in_bars=3.0, out_bars=6.0,
                                        in_curve="slow", out_curve="fast")
        panel.link_checkbox.setChecked(True)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], {"linked": True, "in_bars": 3.0, "out_bars": 3.0,
                                     "in_curve": "slow", "out_curve": "slow"})
        self.assertTrue(panel.linked)
        self.assertEqual(panel.out_length, 3.0)
        self.assertEqual(panel.out_curve, "slow")

    def test_turning_link_off_preserves_values_once(self):
        panel, events = self.make_panel(8.0, linked=True, in_bars=5.0, out_bars=5.0,
                                        in_curve="linear", out_curve="linear")
        panel.link_checkbox.setChecked(False)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], {"linked": False, "in_bars": 5.0, "out_bars": 5.0,
                                     "in_curve": "linear", "out_curve": "linear"})

    # -- unlinked independence ---------------------------------------------
    def test_unlinked_edits_are_independent_and_emit_once(self):
        panel, events = self.make_panel(8.0, linked=False, in_bars=4.0, out_bars=4.0)
        panel.out_spin.setValue(2.0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["out_bars"], 2.0)
        self.assertEqual(events[0]["in_bars"], 4.0)
        self.assertEqual(panel.in_length, 4.0)

        events.clear()
        panel.in_combo.setCurrentIndex(panel.in_combo.findData("fast"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["in_curve"], "fast")
        self.assertEqual(events[0]["out_curve"], "smooth")
        self.assertEqual(panel.out_curve, "smooth")

    # -- set_values: quiet, None = full overlap, clamping ------------------
    def test_set_values_is_quiet_and_none_means_full_overlap(self):
        panel, events = self.make_panel(8.0)
        events.clear()
        panel.set_values(12.0, linked=True)
        self.assertEqual(events, [])
        self.assertEqual(panel.in_length, 12.0)
        self.assertEqual(panel.out_length, 12.0)
        self.assertEqual(panel.out_spin.maximum(), 12.0)
        self.assertEqual(panel.in_spin.maximum(), 12.0)

    def test_set_values_clamps_to_overlap_and_updates_range(self):
        panel, events = self.make_panel(8.0)
        panel.set_values(4.0, linked=False, in_bars=99.0, out_bars=0.1,
                         in_curve="linear", out_curve="slow")
        self.assertEqual(events, [])
        self.assertEqual(panel.in_length, 4.0)
        self.assertEqual(panel.out_length, 0.25)
        self.assertFalse(panel.linked)
        self.assertEqual(panel.in_curve, "linear")
        self.assertEqual(panel.out_curve, "slow")

    def test_set_values_new_overlap_reclamps_displayed_lengths(self):
        panel, events = self.make_panel(16.0, linked=False, in_bars=16.0, out_bars=16.0)
        panel.set_values(4.0, linked=False, in_bars=None, out_bars=None)
        self.assertEqual(events, [])
        self.assertEqual(panel.in_length, 4.0)
        self.assertEqual(panel.out_length, 4.0)

    # -- reset ---------------------------------------------------------------
    def test_reset_restores_defaults_and_emits_once(self):
        panel, events = self.make_panel(8.0, linked=False, in_bars=2.0, out_bars=3.0,
                                        in_curve="fast", out_curve="slow")
        panel.reset.click()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], {"linked": True, "in_bars": 8.0, "out_bars": 8.0,
                                     "in_curve": "smooth", "out_curve": "smooth"})
        self.assertTrue(panel.linked)
        self.assertEqual(panel.in_length, 8.0)
        self.assertEqual(panel.out_length, 8.0)

    def test_reset_is_callable_and_quiet_when_already_reset(self):
        panel, events = self.make_panel(8.0)
        panel.reset()  # callable form; already at defaults after set_values(8)
        self.assertEqual(events, [])
        panel.out_spin.setValue(2.0)
        self.assertEqual(len(events), 1)
        events.clear()
        panel.reset_fades()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["in_bars"], 8.0)

    # -- control specs ---------------------------------------------------------
    def test_control_specs_and_labels(self):
        panel, _ = self.make_panel(8.0)
        spins = panel.findChildren(QDoubleSpinBox)
        self.assertEqual(len(spins), 2)
        for spin in spins:
            self.assertEqual(spin.minimum(), 0.25)
            self.assertEqual(spin.maximum(), 8.0)
            self.assertEqual(spin.singleStep(), 0.25)
            self.assertEqual(spin.suffix(), " bars")
            self.assertEqual(spin.decimals(), 2)
            self.assertTrue(spin.accessibleName())
            self.assertTrue(spin.toolTip())
        combos = panel.findChildren(QComboBox)
        self.assertEqual(len(combos), 2)
        for combo in combos:
            self.assertEqual([combo.itemData(i) for i in range(combo.count())],
                             ["smooth", "linear", "fast", "slow"])
            self.assertEqual(combo.itemText(0), "Smooth (equal power)")
            self.assertTrue(combo.accessibleName())
        checks = panel.findChildren(QCheckBox)
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].text(), "Link fades")
        self.assertTrue(checks[0].isChecked())
        buttons = [b for b in panel.findChildren(QPushButton) if b.text() == "Reset fades"]
        self.assertEqual(len(buttons), 1)
        # Hint copy required by the assignment
        from PySide6.QtWidgets import QLabel
        hints = "\n".join(label.text() for label in panel.findChildren(QLabel))
        self.assertIn("With bass swap on, bass keeps its separate middle handoff. "
                      "Turn it off to fade the whole sound.", hints)
        self.assertIn("white timeline fade handles", hints)


if __name__ == "__main__":
    unittest.main()
