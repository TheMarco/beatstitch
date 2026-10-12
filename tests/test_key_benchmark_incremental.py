"""Cached comparisons must not cross detector, excerpt or partition boundaries."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from benchmark_madmom_incremental import checked_baseline, checked_predictions


class IncrementalKeyBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.record = {'id': '1.LOFI', 'key': 'C major', 'md5': 'a' * 32,
                       'original_label': 'C major', 'split': 'calibration'}
        self.identity = {'model_sha256': 'pinned model', 'runner_sha256': 'pinned code'}
        self.baseline = {'1.LOFI': {'record': self.record, 'duration': 60., 'exact': True}}
        self.cached = {'status': 'finished', 'split': 'calibration', 'selected': 1,
                       'detector': self.identity, 'results': [
                           {'record': self.record, 'duration': 60., 'baseline_exact': True,
                            'status': 'estimated', 'key': 'C major'}]}

    def test_valid_cache_reused_but_changed_model_excerpt_or_annotation_refused(self):
        self.assertEqual(set(checked_predictions(self.cached, self.identity, self.baseline)), {'1.LOFI'})
        cases = []
        value = copy.deepcopy(self.cached); value['detector']['model_sha256'] = 'other'; cases.append(value)
        value = copy.deepcopy(self.cached); value['results'][0]['duration'] = 30.; cases.append(value)
        value = copy.deepcopy(self.cached); value['results'][0]['record']['md5'] = 'b' * 32; cases.append(value)
        value = copy.deepcopy(self.cached); value['split'] = 'test'; cases.append(value)
        value = copy.deepcopy(self.cached); value['results'].append(value['results'][0]); cases.append(value)
        for index, value in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                checked_predictions(value, self.identity, self.baseline)

    def test_running_baseline_may_be_partial_but_finished_baseline_must_be_complete(self):
        expected = {'1.LOFI': self.record}
        source = {'status': 'running', 'split': 'calibration', 'selected': 1,
                  'detector': self.identity, 'results': []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            path.write_text(json.dumps(source))
            _, rows = checked_baseline(path, expected, self.identity)
            self.assertFalse(rows)
            source['status'] = 'finished'; path.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, 'every development recording'):
                checked_baseline(path, expected, self.identity)
            source['results'] = list(self.baseline.values()); path.write_text(json.dumps(source))
            _, rows = checked_baseline(path, expected, self.identity)
            self.assertEqual(set(rows), set(expected))

    def test_failed_attempt_without_decoded_duration_remains_available_for_retry(self):
        value = copy.deepcopy(self.cached)
        value['results'][0].update(status='failed', error='Decode failed before duration was recorded.')
        value['results'][0].pop('duration')
        rows = checked_predictions(value, self.identity, self.baseline)
        self.assertEqual(rows['1.LOFI']['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
