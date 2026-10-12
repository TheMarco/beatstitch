"""Correctness calibration must remain monotone and respect reserved data."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from calibrate_key_confidence import KEYS
from select_key_confidence import (apply_map, binary_metrics, fit_map, load_predictions,
                                    select_candidate)


class KeyConfidenceSelectionTests(unittest.TestCase):
    def test_controlled_overconfidence_and_identity(self):
        scores = np.repeat([.6, .8, .95], 100)
        outcomes = np.concatenate([np.arange(100) < hits for hits in (40, 60, 80)])
        for method in ('logit', 'beta'):
            model = fit_map(scores, outcomes, method)
            adjusted = apply_map(scores, model)
            self.assertTrue(np.all(np.diff(adjusted) >= 0))
            for metric in ('brier', 'binary_log_loss'):
                self.assertLess(binary_metrics(adjusted, outcomes)[metric],
                                binary_metrics(scores, outcomes)[metric])
        for method, coefficients in (('logit', [1, 0]), ('beta', [1, 1, 0])):
            np.testing.assert_allclose(apply_map(scores, {'method': method, 'coefficients': coefficients}), scores)
        model = fit_map(np.full(100, .99), np.arange(100) < 50, 'logit')
        np.testing.assert_allclose(apply_map([.99], model), [.5], atol=1e-6)

    def test_binary_metrics_and_invalid_inputs(self):
        measured = binary_metrics([.8, .2], [1, 0])
        self.assertAlmostEqual(measured['brier'], .04)
        self.assertAlmostEqual(measured['binary_log_loss'], -np.log(.8))
        self.assertEqual(sum(b['count'] for b in measured['bins']), 2)
        for scores, outcomes in (([], []), ([np.nan], [1]), ([1.1], [0]), ([.5], [2]), ([.5], [0, 1])):
            with self.subTest(scores=scores), self.assertRaises(ValueError):
                binary_metrics(scores, outcomes)
        with self.assertRaises(ValueError):
            fit_map([.4, .9], [1, 1], 'beta')
        with self.assertRaises(ValueError):
            apply_map([.5], {'method': 'logit', 'coefficients': [-1, 0]})

    def test_partition_and_prediction_identity_checks(self):
        record = {'id': 'one', 'md5': 'a' * 32, 'key': KEYS[0], 'split': 'validation'}
        detector = {'model': 'fixed'}
        candidates = [{'key': key, 'softmax': .77 if i == 0 else .01} for i, key in enumerate(KEYS)]
        valid = {'status': 'finished', 'split': 'validation', 'selected': 1, 'reference_detector': detector,
                 'results': [{'record': record, 'status': 'estimated', 'duration': 60., 'frames': 2646000,
                              'key': KEYS[0], 'exact': True, 'candidates': candidates}]}
        with tempfile.TemporaryDirectory() as directory, patch('select_key_confidence.annotations', return_value=[record]):
            path = Path(directory) / 'data.json'
            path.write_text(json.dumps(valid))
            _, scores, correct, _ = load_predictions(path, 'unused', 'validation', detector)
            self.assertEqual(scores.shape, (1, 24)); self.assertTrue(correct[0])
            with self.assertRaises(ValueError):
                load_predictions(path, 'unused', 'test', detector)
            with self.assertRaises(ValueError):
                load_predictions(path, 'unused', 'calibration', detector)
            cases = []
            bad = copy.deepcopy(valid); bad['results'].append(bad['results'][0]); cases.append(bad)
            bad = copy.deepcopy(valid); bad['results'][0]['exact'] = False; cases.append(bad)
            bad = copy.deepcopy(valid); bad['results'][0]['key'] = KEYS[1]; cases.append(bad)
            bad = copy.deepcopy(valid); bad['results'][0]['frames'] -= 1; cases.append(bad)
            bad = copy.deepcopy(valid); bad['results'][0]['record']['md5'] = 'b' * 32; cases.append(bad)
            bad = copy.deepcopy(valid); bad['reference_detector'] = {'model': 'changed'}; cases.append(bad)
            bad = copy.deepcopy(valid); bad['results'][0]['candidates'][1]['key'] = KEYS[0]; cases.append(bad)
            for i, bad in enumerate(cases):
                path.write_text(json.dumps(bad))
                with self.subTest(case=i), self.assertRaises(ValueError):
                    load_predictions(path, 'unused', 'validation', detector)

    def test_selection_requires_both_metrics_to_beat_both_baselines(self):
        measured = {name: {'brier': .2, 'binary_log_loss': .5}
                    for name in ('raw', 'constant', 'temperature', 'logit', 'beta')}
        self.assertIsNone(select_candidate(measured))
        measured['beta']['brier'] = .1
        self.assertIsNone(select_candidate(measured))
        measured['beta']['binary_log_loss'] = .4
        self.assertEqual(select_candidate(measured), 'beta')
        measured['logit'] = dict(measured['beta'])
        self.assertEqual(select_candidate(measured), 'logit')
        measured['constant']['brier'] = .05
        self.assertIsNone(select_candidate(measured))


if __name__ == '__main__':
    unittest.main()
