"""Small active explanations must remain readable in the actual Qt theme."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget
import studio_theme as theme


def contrast(foreground, background):
    foreground, background = QColor(foreground), QColor(background)
    alpha = foreground.alphaF()
    foreground = QColor.fromRgbF(*(a * alpha + b * (1 - alpha) for a, b in zip(
        (foreground.redF(), foreground.greenF(), foreground.blueF()),
        (background.redF(), background.greenF(), background.blueF()))))
    def luminance(value):
        color = QColor(value)
        rgb = [color.redF(), color.greenF(), color.blueF()]
        linear = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in rgb]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    a, b = sorted((luminance(foreground), luminance(background)))
    return (b + .05) / (a + .05)


class ReadabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_active_small_ink_clears_contrast_floor_on_each_used_surface(self):
        # A desktop readability benchmark, not a claim of whole-app WCAG compliance.
        for ink in (theme.TEXT, theme.TEXT_2, theme.TEXT_3):
            for background in (theme.BASE, theme.TITLE, theme.LANE, theme.LANE_ALT,
                               theme.FIELD, theme.RAISED, theme.HOVER, theme.SELECTION):
                with self.subTest(ink=ink, background=background):
                    self.assertGreaterEqual(contrast(ink, background), 4.5)
        for background in (theme.RAISED, theme.HOVER, theme.PRESSED, theme.SELECTION):
            with self.subTest(warning_background=background):
                self.assertGreaterEqual(contrast(theme.BAD_TEXT, background), 4.5)

    def test_styled_hint_and_editor_placeholder_use_readable_active_ink(self):
        previous = self.app.palette(), self.app.styleSheet()
        self.addCleanup(self.app.setPalette, previous[0]); self.addCleanup(self.app.setStyleSheet, previous[1])
        self.app.setPalette(theme.palette()); self.app.setStyleSheet(theme.stylesheet())
        panel = QWidget(); layout = QVBoxLayout(panel)
        self.addCleanup(panel.close)
        hint = QLabel('Listen for doubled kicks and a drifting pulse.'); hint.setProperty('role', 'hint')
        editor = QPlainTextEdit(); editor.setPlaceholderText('Write a listening reminder.')
        unavailable = QPushButton('Unavailable'); unavailable.setEnabled(False)
        unavailable.setProperty('role', 'danger')
        for widget in (hint, editor, unavailable): layout.addWidget(widget)
        panel.show(); self.app.processEvents()
        hint_ink = hint.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)
        placeholder = editor.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.PlaceholderText)
        self.assertGreaterEqual(contrast(hint_ink, theme.BASE), 4.5)
        self.assertGreaterEqual(contrast(placeholder, editor.palette().color(QPalette.ColorRole.Base)), 4.5)
        self.assertEqual(placeholder.alpha(), 255)
        self.assertNotEqual(hint_ink, unavailable.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText))
        self.assertFalse(unavailable.isEnabled())
        for kind in (QLineEdit, QTextEdit, QPlainTextEdit):
            field = kind(); field.setPlaceholderText('Search or write a reminder.'); layout.addWidget(field)
            field.ensurePolished(); self.app.processEvents()
            for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
                ink = field.palette().color(group, QPalette.ColorRole.PlaceholderText)
                self.assertEqual(ink.alpha(), 255)
                self.assertGreaterEqual(contrast(ink, field.palette().color(group, QPalette.ColorRole.Base)), 4.5)
        for role in ('primary', 'ghost', 'danger'):
            unavailable.setProperty('role', role)
            unavailable.style().unpolish(unavailable); unavailable.style().polish(unavailable)
            self.assertEqual(unavailable.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText),
                             QColor(theme.DISABLED_TEXT))


if __name__ == '__main__': unittest.main()
