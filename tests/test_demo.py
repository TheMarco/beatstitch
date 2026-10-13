"""Bundled practice music, stable sources, cancellation and project preservation."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import soundfile as sf
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import djmix
from desktop_app import Studio
from studio_beat_grid import manual_settings
from studio_demo import bundled_project, prepare_demo
from studio_engine import Clip, Session, Track, layout
from studio_projects import read_project
from studio_guidance import timing_guidance
from studio_recommendations import review_mix
from studio_timing import VERSION as TIMING_VERSION


class DemoAssetsTests(unittest.TestCase):
    def test_complete_portable_demo_has_actual_waveforms_and_authored_drum_sections(self):
        session, raw = read_project(bundled_project())
        self.assertEqual(len(session.clips), 3)
        self.assertAlmostEqual(layout(session)[-1]['end'] / djmix.SR, 80 * 240 / 126, places=4)
        self.assertEqual(raw['_portable_paths'], 1)
        self.assertEqual(review_mix(session)['songs'], [])
        for track in session.tracks.values():
            guide = timing_guidance(track.timing, track=track)
            self.assertEqual(guide.title, 'Steady demo tempo')
            self.assertIn('not an analyzer result', guide.explanation)
            self.assertFalse(guide.available)
            grid = manual_settings(track, True)
            self.assertEqual(grid['anchor'], 0)
            self.assertGreater(len(track.waveform), 1000)
            self.assertGreater(np.std(track.waveform), .04)
            audio, rate = sf.read(track.path)
            self.assertEqual(rate, djmix.SR)
            self.assertEqual(audio.shape[1], 2)
            self.assertTrue(np.isfinite(audio).all())
            self.assertLessEqual(np.max(abs(audio)), .780001)
            # All melodic sources are stereo; drums are mono. The authored drum
            # intro/outro must be free of that musical layer, including delay tails.
            intro = round(8 * 240 / track.bpm * rate)
            outro = round(24 * 240 / track.bpm * rate)
            np.testing.assert_allclose(audio[:intro, 0], audio[:intro, 1], atol=2e-7)
            np.testing.assert_allclose(audio[outro + 1:, 0], audio[outro + 1:, 1], atol=2e-7)
            self.assertGreater(np.max(abs(audio[intro:outro, 0] - audio[intro:outro, 1])), .01)

    def test_authored_status_requires_matching_source_and_bpm(self):
        session, _ = read_project(bundled_project())
        track = session.tracks['demo-1']
        track.bpm += 1
        self.assertEqual(timing_guidance(track.timing, track=track).title, 'Timing not analyzed')
        track.bpm -= 1; track.timing['source'] = ['old', 0, 0]
        self.assertEqual(timing_guidance(track.timing, track=track).title, 'Timing not analyzed')
        self.assertEqual(timing_guidance({'version': TIMING_VERSION, 'state': 'steady'}).title, 'No significant drift detected')

    def test_sources_persist_and_reopening_resets_only_the_demo_arrangement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path, session = prepare_demo(root)
            self.assertTrue(all(Path(t.path).is_relative_to(root / 'demo-sources') for t in session.tracks.values()))
            self.assertTrue(all(manual_settings(t, True) for t in session.tracks.values()))
            locations = {k: t.path for k, t in session.tracks.items()}
            # Even an edited stored template must not alter the fresh-demo choices.
            value = json.loads(path.read_text()); value['clips'][1]['blend_bars'] = 4; path.write_text(json.dumps(value))
            with patch('studio_demo.create_portable_copy', side_effect=AssertionError('Must reuse unchanged audio')):
                again, fresh = prepare_demo(root)
            self.assertEqual(again, path)
            self.assertEqual(fresh.clips[1].blend_bars, 8)
            self.assertEqual(locations, {k: t.path for k, t in fresh.tracks.items()})
            # A missing source gets a separate fresh copy; the old project is retained.
            Path(session.tracks['demo-1'].path).unlink()
            replacement, repaired = prepare_demo(root)
            self.assertNotEqual(replacement, path)
            self.assertTrue(path.is_file())
            self.assertTrue(all(Path(t.path).is_file() for t in repaired.tracks.values()))

    def test_cancelled_copy_does_not_publish_half_a_demo(self):
        with tempfile.TemporaryDirectory() as directory:
            def cancel(message):
                if message.startswith('Copying source'): raise djmix.MixError('Operation cancelled.')
            with self.assertRaisesRegex(djmix.MixError, 'Operation cancelled'):
                prepare_demo(Path(directory), cancel)
            self.assertEqual(list((Path(directory) / 'demo-sources').iterdir()), [])


class DemoWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(self.root); self.addCleanup(self.close)
        self.w.session = Session(name='My original mix', tracks={'a': Track('a', '/old-song.wav', 'My song', 120, 120)},
                                 clips=[Clip('a', 0, 120)])
        self.w.selected_id = self.w.session.clips[0].id
        self.w.dirty = True; self.w.autosave(); self.w.refresh()

    def settle(self):
        deadline = time.monotonic() + 15
        while self.w.jobs and time.monotonic() < deadline: QTest.qWait(10)
        self.assertFalse(self.w.jobs, 'Demo opening did not finish')
        self.app.processEvents()

    def close(self):
        self.settle()
        if self.w.help_window: self.w.help_window.close()
        self.w.close(); self.app.processEvents()

    def test_open_save_and_restore_preserve_user_work_and_bundled_template(self):
        w = self.w; before = copy.deepcopy(w.session.to_dict())
        asset = bundled_project().read_bytes()
        w.open_demo(); self.assertFalse(w.act_demo.isEnabled()); self.settle()
        self.assertIsNone(w.project_path)
        self.assertTrue(w.dirty)
        self.assertEqual(len(w.session.clips), 3)
        self.assertEqual(w.selected_id, w.session.clips[1].id)
        self.assertEqual(w.help_window.current_topic, 'demo')
        self.assertTrue(w.player.source().isEmpty(), 'Demo must not autoplay')
        recoveries = list(self.root.glob('recovery-*.djmix'))
        self.assertEqual(len(recoveries), 1)
        original = json.loads(recoveries[0].read_text())
        self.assertEqual(Session.from_dict(original).to_dict(), before)
        self.assertEqual(original['_recovery_reason'], 'Before trying the demo mix')
        self.assertTrue(w.write_project(self.root / 'My practice mix.djmix'))
        self.assertEqual(bundled_project().read_bytes(), asset)
        self.assertTrue(w.open_project_path(recoveries[0], recovery=True))
        self.assertEqual(w.session.to_dict(), before)

    def test_late_cancellation_keeps_current_mix_and_does_not_show_guide(self):
        w = self.w; before = copy.deepcopy(w.session.to_dict())
        autosave = (self.root / 'autosave.djmix').read_bytes()
        cancel = w.open_demo()
        self.assertTrue(w.pool.waitForDone(15000))
        cancel.set(); self.settle()
        self.assertEqual(w.session.to_dict(), before)
        self.assertEqual((self.root / 'autosave.djmix').read_bytes(), autosave)
        self.assertIsNone(w.help_window)
        self.assertFalse(list(self.root.glob('recovery-*.djmix')))


if __name__ == '__main__': unittest.main()
