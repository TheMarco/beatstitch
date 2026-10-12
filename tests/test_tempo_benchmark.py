import unittest

from benchmark_tempo import score, summarize


class TempoBenchmarkTests(unittest.TestCase):
    def test_octave_matches_are_not_reported_as_correct_source_bpm(self):
        for estimate, label in [(64, 'half'), (256, 'double'), (128 / 3, 'third'), (384, 'triple')]:
            value = score(estimate, 128)
            self.assertFalse(value['accuracy1_4pct'])
            self.assertFalse(value['within_0_5_bpm'])
            self.assertTrue(value['accuracy2_4pct'])
            self.assertEqual(value['tempo_multiple'], label)
        self.assertFalse(score(180, 128)['accuracy2_4pct'])

    def test_tight_bpm_accuracy_is_distinct_from_standard_four_percent(self):
        self.assertTrue(score(128.5, 128)['within_0_5_bpm'])
        self.assertFalse(score(128.501, 128)['within_0_5_bpm'])
        self.assertTrue(score(133.12, 128)['accuracy1_4pct'])
        self.assertFalse(score(133.121, 128)['accuracy1_4pct'])
        for estimate, reference in [(0, 128), (128, 0), (float('nan'), 128), (128, float('inf'))]:
            with self.assertRaises(ValueError): score(estimate, reference)

    def test_uncertain_refinement_does_not_hide_a_wrong_fallback_bpm(self):
        row = {'status': 'estimated', 'initial_score': score(64, 128), 'score': score(64, 128),
               'timing': {'state': 'uncertain', 'bpm': None}}
        value = summarize([row, {'status': 'failed'}, {'status': 'abstained'}])
        self.assertEqual(value['accuracy_denominator'], 3)
        self.assertEqual(value['estimated'], 1)
        self.assertEqual(value['final']['within_0_5_bpm'], 0)
        self.assertEqual(value['final']['accuracy2_4pct'], 1)
        self.assertEqual(value['refinement_used'], 0)
        self.assertEqual(value['timing_states'], {'uncertain': 1})

if __name__ == '__main__':
    unittest.main()
