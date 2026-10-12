"""Source identity, safe caching and musically bounded draft suggestions."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import djmix
from studio_cache import source_signature
from studio_engine import Track, Clip, Session
from studio_process import cancel_on
from studio_song_sections import (VERSION, MODEL_ID, analyze_sections, current_sections,
                                  normalize_regions, valid_result, section_cues)


def report(track, regions=None):
    return {'version': VERSION, 'model_id': MODEL_ID, 'duration': track.duration,
            'source': source_signature(track.path), 'device': 'cpu',
            'regions': regions or [dict(label='intro', start=0., end=8.),
                                   dict(label='buildup', start=8., end=12.),
                                   dict(label='drop', start=12., end=32.),
                                   dict(label='outro', start=32., end=40.)]}


class SongSectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'song.mp3'; self.source.write_bytes(b'source')
        self.track = Track('a', str(self.source), 'Song', 40., 120., cue_out=40.)

    def test_normalization_does_not_hide_gaps_or_invalid_ranges(self):
        self.assertEqual(normalize_regions([dict(label='drop', start=0, end=10), dict(label='drop', start=10, end=20),
                                           dict(label='drop', start=21, end=40.05)], 40),
                         [dict(label='drop', start=0, end=20), dict(label='drop', start=21, end=40)])
        for value in (float('nan'), float('inf'), -1, 40.3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_regions([dict(label='intro', start=0, end=value)], 40)
        for regions in ([], [dict(label='chorus', start=0, end=10)],
                        [dict(label='intro', start=0, end=10), dict(label='drop', start=9, end=20)]):
            with self.assertRaises(ValueError): normalize_regions(regions, 40)
        value = report(self.track); self.assertTrue(valid_result(value, 40))
        value['model_id'] = 'old'; self.assertFalse(valid_result(value, 40))

    def test_cached_result_skips_worker_and_bad_cache_or_changed_file_recomputes(self):
        cache = self.root / 'structure-cache'; logs = []
        with patch('studio_song_sections.verify_models'), patch('studio_song_sections._run_worker', return_value={
                'sections': report(self.track)['regions'], 'device': 'cpu'}) as worker:
            first = analyze_sections(self.source, 40, cache, logs.append)
            self.assertEqual(analyze_sections(self.source, 40, cache, logs.append), first)
            self.assertEqual(worker.call_count, 1); self.assertIn('Reusing', logs[-1])
            file = next(cache.glob('*.sections.json')); file.write_text('{bad')
            analyze_sections(self.source, 40, cache); self.assertEqual(worker.call_count, 2)
            self.source.write_bytes(b'changed song')
            analyze_sections(self.source, 40, cache); self.assertEqual(worker.call_count, 3)
        self.assertFalse(list(cache.glob('section-work-*')))

    def test_source_change_and_cancellation_never_publish_analysis(self):
        cache = self.root / 'structure-cache'
        def worker(*args):
            self.source.write_bytes(b'changed during work')
            return {'sections': report(self.track)['regions'], 'device': 'cpu'}
        with patch('studio_song_sections.verify_models'), patch('studio_song_sections._run_worker', side_effect=worker):
            with self.assertRaisesRegex(djmix.MixError, 'source changed'):
                analyze_sections(self.source, 40, cache)
        self.assertFalse(list(cache.glob('*.json')))
        event = threading.Event()
        def cancel(*args):
            event.set(); return {'sections': report(self.track)['regions'], 'device': 'cpu'}
        with cancel_on(event), patch('studio_song_sections.verify_models'), patch('studio_song_sections._run_worker', side_effect=cancel):
            with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                analyze_sections(self.source, 40, cache)
        self.assertFalse(list(cache.glob('*.json'))); self.assertFalse(list(cache.glob('section-work-*')))

    def test_cues_use_current_grid_fit_both_neighbors_and_preserve_session(self):
        from studio_beat_grid import grid_settings
        t = self.track; t.song_sections = report(t); t.beat_grid = grid_settings(t, 120, 0)
        session = Session(target_bpm=120, tracks={'a': t}, clips=[Clip('a', 0, 40, blend_bars=8) for _ in range(3)])
        session.clips[1].cue_in = 2; session.clips[1].cue_out = 38
        before = copy.deepcopy(session.to_dict()); choices = section_cues(session, 1)
        # The manual grid deliberately stops before the exact source end; no
        # extra bar is invented for the last two seconds.
        self.assertEqual([(v['kind'], v['time'], v['overlap']) for v in choices], [('start', 0, 4), ('end', 38, 3)])
        self.assertEqual(session.to_dict(), before)
        session.clips[1].protected_moments = [dict(start=4, end=36, kind='favorite', label='Keep')]
        self.assertEqual(section_cues(session, 1), [])
        t.beat_grid = {}; self.assertEqual(section_cues(session, 1), [])
        self.source.write_bytes(b'replaced'); self.assertEqual(current_sections(t), {})

    def test_new_annotation_roundtrips_and_old_project_defaults_empty(self):
        t = self.track; t.song_sections = report(t)
        session = Session(tracks={'a': t}, clips=[Clip('a', 0, 40)])
        self.assertEqual(Session.from_dict(session.to_dict()).tracks['a'].song_sections, t.song_sections)
        old = session.to_dict(); old['version'] = 10; del old['tracks']['a']['song_sections']
        self.assertEqual(Session.from_dict(old).tracks['a'].song_sections, {})

    def test_interior_outro_cannot_suggest_trimming_a_later_drop(self):
        from studio_beat_grid import grid_settings
        t = self.track; t.song_sections = report(t, [dict(label='drop', start=0, end=20),
            dict(label='outro', start=20, end=32), dict(label='drop', start=32, end=40)])
        t.beat_grid = grid_settings(t, 120, 0)
        session = Session(tracks={'a': t}, clips=[Clip('a', 0, 40)])
        self.assertEqual(section_cues(session, 0), [])

    def test_stop_reaps_isolated_worker_promptly(self):
        import subprocess, sys, time
        from studio_song_sections import _run_worker
        event = threading.Event(); children = []; original = subprocess.Popen
        def popen(command, **options):
            child = original([sys.executable, '-c', 'import time; time.sleep(30)'], **options)
            children.append(child); return child
        timer = threading.Timer(.3, event.set); timer.start(); started = time.monotonic()
        try:
            with patch('studio_song_sections.subprocess.Popen', side_effect=popen), cancel_on(event):
                with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                    _run_worker(self.source, self.root, lambda _: None)
        finally: timer.cancel(); timer.join()
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(children), 1); self.assertIsNotNone(children[0].poll())


if __name__ == '__main__': unittest.main()
