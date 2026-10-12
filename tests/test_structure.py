"""Observed bar support, ambiguity, source-sensitive reuse and cue provenance."""
import copy
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from studio_cache import digest, source_signature
from studio_structure import MODEL_SHA256, analyze_song, analyze_tempo, coherent_grid, cue_suggestions, current_tempo_estimate


def observations():
    beats = 1 + np.arange(120) * .5
    downbeats = beats[::4]
    return beats, downbeats, np.full(len(beats), .94), np.full(len(downbeats), .96)


class StructureTests(unittest.TestCase):
    def test_extra_bar_detections_cannot_displace_supported_quarter_beats(self):
        beats, downbeats, _, _ = observations()
        extra = downbeats + .2
        beats = np.sort(np.r_[beats, extra])
        guesses = np.sort(np.r_[downbeats, extra])
        strengths = np.where(np.isin(guesses, extra), .999, .95)
        result = coherent_grid(beats, guesses, np.full(len(beats), .94), strengths, 120, 62)
        self.assertGreaterEqual(len(result['bars']), 25)
        np.testing.assert_allclose(result['downbeats'], downbeats)
        self.assertAlmostEqual(result['bpm'], 120)

    def test_missing_passages_and_low_evidence_do_not_get_invented_bars(self):
        b, d, bs, ds = observations()
        keep_b = (b < 20) | (b > 34)
        keep_d = (d < 20) | (d > 34)
        result = coherent_grid(b[keep_b], d[keep_d], bs[keep_b], ds[keep_d], 120, 62)
        self.assertTrue(result['bars'])
        self.assertTrue(all(bar['end'] <= 20 or bar['start'] >= 34 for bar in result['bars']))
        self.assertLess(result['coverage'], .8)
        weak = coherent_grid(b, d, np.full_like(bs, .51), ds, 120, 62)
        self.assertEqual(weak['state'], 'uncertain')
        self.assertEqual(weak['bars'], [])

    def test_half_double_tempo_and_invalid_observations_are_explicit(self):
        args = observations()
        result = coherent_grid(*args, 60, 62)
        self.assertEqual(result['tempo_relation'], 'half-double')
        self.assertEqual(result['bars'], [])
        with self.assertRaises(ValueError):
            coherent_grid(args[0][::-1], *args[1:], 120, 62)
        with self.assertRaises(ValueError):
            coherent_grid(*args, float('nan'), 62)

    def test_cues_preserve_tail_option_and_report_removed_audio(self):
        result = coherent_grid(*observations(), 120, 62)
        result['boundaries'] = [{'time': 48., 'label': 'Energy falls'}]
        before = copy.deepcopy(result)
        cues = cue_suggestions(result, 62)
        self.assertEqual(cues[0]['time'], 62)
        ending = next(c for c in cues if c['time'] == 48)
        self.assertIn('14.0 seconds', ending['reason'])
        self.assertTrue(all(0 <= c['time'] <= 62 for c in cues))
        self.assertEqual(result, before)

    def test_result_cache_reuses_observations_after_bpm_edit_and_invalidates_changed_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'song.wav'
            source.write_bytes(b'fixture identity')
            b, d, bs, ds = observations()
            observations_file = root / (digest([1, MODEL_SHA256, source_signature(source)]) + '.npz')
            np.savez(observations_file, beats=b, downbeats=d, beat_strength=bs, downbeat_strength=ds,
                     logmel=np.ones((3100, 8)), rms=np.full(3100, .1))
            with patch('studio_structure.djmix.probe', return_value={'duration': 62}), \
                    patch('studio_structure._infer', side_effect=AssertionError('Must reuse observations')):
                independent = analyze_tempo(source, 62, root)
                first = analyze_song(source, 120, 62, root)
                again = analyze_song(source, 120, 62, root)
                half = analyze_song(source, 60, 62, root)
            self.assertEqual(first, again)
            self.assertEqual(half['tempo_relation'], 'half-double')
            self.assertEqual(first['tempo_estimate']['bpm'], 120)
            self.assertEqual(independent['bpm'], first['tempo_estimate']['bpm'])
            self.assertEqual(independent['source'], source_signature(source))
            self.assertEqual(half['tempo_estimate'], first['tempo_estimate'])
            track = SimpleNamespace(path=str(source), duration=62, bpm=60, structure=first)
            self.assertEqual(current_tempo_estimate(track)['bpm'], 120)
            invalid = copy.deepcopy(first)
            invalid['tempo_estimate']['bpm'] = float('nan')
            track.structure = invalid
            self.assertEqual(current_tempo_estimate(track), {})
            track.structure = first
            source.write_bytes(b'changed audio identity')
            self.assertEqual(current_tempo_estimate(track), {})
            with patch('studio_structure.djmix.probe', return_value={'duration': 62}), \
                    patch('studio_structure._infer', side_effect=RuntimeError('Fresh inference needed')):
                with self.assertRaisesRegex(RuntimeError, 'Fresh inference needed'):
                    analyze_song(source, 120, 62, root)


if __name__ == '__main__':
    unittest.main()
