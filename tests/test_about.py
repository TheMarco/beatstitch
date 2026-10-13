"""About remains offline, opens from the app menu, and preserves the mix."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QStandardPaths, QUrl
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

from desktop_app import Studio, configure_application_identity
from studio_about import AboutDialog, entry_html, load_credits


class AboutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_about_menu_reuses_dialog_without_touching_mix_or_starting_processing(self):
        with tempfile.TemporaryDirectory() as folder:
            window = Studio(Path(folder))
            try:
                before = copy.deepcopy(window.session.to_dict())
                jobs = dict(window.jobs)
                window.act_about.trigger()
                self.app.processEvents()
                dialog = window.about_window
                self.assertIsInstance(dialog, AboutDialog)
                self.assertFalse(dialog.isModal())
                with patch('studio_about.QDesktopServices.openUrl') as open_url:
                    dialog.support_button.click()
                    open_url.assert_called_once_with(QUrl('https://ko-fi.com/aianddesign'))
                self.assertFalse(dialog.logo.source.isNull())
                self.assertEqual(dialog.logo.source.width(), 3 * dialog.logo.source.height())
                icon = window.windowIcon().pixmap(64, 64).toImage()
                colors = [icon.pixelColor(x, y) for y in range(64) for x in range(64)]
                self.assertGreater(sum(c.blue() > 120 and c.red() > 45 and c.green() < 100 for c in colors), 100)
                self.assertGreater(sum(c.red() > 150 and c.green() > 70 and c.blue() < 100 for c in colors), 100)
                self.assertEqual(window.act_about.menuRole(), QAction.MenuRole.AboutRole)
                window.act_about.trigger()
                self.assertIs(window.about_window, dialog)
                self.assertEqual(window.session.to_dict(), before)
                self.assertEqual(window.jobs, jobs)
                self.assertIn('Marco van Hylckama Vlieg', load_credits()['creator'])
                dialog.search.setText('OpenKeyScan')
                self.assertEqual(dialog.list.count(), 1)
                self.assertIn('Alexander Sommer', dialog.details.toPlainText())
                dialog.search.setText('does-not-exist-123')
                self.assertEqual(dialog.list.count(), 0)
                self.assertIn('No matching', dialog.details.toPlainText())
                dialog.search.clear()
                self.assertGreater(dialog.list.count(), 20)
            finally:
                if window.about_window:
                    window.about_window.close()
                window.close()
                self.app.processEvents()

    def test_offline_notice_content_is_escaped_and_links_are_explicit_web_only(self):
        html = entry_html({'name': '<script>bad</script>', 'credit': 'A & B',
                           'notices': [{'title': 'Copyright', 'text': '<tag>\ntext'}],
                           'urls': ['file:///private/data', 'https://example.org/project']})
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;tag&gt;', html)
        self.assertNotIn('file://', html)
        with patch('studio_about.QDesktopServices.openUrl') as open_url:
            AboutDialog.open_project_link(QUrl('file:///private/data'))
            open_url.assert_not_called()
            AboutDialog.open_project_link(QUrl('https://example.org/project'))
            open_url.assert_called_once()

    def test_rebrand_keeps_the_existing_data_folder(self):
        names = (self.app.applicationName(), self.app.organizationName(), self.app.applicationDisplayName())
        try:
            self.app.setApplicationName('DJ Studio')
            self.app.setOrganizationName('DJ Studio')
            existing = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
            self.assertEqual(configure_application_identity(self.app), existing)
            self.assertEqual(self.app.applicationName(), 'BeatStitch')
            self.assertEqual(self.app.applicationDisplayName(), 'BeatStitch')
        finally:
            self.app.setApplicationName(names[0])
            self.app.setOrganizationName(names[1])
            self.app.setApplicationDisplayName(names[2])

    def test_catalog_includes_main_tools_models_fonts_and_transitive_libraries(self):
        entries = load_credits()['entries']
        ids = {e['id'] for e in entries}
        self.assertEqual(len(ids), len(entries))
        for expected in ('beatstitch', 'rubber-band', 'ffmpeg', 'qt', 'python', 'barlow', 'openkeyscan',
                         'beat-this', 'panns', 'spleeter', 'edmformer', 'musicfm', 'muq',
                         'python:numpy', 'python:scipy', 'python:librosa', 'python:torch',
                         'python:llvmlite', 'python:pyinstaller'):
            self.assertIn(expected, ids)
        for entry in entries:
            self.assertTrue(entry['credit'])
            self.assertTrue(entry['license'])
        for identity in ('beatstitch', 'barlow', 'openkeyscan', 'beat-this', 'panns', 'qt', 'spleeter', 'python:pyside6', 'python:shiboken6'):
            self.assertTrue(next(e for e in entries if e['id'] == identity)['notices'])


if __name__ == '__main__':
    unittest.main()
