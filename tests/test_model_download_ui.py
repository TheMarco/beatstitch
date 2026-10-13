import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication, QWidget

from studio_downloads import DownloadsDialog
from studio_process import check_cancelled


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.jobs = {}; self.closing = False; self.pool = QThreadPool(self)
    def show_about(self): pass


class DownloadsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'BEATSTITCH_MODEL_DIR':self.temp.name, 'BEATSTITCH_NO_BUNDLED_MODELS':'1'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.host = Host(); self.dialog = DownloadsDialog(self.host)
        self.addCleanup(self.dispose)

    def dispose(self):
        if self.dialog.busy:
            self.dialog.request_cancel(); self.pump(lambda: not self.dialog.busy)
        self.host.pool.waitForDone(2000)
        self.dialog.deleteLater(); self.host.deleteLater(); self.app.processEvents()

    def pump(self, done):
        end = time.monotonic()+3
        while not done() and time.monotonic()<end:
            self.app.processEvents(); time.sleep(.01)
        self.assertTrue(done())

    def test_fresh_install_recommends_only_three_small_packs(self):
        self.assertEqual(set(self.dialog.selected()), {'beats','key','content'})
        self.assertFalse(self.dialog.rows['sections'][0].isChecked())
        self.assertFalse(self.dialog.rows['separation'][0].isChecked())
        self.assertIn('125 MB', self.dialog.install_selected.text())
        self.assertIn('3.07 GB', self.dialog.rows['sections'][3].text())
        self.assertIn('noncommercial', self.dialog.rows['sections'][3].parentWidget().findChildren(type(self.dialog.message))[-1].text())

    def test_feature_request_selects_only_needed_pack_and_does_not_download(self):
        called = []
        with patch('studio_downloads.install', side_effect=AssertionError('Unexpected download')):
            self.dialog.request(['key'], lambda: called.append(True))
            self.assertEqual(self.dialog.selected(), ['key'])
            self.assertIn('Download & continue', self.dialog.install_selected.text())
            self.assertEqual(called, [])

    def test_background_download_shows_progress_and_cancels_without_abandoning_worker(self):
        started = threading.Event()
        def fake_install(pack, progress):
            started.set()
            progress(dict(pack=pack, phase='Downloading', received=3, total=9, pack_received=3, pack_total=9))
            while True:
                check_cancelled(); time.sleep(.01)
        with patch('studio_downloads.install', side_effect=fake_install):
            self.dialog.show(); self.dialog.start(['key'])
            self.pump(lambda: started.is_set() and 'Key detection' in self.dialog.message.text())
            self.assertTrue(self.dialog.busy)
            self.assertFalse(self.dialog.install_selected.isEnabled())
            self.assertFalse(self.dialog.rows['sections'][1].isEnabled())
            self.dialog.close(); self.app.processEvents()
            self.assertFalse(self.dialog.isVisible()); self.assertTrue(self.dialog.busy)
            self.dialog.request_cancel(); self.pump(lambda: not self.dialog.busy)
            self.assertIn('Saved bytes are kept', self.dialog.message.text())
            self.assertTrue(self.dialog.rows['key'][1].isEnabled())


if __name__ == '__main__': unittest.main()
