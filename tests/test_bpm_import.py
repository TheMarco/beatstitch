"""Import BPM choice, fallbacks, cancellation and source consistency."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import djmix
from studio_engine import Session, Track, estimate_track
from studio_structure import current_opening_tempo


class BPMImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'song.wav'
        self.source.write_bytes(b'fixture audio identity')
        self.track = Track('a', str(self.source), 'Song', 120., key='F minor', key_origin='manual')
        self.opening = patch('analyze_audio.analyze', return_value={'candidate_windows': [
            {'bpm': 131., 'cue_seconds': 2., 'grid_residual_p95_ms': 10.}]})
        self.beats = patch('studio_structure.analyze_tempo', return_value={'bpm': 125.})
        self.timing = patch('studio_timing.analyze', return_value={'bpm': 125.01, 'state': 'steady'})
        self.structure = patch('studio_structure.analyze_song', return_value={'fixture': 'musical analysis'})
        self.key = patch('studio_key.analyze_key', return_value={'state': 'unavailable'})
        for name in ['opening', 'beats', 'timing', 'structure', 'key']:
            patcher = getattr(self, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def run_import(self):
        return estimate_track(self.track, self.root / 'timing-cache')

    def test_beat_estimate_seeds_refinement_and_preserves_manual_key(self):
        track = self.run_import()
        self.assertEqual(track.bpm, 125.01)
        self.assertEqual(self.timing.call_args.args[1], 125.)
        self.assertEqual(self.structure.call_args.args[1], 125.01)
        self.assertEqual(self.beats.call_args.args[2], self.root / 'structure-cache')
        self.assertEqual((track.key, track.key_origin), ('F minor', 'manual'))
        self.assertEqual(track.cue_in, 2.)
        self.assertEqual(current_opening_tempo(track)['bpm'], 131.)
        self.assertIn('Beat-based', track.status)
        self.assertEqual(track.structure, {'fixture': 'musical analysis'})

    def test_opening_alternative_survives_save_and_old_projects_keep_their_bpm(self):
        track = self.run_import()
        project = Session(tracks={track.id: track}).to_dict()
        restored = Session.from_dict(project).tracks[track.id]
        self.assertEqual(current_opening_tempo(restored)['bpm'], 131.)
        self.assertEqual(restored.bpm, 125.01)
        restored.opening_tempo['bpm'] = float('nan')
        self.assertEqual(current_opening_tempo(restored), {})
        project['version'] = 13
        project['tracks'][track.id].pop('opening_tempo')
        legacy = Session.from_dict(project).tracks[track.id]
        self.assertEqual(legacy.bpm, 125.01)
        self.assertEqual(legacy.opening_tempo, {})

    def test_inconclusive_or_unavailable_beats_use_explicit_opening_fallback(self):
        for unavailable in (False, True):
            self.beats.side_effect = djmix.MixError('Model unavailable') if unavailable else None
            self.beats.return_value = {'bpm': None, 'state': 'uncertain'}
            self.timing.return_value = {'bpm': None, 'state': 'uncertain'}
            track = self.run_import()
            self.assertEqual(track.bpm, 131.)
            self.assertEqual(self.timing.call_args.args[1], 131.)
            self.assertIn('Opening-passage', track.status)

    def test_missing_opening_grid_can_still_use_beats_and_keep_original_start(self):
        self.opening.return_value = {'candidate_windows': []}
        track = self.run_import()
        self.assertEqual(track.bpm, 125.01)
        self.assertEqual(track.cue_in, 0.)
        self.beats.return_value = {'bpm': None}
        with self.assertRaisesRegex(djmix.MixError, 'Enter BPM manually'):
            self.run_import()

    def test_cancellation_never_becomes_a_fallback(self):
        for stage in (self.opening, self.beats, self.structure):
            stage.side_effect = djmix.MixError('Operation cancelled.')
            with self.assertRaisesRegex(djmix.MixError, 'Operation cancelled'):
                self.run_import()
            self.assertIsNone(self.track.bpm)
            stage.side_effect = None

    def test_changed_audio_is_rejected_before_applying_tempo_or_cues(self):
        def changed(*args):
            self.source.write_bytes(b'replaced fixture audio identity')
            return {'bpm': 125.}
        self.beats.side_effect = changed
        with self.assertRaisesRegex(djmix.MixError, 'changed during analysis'):
            self.run_import()
        self.assertIsNone(self.track.bpm)
        self.assertEqual(self.track.cue_in, 0.)


if __name__ == '__main__':
    unittest.main()
