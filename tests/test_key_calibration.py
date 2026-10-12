"""Calibration must preserve decisions and never fit selected or held-out data."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from calibrate_key_confidence import (KEYS, checked_probabilities, fit_temperature,
                                      load_development_report, metrics, scaled_probabilities)


class KeyCalibrationTests(unittest.TestCase):
    def test_overconfident_predictions_improve_without_changing_key_rankings(self):
        values = np.full((100, 24), .01 / 23)
        values[:, 0] = .99
        targets = np.array([0, 1] * 50)
        temperature = fit_temperature(values, targets)
        calibrated = scaled_probabilities(values, temperature)
        before, after = metrics(values, targets), metrics(calibrated, targets)
        self.assertGreater(temperature, 1)
        self.assertLess(after['negative_log_likelihood'], before['negative_log_likelihood'])
        self.assertLess(after['top_label_brier'], before['top_label_brier'])
        np.testing.assert_array_equal(values.argmax(axis=1), calibrated.argmax(axis=1))
        np.testing.assert_allclose(calibrated.max(axis=1), .5, atol=1e-6)
        self.assertEqual(after['exact'], 50)
        self.assertEqual(sum(b['count'] for b in after['bins']), 100)

    def test_invalid_scores_temperatures_and_reference_labels_are_rejected(self):
        valid = np.full((2, 24), 1 / 24)
        for bad in (np.ones((2, 24)), np.full((2, 24), np.nan), np.empty((0, 24)), valid[:, :23]):
            with self.subTest(shape=bad.shape), self.assertRaises(ValueError):
                checked_probabilities(bad)
        for temperature in (0, -1, float('nan'), float('inf')):
            with self.subTest(temperature=temperature), self.assertRaises(ValueError):
                scaled_probabilities(valid, temperature)
        for target in ([24, 0], [-1, 0], [0., 1.], [0]):
            with self.subTest(target=target), self.assertRaises(ValueError):
                metrics(valid, target)

    def test_fitting_requires_complete_disjoint_development_records(self):
        records = [{'id': f'{i}.LOFI', 'md5': str(i) * 32, 'key': KEYS[i],
                    'original_label': KEYS[i], 'split': 'calibration'} for i in range(2)]
        valid = {'status': 'finished', 'split': 'calibration', 'selected': 2, 'results': [
            {'record': record, 'status': 'estimated',
             'candidates': [{'key': key, 'softmax': 1 / 24} for key in reversed(KEYS)]}
            for record in records]}
        with tempfile.TemporaryDirectory() as directory, patch('calibrate_key_confidence.annotations', return_value=records):
            path = Path(directory) / 'report.json'
            path.write_text(json.dumps(valid))
            _, values, targets = load_development_report(path, 'unused-annotations.zip')
            self.assertEqual(values.shape, (2, 24))
            np.testing.assert_array_equal(targets, [0, 1])
            cases = []
            value = copy.deepcopy(valid); value['split'] = 'validation'; cases.append(value)
            value = copy.deepcopy(valid); value['results'].pop(); value['selected'] = 1; cases.append(value)
            value = copy.deepcopy(valid); value['results'][1] = value['results'][0]; cases.append(value)
            value = copy.deepcopy(valid); value['results'][0]['record']['md5'] = 'f' * 32; cases.append(value)
            value = copy.deepcopy(valid); value['results'][0]['status'] = 'failed'; cases.append(value)
            value = copy.deepcopy(valid); value['results'][0]['candidates'][1]['key'] = KEYS[-1]; cases.append(value)
            for index, value in enumerate(cases):
                path.write_text(json.dumps(value))
                with self.subTest(case=index), self.assertRaises(ValueError):
                    load_development_report(path, 'unused-annotations.zip')


if __name__ == '__main__':
    unittest.main()
