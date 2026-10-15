"""Update checks ask only the website, pin downloads, and never replace another file."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QPushButton

import studio_updates as updates
from studio_version import VERSION

ROOT = Path(__file__).resolve().parents[1]


class Response(io.BytesIO):
    def __init__(self, data, *, status=200, headers=None, url=updates.LATEST_URL):
        super().__init__(data)
        self.status, self.headers, self.url = status, headers or {}, url

    def geturl(self): return self.url


def release(version, data=b'new build'):
    return {'version': version, 'downloads': {key: {
        'url': f'https://github.com/TheMarco/beatstitch/releases/download/v{version}/BeatStitch-{version}-{suffix}',
        'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for key, suffix in updates.PACKAGES.items()}}


class VersionTests(unittest.TestCase):
    def test_versions_compare_numerically_and_reject_junk(self):
        self.assertTrue(updates.is_newer('0.1.10', '0.1.2'))
        self.assertTrue(updates.is_newer('0.2', '0.1.9'))
        self.assertFalse(updates.is_newer('0.1', '0.1.0'))
        self.assertFalse(updates.is_newer('0.1.1', '0.1.2'))
        for junk in ('', 'v0.1.3', '0.1.3-beta', '1', '0..1', None, 3, '0.1.2.3.4'):
            self.assertIsNone(updates.parse_version(junk), junk)
            self.assertFalse(updates.is_newer(junk, '0.0.1'))

    def test_automatic_checks_run_at_most_daily(self):
        now = 1_800_000_000
        self.assertTrue(updates.due(None, now))
        self.assertTrue(updates.due('yesterday', now))
        self.assertFalse(updates.due(now - 3600, now))
        self.assertTrue(updates.due(now - 25 * 3600, now))
        self.assertTrue(updates.due(now + 3600, now))

    def test_package_matches_platform_and_takes_its_name_from_the_version(self):
        info = release('0.1.3')
        self.assertEqual(updates.package(info, 'darwin')['name'], 'BeatStitch-0.1.3-mac-arm64.zip')
        self.assertEqual(updates.package(info, 'win32')['name'], 'BeatStitch-0.1.3-windows-x64.zip')
        self.assertEqual(updates.package(info, 'linux')['name'], 'BeatStitch-0.1.3-linux-x64.tar.gz')
        self.assertIsNone(updates.package(info, 'sunos5'))
        for field, value in (('url', 'http://example.com/a.zip'), ('size', True), ('size', 0), ('sha256', 'abc')):
            broken = json.loads(json.dumps(info)); broken['downloads']['mac'][field] = value
            self.assertIsNone(updates.package(broken, 'darwin'), (field, value))
        self.assertIsNone(updates.package({'version': '0.1.3'}, 'darwin'))


class FetchTests(unittest.TestCase):
    def fetch(self, body, **response):
        requests = []
        def opener(request, **kwargs):
            requests.append(request)
            return Response(body if isinstance(body, bytes) else json.dumps(body).encode(), **response)
        return updates.fetch_latest(opener=opener), requests

    def test_reads_version_and_sends_only_the_app_version(self):
        info, requests = self.fetch(release('0.1.3'))
        self.assertEqual(info['version'], '0.1.3')
        self.assertEqual(requests[0].full_url, updates.LATEST_URL)
        self.assertEqual(requests[0].get_header('User-agent'), f'BeatStitch/{VERSION} (update check)')
        self.assertIsNone(requests[0].data)

    def test_refuses_insecure_oversized_or_invalid_answers(self):
        cases = [(release('0.1.3'), {'url': 'http://beatstitch.ai-created.com/latest.json'}),
                 (b' ' * (updates.LIMIT + 1), {}), (b'<html>', {}), ([1, 2], {}), ({'version': 'soon'}, {})]
        for body, response in cases:
            with self.assertRaises(ValueError):
                self.fetch(body, **response)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.data = b'new build'
        self.entry = updates.package(release('0.1.3', self.data), 'darwin')

    def opener(self, request, **kwargs):
        return Response(self.data, url=request.full_url)

    def test_downloads_verified_file_without_replacing_another(self):
        mine = self.folder / self.entry['name']
        mine.write_bytes(b'someone else')
        path = updates.download_update(self.entry, self.folder, opener=self.opener)
        self.assertEqual(path.name, 'BeatStitch-0.1.3-mac-arm64 (2).zip')
        self.assertEqual(path.read_bytes(), self.data)
        self.assertEqual(mine.read_bytes(), b'someone else')
        again = updates.download_update(self.entry, self.folder, opener=lambda *a, **k: self.fail('Downloaded twice'))
        self.assertEqual(again, path)

    def test_archive_suffix_stays_whole(self):
        entry = updates.package(release('0.1.3', self.data), 'linux')
        (self.folder / entry['name']).write_bytes(b'other')
        self.assertEqual(updates.target_path(self.folder, entry['name'], entry).name, 'BeatStitch-0.1.3-linux-x64 (2).tar.gz')

    def test_failures_become_plain_messages(self):
        def missing(request, **kwargs):
            raise urllib.error.HTTPError(request.full_url, 404, 'Not Found', {}, None)
        def offline(request, **kwargs):
            raise urllib.error.URLError('nodename nor servname provided')
        with self.assertRaisesRegex(ValueError, 'HTTP 404'):
            updates.download_update(self.entry, self.folder, opener=missing)
        with self.assertRaisesRegex(ValueError, 'internet connection'):
            updates.download_update(self.entry, self.folder, opener=offline)
        with self.assertRaisesRegex(ValueError, 'verification'):
            updates.download_update(self.entry, self.folder, opener=lambda r, **k: Response(b'bad build', url=r.full_url))


class WebsiteReleaseTests(unittest.TestCase):
    def test_latest_json_matches_the_versions_shown_on_the_site(self):
        info = json.loads((ROOT / 'web/latest.json').read_text())
        self.assertIsNotNone(updates.parse_version(info['version']))
        for platform in ('darwin', 'win32', 'linux'):
            entry = updates.package(info, platform)
            self.assertIsNotNone(entry, platform)
            address = urllib.parse.urlparse(entry['url'])
            query = urllib.parse.parse_qs(address.query)
            drive = (address.hostname == 'drive.usercontent.google.com' and address.path == '/download'
                     and bool(query.get('id')) and query.get('export') == ['download']
                     and query.get('confirm') == ['t'])
            self.assertTrue(entry['url'].endswith('/' + entry['name']) or drive, entry['url'])
        page = (ROOT / 'web/index.html').read_text()
        shown = re.findall(r'data-release>([^<]*)<', page)
        self.assertEqual(len(shown), 2)
        self.assertEqual(set(shown), {info['version']})


class MenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from desktop_app import Studio
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.window = Studio(Path(self.temp.name))
        self.addCleanup(self.close)

    def close(self):
        self.window.close(); self.app.processEvents()

    def check(self, **fetch):
        with patch('studio_updates.fetch_latest', **fetch):
            self.window.act_check_updates.trigger()
            end = time.monotonic() + 3
            while self.window.update_signals is not None and time.monotonic() < end:
                self.app.processEvents(); time.sleep(.01)
        self.assertIsNone(self.window.update_signals)

    def actions(self):
        return [b.text() for b in self.window.notice.findChildren(QPushButton) if not b.isHidden()]

    def test_manual_check_reports_latest_newer_and_failure(self):
        self.check(return_value=release(VERSION))
        self.assertIn(f'{VERSION} is the latest version', self.window.notice.message.text())
        self.check(return_value=release('99.0'))
        self.assertIn('BeatStitch 99.0 is available', self.window.notice.message.text())
        self.assertIn('Download', self.actions())
        self.check(side_effect=OSError('offline'))
        self.assertEqual(self.window.notice.property('tone'), 'error')
        self.assertIn('could not be reached', self.window.notice.message.text())
        self.assertIsInstance(self.window.prefs.get('update_checked_at'), float)

    def test_download_saves_and_offers_to_show_the_file(self):
        saved = Path(self.temp.name) / 'BeatStitch-99.0-mac-arm64.zip'
        def synchronous_job(work, finished, **kwargs):
            finished(work(lambda _: None, lambda _: None))
        with patch.object(self.window, 'job', side_effect=synchronous_job), \
             patch('studio_updates.download_update', return_value=saved) as download:
            self.window.download_update('99.0', updates.package(release('99.0')))
        download.assert_called_once()
        self.assertIn(saved.name, self.window.notice.message.text())
        self.assertIn('Quit BeatStitch', self.window.notice.message.text())

    def test_automatic_check_respects_the_setting_and_the_daily_limit(self):
        with patch('desktop_app.QTimer.singleShot') as later:
            self.window.schedule_update_check()
            self.assertEqual(later.call_count, 1)
            self.window.write_prefs(update_checked_at=time.time())
            self.window.schedule_update_check()
            self.assertEqual(later.call_count, 1)
            self.window.write_prefs(update_checked_at=None)
            self.window.act_auto_updates.setChecked(False)
            self.assertFalse(json.loads((Path(self.temp.name) / 'preferences.json').read_text())['update_check'])
            self.window.schedule_update_check()
            self.assertEqual(later.call_count, 1)


if __name__ == '__main__':
    unittest.main()
