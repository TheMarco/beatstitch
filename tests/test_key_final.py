import copy
import unittest

from calibrate_key_confidence import KEYS
from evaluate_key_final import check_test_partition, finish_report


class KeyFinalTests(unittest.TestCase):
    def test_full_reserved_test_partition_and_disjoint_audio(self):
        fit = [{'id': 'fit', 'split': 'calibration', 'md5': 'a'}]
        validation = [{'id': 'val', 'split': 'validation', 'md5': 'b'}]
        test = [{'id': 'one', 'split': 'test', 'md5': 'c'}, {'id': 'two', 'split': 'test', 'md5': 'd'}]
        all_records = fit + validation + test
        check_test_partition(test, all_records, fit)
        for records in (test[:1], test + test[:1], validation):
            with self.assertRaises(ValueError):
                check_test_partition(records, all_records, fit)
        bad = copy.deepcopy(all_records); bad[-1]['md5'] = 'b'
        with self.assertRaises(ValueError):
            check_test_partition(bad[-2:], bad, fit)
        with self.assertRaises(ValueError):
            check_test_partition(test, all_records, fit + validation)

    def test_final_metrics_preserve_failures_and_reject_mismatched_excerpts(self):
        records = [{'id': str(i), 'split': 'test', 'key': KEYS[0], 'md5': str(i)} for i in range(2)]
        baseline = {'status': 'finished', 'split': 'test', 'selected': 2, 'detector': 'fixed', 'results': [
            {'record': record, 'status': 'estimated', 'exact': True, 'duration': 60.,
             'candidates': [{'key': KEYS[0]}]} for record in records]}
        result = {'split': 'test', 'selected': 2, 'results': [
            {'record': records[0], 'status': 'estimated', 'key': KEYS[0], 'exact': True,
             'top_three': True, 'duration': 60., 'frames': 2646000, 'candidates': [
                 {'key': key, 'softmax': .77 if i == 0 else .01} for i, key in enumerate(KEYS)]},
            {'record': records[1], 'status': 'failed', 'error': 'deliberate'}]}
        plan = {'baseline_detector': 'fixed', 'selected_method': 'logit', 'constant': .5,
                'model': {'method': 'logit', 'coefficients': [1, 0]}}
        finish_report(result, baseline, records, plan)
        self.assertEqual(result['summary']['accuracy_of_attempts'], .5)
        self.assertEqual(result['summary']['failed'], 1)
        self.assertIsNone(result['confidence_metrics'])
        bad = copy.deepcopy(baseline); bad['results'][0]['duration'] = 59.
        with self.assertRaises(ValueError):
            finish_report(result, bad, records, plan)
        bad = copy.deepcopy(result); bad['results'][0]['candidates'][1]['key'] = KEYS[0]
        with self.assertRaises(ValueError):
            finish_report(bad, baseline, records, plan)
        result['results'][1] = copy.deepcopy(result['results'][0])
        result['results'][1]['record'] = records[1]
        finish_report(result, baseline, records, plan)
        self.assertEqual(result['summary']['exact'], 2)
        self.assertAlmostEqual(result['confidence_metrics']['measurements']['selected']['brier'], .23 ** 2)
        self.assertTrue(result['confidence_metrics']['key_decisions_unchanged'])


if __name__ == '__main__':
    unittest.main()
