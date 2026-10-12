from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import djmix
from studio_cache import source_signature
from studio_engine import Session, Track
import studio_key as key
import studio_key_model as key_model
from studio_process import cancel_on


def observation(path, winner='D minor'):
    candidates = [{'key': name, 'score': .77 if name == winner else .01} for name in sorted(key.KEYS)]
    candidates.sort(key=lambda c: -c['score'])
    return {'version': key.VERSION, 'model_id': key.MODEL_ID, 'source': source_signature(path),
            'state': 'estimated', 'reason': 'Estimated, not guaranteed.', 'excerpt_seconds': 60.,
            'frames': 2646000, 'candidates': candidates}


class KeyDetectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name); self.source = self.root / 'audio.wav'
        self.source.write_bytes(b'fixture audio identity')
        self.track = Track('a', str(self.source), 'Song', 120, 120, cue_out=120, key_origin='pending')

    def test_pinned_official_cpu_wheel_is_accepted_but_other_releases_are_not(self):
        for torch_version in ('2.11.0', '2.11.0+cpu'):
            versions = {'torch': torch_version, 'librosa': '0.11.0'}
            with patch.object(key_model.metadata, 'version', side_effect=versions.__getitem__):
                key_model.verify_runtime()
        for torch_version in ('2.10.0+cpu', '2.12.0', '2.11.0rc1', '2.11.0+unknown'):
            versions = {'torch': torch_version, 'librosa': '0.11.0'}
            with patch.object(key_model.metadata, 'version', side_effect=versions.__getitem__):
                with self.assertRaises(djmix.MixError):
                    key_model.verify_runtime()
        with patch.object(key_model.metadata, 'version', side_effect=key_model.metadata.PackageNotFoundError):
            with self.assertRaises(djmix.MixError):
                key_model.verify_runtime()

    def test_manual_unknown_and_legacy_labels_survive_analysis_and_save(self):
        estimate = observation(self.source)
        key.remember_analysis(self.track, estimate, update_automatic=True)
        self.assertEqual((self.track.key, self.track.key_origin), ('D minor', 'detected'))
        key.set_manual_key(self.track, 'C major')
        key.remember_analysis(self.track, estimate, update_automatic=True)
        self.assertEqual((self.track.key, self.track.key_origin), ('C major', 'manual'))
        key.set_manual_key(self.track, '')
        key.remember_analysis(self.track, estimate, update_automatic=True)
        session = Session(tracks={'a': self.track})
        restored = Session.from_dict(session.to_dict()).tracks['a']
        self.assertEqual((restored.key, restored.key_origin), ('', 'manual'))
        legacy = session.to_dict(); legacy['version'] = 12
        legacy['tracks']['a'].pop('key_origin'); legacy['tracks']['a'].pop('key_analysis')
        legacy['tracks']['a']['key'] = 'E minor'
        old = Session.from_dict(legacy).tracks['a']
        key.remember_analysis(old, estimate, update_automatic=True)
        self.assertEqual((old.key, old.key_origin), ('E minor', 'saved'))
        legacy['tracks']['a']['key'] = ''
        unknown = Session.from_dict(legacy).tracks['a']
        key.remember_analysis(unknown, estimate, update_automatic=True)
        self.assertEqual((unknown.key, unknown.key_origin), ('', 'saved'))
        key.accept_estimate(old)
        self.assertEqual((old.key, old.key_origin), ('D minor', 'detected'))
        self.source.write_bytes(b'changed audio')
        self.assertFalse(key.current_analysis(old))
        with self.assertRaises(djmix.MixError): key.accept_estimate(old)

    def test_worker_results_and_cache_reject_changed_inputs(self):
        cache = self.root / 'library-cache' / 'keys'
        with patch.object(key, '_run_worker', side_effect=lambda *_: observation(self.source)) as worker, \
                patch.object(key, 'verify_model'), \
                patch.object(key.djmix, 'run', return_value=SimpleNamespace(stdout=b'fixed decoder')):
            first = key.analyze_key(self.source, cache)
            self.assertTrue(key.valid_result(first))
            self.assertEqual(key.analyze_key(self.source, cache), first)
            self.assertEqual(worker.call_count, 1)
            self.source.write_bytes(b'another source')
            key.analyze_key(self.source, cache)
            self.assertEqual(worker.call_count, 2)
            with patch.object(key, 'MODEL_ID', 'next model'):
                key.analyze_key(self.source, cache)
            self.assertEqual(worker.call_count, 3)
        for change in ('score', 'duplicate', 'frames'):
            malformed = observation(self.source)
            if change == 'score': malformed['candidates'][0]['score'] = float('nan')
            elif change == 'duplicate': malformed['candidates'][1]['key'] = malformed['candidates'][0]['key']
            else: malformed['frames'] += 1
            self.assertFalse(key.valid_result(malformed))

    def test_real_child_is_reaped_when_cancelled(self):
        launch = subprocess.Popen
        children, event = [], threading.Event()

        def waiting_child(command, **options):
            child = launch([sys.executable, '-c', 'import time; time.sleep(20)'], **options)
            children.append(child); event.set()
            return child

        with cancel_on(event), patch.object(key.subprocess, 'Popen', side_effect=waiting_child):
            with self.assertRaisesRegex(djmix.MixError, 'Operation cancelled'):
                key._run_worker(self.source, self.root)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertFalse((self.root / 'result.json').exists())

    def test_detector_upgrade_keeps_saved_labels_but_requires_a_fresh_estimate(self):
        self.track.key, self.track.key_origin = 'F major', 'detected'
        self.track.key_analysis = observation(self.source)
        self.track.key_analysis['model_id'] = 'previous-detector'
        restored = Session.from_dict(Session(tracks={'a': self.track}).to_dict()).tracks['a']
        self.assertEqual(restored.key, 'F major')
        self.assertFalse(key.current_analysis(restored))
        with self.assertRaises(djmix.MixError): key.accept_estimate(restored)
        key.remember_analysis(restored, observation(self.source))
        self.assertEqual(restored.key, 'F major')
        key.accept_estimate(restored)
        self.assertEqual(restored.key, 'D minor')

    def test_engine_keeps_manual_key_on_reanalysis_and_detector_failure(self):
        from studio_engine import estimate_track
        key.set_manual_key(self.track, 'F major')
        baseline = {'candidate_windows': [{'bpm': 120., 'cue_seconds': 0., 'grid_residual_p95_ms': 1}],
                    'section_key_candidates': [{'key': 'A minor'}]}
        with patch('studio_structure.analyze_tempo', return_value={'bpm': None}), \
                patch('studio_structure.analyze_song', return_value={}), \
                patch('analyze_audio.analyze', return_value=baseline), \
                patch('studio_timing.analyze', return_value={'bpm': 120.}), \
                patch.object(key, 'analyze_key', return_value=observation(self.source)):
            estimate_track(self.track, self.root / 'timing-cache')
        self.assertEqual((self.track.key, self.track.key_origin), ('F major', 'manual'))
        with patch('studio_structure.analyze_tempo', return_value={'bpm': None}), \
                patch('studio_structure.analyze_song', return_value={}), \
                patch('analyze_audio.analyze', return_value=baseline), \
                patch('studio_timing.analyze', return_value={'bpm': 120.}), \
                patch.object(key, 'analyze_key', side_effect=djmix.MixError('Detector unavailable')):
            estimate_track(self.track, self.root / 'timing-cache')
        self.assertEqual(self.track.key, 'F major')
        self.assertEqual(self.track.key_analysis['state'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
