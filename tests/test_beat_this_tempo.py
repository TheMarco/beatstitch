import unittest

import numpy as np

from prototype_beat_this_tempo import compare
from studio_beat_tempo import tempo_from_beats


class BeatTempoTests(unittest.TestCase):
    def test_quantized_beats_preserve_slow_fast_and_fractional_tempos(self):
        for bpm in (64., 127.4, 174., 256.):
            beats = np.round((.17 + np.arange(160) * 60 / bpm) * 50) / 50
            result = tempo_from_beats(beats, float(beats[-1] + 1))
            self.assertAlmostEqual(result['bpm'], bpm, delta=.15)

    def test_missing_and_extra_beats_do_not_become_a_new_tempo(self):
        beats = .2 + np.arange(100) * .5
        beats = np.delete(beats, np.arange(32, 45))
        beats = np.sort(np.r_[beats, 6.4])
        result = tempo_from_beats(beats, 51.)
        self.assertAlmostEqual(result['bpm'], 120.)
        self.assertLess(result['coverage'], .85)
        self.assertTrue(all(not (w['start'] < 16.2 and w['end'] > 22.2) for w in result['windows']))

    def test_insufficient_or_invalid_observations_do_not_invent_a_bpm(self):
        for beats in ([], [0., .5], np.arange(17) * .5):
            self.assertIsNone(tempo_from_beats(beats, 10.)['bpm'])
        for beats in ([0., 0.], [1., .5], [float('nan')], [-1.], [10.]):
            with self.assertRaises(ValueError): tempo_from_beats(beats, 10.)

    def test_failed_candidate_counts_as_regression_in_paired_comparison(self):
        record = {'id': 'one'}
        baseline = [{'record': record, 'score': {'accuracy1_4pct': True}}]
        result = compare(baseline, [{'record': record, 'status': 'failed'}])
        self.assertEqual(result['regressed'], ['one'])
        with self.assertRaises(ValueError):
            compare(baseline, [{'record': {'id': 'one', 'bpm': 128}}])


if __name__ == '__main__':
    unittest.main()
