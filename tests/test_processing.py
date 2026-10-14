"""Running work blocks input until every task and result delivery have finished."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QProgressBar
from desktop_app import Studio
from studio_process import check_cancelled


class ProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        with patch.object(Studio, 'load_bands'):
            self.w = Studio(Path(self.temp.name))
        self.w.show(); self.app.processEvents(); self.addCleanup(self.dispose)

    def dispose(self):
        self.w.cancel_job()
        end = time.monotonic() + 3
        while self.w.jobs and time.monotonic() < end: QTest.qWait(10)
        self.w.jobs.clear(); self.w.downloads_window = None; self.w.finalizing_tasks = 0
        self.w.update_job_flags(); self.w.close(); self.app.processEvents()

    def worker(self, cancellable=True):
        worker = Mock(ui_title='Checking songs', ui_reason='', ui_progress='2 of 6: Song')
        event = threading.Event(); self.w.jobs[worker] = (event, True, False, cancellable)
        self.w.update_job_flags()
        return worker, event

    def test_modal_wait_and_cancel_cannot_be_dismissed_early(self):
        worker, event = self.worker()
        dialog = self.w.processing_dialog
        self.assertEqual(dialog.windowModality(), Qt.WindowModality.ApplicationModal)
        self.assertFalse(self.w.centralWidget().isEnabled())
        self.assertFalse(self.w.menuBar().isEnabled())
        spy = QSignalSpy(self.w.library_tools.detect.clicked)
        self.w.library_tools.detect.click(); self.assertEqual(spy.count(), 0)
        dialog.reject(); self.assertTrue(event.is_set()); self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog.cancel.isEnabled())
        self.assertIn('Stopping', dialog.title.text())
        self.w.jobs.pop(worker); self.w.finalizing_tasks = 1; self.w.update_job_flags()
        self.assertTrue(dialog.isVisible()); self.assertFalse(self.w.centralWidget().isEnabled())
        self.w.finalizing_tasks = 0; self.w.update_job_flags()
        self.assertFalse(dialog.isVisible()); self.assertTrue(self.w.centralWidget().isEnabled())

    def test_noncancellable_and_overlapping_work_stays_blocked(self):
        first, _ = self.worker(); second, event = self.worker(cancellable=False)
        self.w.jobs.pop(first); self.w.update_job_flags()
        dialog = self.w.processing_dialog
        self.assertTrue(dialog.isVisible()); self.assertTrue(dialog.cancel.isHidden())
        dialog.close(); self.assertTrue(dialog.isVisible()); self.assertFalse(event.is_set())
        self.assertFalse(self.w.centralWidget().isEnabled())
        self.w.jobs.pop(second); self.w.update_job_flags(); self.assertFalse(dialog.isVisible())

    def test_result_signal_does_not_unlock_before_worker_cleanup(self):
        delivered = []
        with patch.object(self.w.pool, 'start') as start:
            self.w.job(lambda *_: None, lambda result: delivered.append((result, self.w.centralWidget().isEnabled())), cancellable=True)
        worker = start.call_args.args[0]
        worker.signals.result.emit('result')
        self.assertEqual(delivered, []); self.assertTrue(self.w.jobs)
        self.assertTrue(self.w.processing_dialog.isVisible())
        worker.signals.finished.emit()
        self.assertEqual(delivered, [('result', False)])
        self.assertFalse(self.w.jobs); self.assertTrue(self.w.centralWidget().isEnabled())

    def test_real_cancel_releases_modal_only_after_worker_stops(self):
        started = threading.Event()
        def work(progress, emit):
            progress('Checking source audio'); started.set()
            while True: check_cancelled(); time.sleep(.01)
        self.w.job(work, Mock(), cancellable=True)
        end = time.monotonic() + 3
        while not started.is_set() and time.monotonic() < end: QTest.qWait(10)
        self.assertTrue(started.is_set())
        self.w.processing_dialog.cancel.click()
        self.assertFalse(self.w.centralWidget().isEnabled())
        while self.w.jobs and time.monotonic() < end: QTest.qWait(10)
        self.assertFalse(self.w.jobs); self.assertFalse(self.w.processing_dialog.isVisible())

    def test_model_download_uses_same_block_and_cancel(self):
        event = threading.Event(); progress = QProgressBar(); progress.setRange(0, 100); progress.setValue(40)
        def stop(): event.set()
        self.w.downloads_window = SimpleNamespace(busy=True, worker=SimpleNamespace(removing=False),
            cancel=event, banner='Key detection: 8 MB of 19 MB', progress=progress, request_cancel=stop)
        self.w.update_job_flags(); dialog = self.w.processing_dialog
        self.assertTrue(dialog.isVisible()); self.assertFalse(self.w.centralWidget().isEnabled())
        self.assertEqual(dialog.progress.value(), 40)
        dialog.cancel.click(); self.assertTrue(event.is_set()); self.assertTrue(dialog.isVisible())
        self.w.downloads_window.busy = False; self.w.update_job_flags()
        self.assertFalse(dialog.isVisible())


if __name__ == '__main__': unittest.main()
