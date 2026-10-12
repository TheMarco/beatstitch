"""Held-out evaluation must preserve split integrity and failed attempts."""
import copy
import unittest

from calibrate_key_confidence import KEYS
from validate_key_confidence import check_partition, check_reference, finish_report


class KeyValidationTests(unittest.TestCase):
    def setUp(self):
        self.fit = [{'id': 'train', 'split': 'calibration', 'md5': 'a' * 32, 'key': KEYS[0]}]
        self.validation = [{'id': f'val{i}', 'split': 'validation', 'md5': str(i) * 32, 'key': KEYS[i]}
                           for i in range(2)]

    def test_partition_rejects_selected_records_duplicates_and_shared_audio(self):
        all_records = self.fit + self.validation
        check_partition(self.fit, self.validation, all_records)
        for selected in (self.validation[:1], self.validation + self.validation[:1], self.fit):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                check_partition(self.fit, selected, all_records)
        overlap = copy.deepcopy(self.validation)
        overlap[0]['md5'] = self.fit[0]['md5']
        with self.assertRaisesRegex(ValueError, 'overlapping audio'):
            check_partition(self.fit, overlap, self.fit + overlap)

    def test_reference_parity_checks_excerpt_and_all_key_scores(self):
        reference = {'record': self.fit[0], 'status': 'estimated', 'duration': 60., 'frames': 2646000,
                     'key': KEYS[0], 'exact': True, 'top_three': True,
                     'candidates': [{'key': k, 'softmax': 1 / 24} for k in KEYS]}
        self.assertEqual(check_reference(reference, copy.deepcopy(reference)), 0.)
        changed = copy.deepcopy(reference); changed['duration'] = 30.
        with self.assertRaisesRegex(ValueError, 'duration'):
            check_reference(reference, changed)
        changed = copy.deepcopy(reference)
        changed['candidates'][0]['softmax'] += .01
        changed['candidates'][1]['softmax'] -= .01
        with self.assertRaisesRegex(ValueError, 'scores'):
            check_reference(reference, changed)

    def test_failure_is_counted_and_cannot_yield_a_complete_confidence_measurement(self):
        baseline = {'status': 'finished', 'split': 'validation', 'selected': 2, 'results': [
            {'record': r, 'status': 'estimated', 'duration': 60., 'exact': True} for r in self.validation]}
        report = {'results': [
            {'record': self.validation[0], 'status': 'estimated', 'duration': 60., 'frames': 2646000,
             'exact': True, 'top_three': True},
            {'record': self.validation[1], 'status': 'failed', 'error': 'Decode failed.'}]}
        finish_report(report, baseline, {r['id']: r for r in self.validation}, 1.3)
        self.assertEqual(report['summary']['attempted'], 2)
        self.assertEqual(report['summary']['failed'], 1)
        self.assertEqual(report['summary']['accuracy_of_attempts'], .5)
        self.assertEqual(report['summary']['regressed'], 1)
        self.assertIsNone(report['confidence_metrics'])


if __name__ == '__main__':
    unittest.main()
