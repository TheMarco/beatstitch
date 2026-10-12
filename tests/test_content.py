"""Passage observations must remain bounded, tentative and non-destructive."""
import copy
import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QPushButton

import djmix
from studio_cache import source_signature
from studio_content import (MODEL_SHA256, VERSION, analyze_content, current_content, cue_candidates,
                            drum_regions, voice_regions, state, valid_result)
from studio_engine import Session, layout
from studio_source_identity import rebind_analysis


def observations(track):
    windows = []
    for a in range(0, 31, 5):
        scores = {'voice': .02, 'drums': .65 if a <= 5 or a >= 25 else .1, 'pitched': .04, 'music': .8}
        windows.append({'start': float(a), 'end': float(a + 10), 'scores': scores,
                        'rms_db': -18., 'state': state(scores, -18.)})
    return {'version': VERSION, 'model_sha256': MODEL_SHA256, 'source': source_signature(track.path),
            'duration': track.duration, 'windows': windows}


class ContentTests(unittest.TestCase):
    def test_voice_ranges_keep_gaps_and_never_turn_unknown_or_quiet_into_absence(self):
        def window(start, voice, rms=-18):
            scores = {'voice': voice, 'drums': .1, 'pitched': .3, 'music': .8}
            return {'start': start, 'end': start + 10, 'scores': scores, 'rms_db': rms, 'state': state(scores, rms)}
        result = {'windows': [window(0, .7), window(5, .6), window(10, .01),
                              window(15, .9, -80), window(25, .8)]}
        self.assertEqual(voice_regions(result), [{'start': 0, 'end': 15, 'observations': 2},
                                                {'start': 25, 'end': 35, 'observations': 1}])
        self.assertFalse(voice_regions({'windows': [window(0, .01)]}))

    def test_real_model_finds_authored_drum_sections_and_cache_avoids_inference(self):
        from studio_projects import read_project
        from studio_demo import bundled_project
        from studio_render_cache import audio_recipe
        session, _ = read_project(bundled_project()); track = next(iter(session.tracks.values()))
        before = audio_recipe(session)['fingerprint']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = analyze_content(track.path, track.duration, root)
            track.content = result
            self.assertEqual(audio_recipe(session)['fingerprint'], before)
            self.assertTrue(valid_result(result, track.duration))
            regions = drum_regions(result)
            self.assertEqual(len(regions), 2)
            self.assertEqual(regions[0]['start'], 0)
            self.assertLessEqual(regions[0]['end'], 16)
            self.assertGreater(regions[1]['start'], 40)
            self.assertFalse(any(w['state'] == 'drum_led' for w in result['windows'] if 20 <= w['start'] <= 30))
            with patch('studio_content_model.ContentModel', side_effect=AssertionError('Must reuse observations')):
                self.assertEqual(analyze_content(track.path, track.duration, root), result)
            self.assertFalse(list(root.glob('.pending-*')))
            # A corrupt cache is discarded; inference must run again.
            next(root.glob('*.json')).write_text('{"key": []}')
            with patch('studio_content_model.ContentModel', side_effect=RuntimeError('Fresh observations required')):
                with self.assertRaisesRegex(RuntimeError, 'Fresh observations required'):
                    analyze_content(track.path, track.duration, root)

    def test_voice_unknown_and_quiet_observations_cannot_be_bridged(self):
        from tests.test_transitions import project
        with tempfile.TemporaryDirectory() as temporary:
            track = project().tracks['1']; path = Path(temporary) / 'song.wav'; path.write_bytes(b'fixture'); track.path = str(path)
            result = observations(track)
            self.assertEqual([(r['start'], r['end']) for r in drum_regions(result)], [(0, 10), (30, 40)])
            score = {'voice': .7, 'drums': .9, 'pitched': 0., 'music': .8}
            self.assertEqual(state(score, -18), 'voice')
            self.assertEqual(state({**score, 'voice': .1}, -18), 'uncertain')
            self.assertEqual(state({**score, 'voice': .01}, -90), 'quiet')
            result['windows'][1]['scores']['voice'] = .7; result['windows'][1]['state'] = 'voice'
            self.assertEqual([(r['start'], r['end']) for r in drum_regions(result)], [(30, 40)])
            track.content = result; self.assertTrue(current_content(track))
            old = result['source']; moved = path.with_name('moved.wav'); shutil.copy2(path, moved)
            path.write_bytes(b'changed fixture'); self.assertFalse(current_content(track))
            track.path = str(moved)
            rebind_analysis(track, old, source_signature(moved)); self.assertTrue(current_content(track))
            result['windows'][0]['scores']['voice'] = float('nan'); self.assertFalse(valid_result(result, 40))

    def test_cancel_removes_partial_audio_and_observations(self):
        from studio_projects import read_project
        from studio_demo import bundled_project
        session, _ = read_project(bundled_project()); track = next(iter(session.tracks.values()))
        def cancel(message):
            if 'passage 2 of' in message: raise djmix.MixError('Operation cancelled.')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                analyze_content(track.path, track.duration, root, cancel)
            self.assertFalse(list(root.glob('*.json')))
            self.assertFalse(list(root.glob('.pending-*')))


class PassageEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_candidate_is_explicit_draft_with_one_undo_and_protection_limits(self):
        from desktop_app import Studio
        from tests.test_transitions import project
        from studio_beat_grid import grid_settings
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bands = patch.object(Studio, 'load_bands'); bands.start()
            w = Studio(root)
            try:
                w.session = project(); track = w.session.tracks['1']
                path = root / 'song.wav'; path.write_bytes(b'fixture'); track.path = str(path)
                track.beat_grid = grid_settings(track, 120, 0)
                # A manual grid is enough to align passage cues; no beat inference needed.
                track.structure = {'retained': True}; track.content = observations(track)
                clip = w.session.clips[1]; clip.cue_in = 2; clip.cue_out = 35; clip.blend_bars = 8; clip.intro_percussive = True
                w.selected_id = clip.id; w.refresh(); before = copy.deepcopy(w.session.to_dict())
                w.review_song_cues(protect=True); d = w.cue_review
                candidates = cue_candidates(d.session, 1, track.content)
                candidate = next(c for c in candidates if c['kind'] == 'start')
                self.assertEqual((candidate['time'], candidate['overlap']), (0, 4))
                d.try_passage(candidate)
                self.assertEqual(w.session.to_dict(), before)
                self.assertEqual(d.clip.blend_bars, 4)
                self.assertFalse(d.clip.intro_percussive)
                self.assertIn('Drums-only label off', d.status.text())
                d.accept_cues()
                self.assertEqual(w.session.clips[1].cue_in, 0)
                self.assertEqual(w.session.clips[1].blend_bars, 4)
                self.assertFalse(w.session.clips[1].intro_percussive)
                self.assertEqual(len(w.history), 1)
                self.assertIn('cues and overlaps', w.history[-1]['action'])
                w.undo(); self.assertEqual(w.session.to_dict(), before)
                protected = copy.deepcopy(w.session)
                protected.clips[1].protected_moments = [{'id': 'x', 'start': 3., 'end': 9., 'kind': 'favorite', 'label': 'Keep this'}]
                self.assertFalse(any(c['kind'] == 'start' for c in cue_candidates(protected, 1, track.content)))
                saved = w.session.to_dict(); self.assertEqual(Session.from_dict(saved).to_dict(), saved)
                saved['version'] = 9
                for t in saved['tracks'].values(): t.pop('content')
                self.assertFalse(Session.from_dict(saved).tracks['1'].content)
            finally:
                if w.cue_review is not None: w.cue_review.reject()
                w.close()
                bands.stop()


if __name__ == '__main__': unittest.main()
