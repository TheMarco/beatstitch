"""Reused benchmark evidence must not become an independent confidence claim."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import calibrate_openkeyscan_confidence as study
from calibrate_key_confidence import KEYS


class OpenKeyScanConfidenceTests(unittest.TestCase):
    def setUp(self):
        self.detector = {'library': 'OpenKeyScan', 'source_revision': study.SOURCE_REVISION,
                         'model_sha256': study.MODEL_SHA256}
        self.records, rows = [], []
        for i in range(18):
            split = ('calibration', 'validation', 'test')[i // 6]
            score = (.3, .3, .6, .6, .9, .9)[i % 6]
            correct = i % 6 in (1, 2, 4, 5)
            record = {'id': str(i), 'md5': f'{i:032x}', 'key': KEYS[0 if correct else 1],
                      'original_label': KEYS[0 if correct else 1], 'split': split}
            self.records.append(record)
            rows.append({'record': record, 'status': 'estimated', 'duration': 60., 'frames': 2646000,
                         'key': KEYS[0], 'exact': correct,
                         'candidates': [{'key': key, 'softmax': score if j == 0 else (1 - score) / 23}
                                        for j, key in enumerate(KEYS)]})
        self.dev = {'status': 'finished', 'selected': 6, 'split': 'calibration',
                    'detector': self.detector, 'results': rows[:6]}
        self.evaluation = {'status': 'finished', 'selected': 12,
                           'detector': self.detector, 'results': rows[6:]}

    def test_complete_partitions_preserve_saved_predictions_and_correctness(self):
        before = copy.deepcopy(self.evaluation)
        result = study.checked_evaluation(self.evaluation, self.records, self.detector)
        self.assertEqual(set(result), {'validation', 'test'})
        for split, (scores, outcomes, rows) in result.items():
            self.assertEqual(len(scores), 6); self.assertEqual(int(outcomes.sum()), 4)
            self.assertTrue(all(r['record']['split'] == split for r in rows))
        self.assertEqual(self.evaluation, before)

    def test_partial_duplicate_failed_or_changed_reference_predictions_are_rejected(self):
        cases = []
        value = copy.deepcopy(self.evaluation); value['results'].pop(); cases.append(value)
        value = copy.deepcopy(self.evaluation); value['results'][0] = value['results'][1]; cases.append(value)
        value = copy.deepcopy(self.evaluation); value['results'][0]['status'] = 'failed'; cases.append(value)
        value = copy.deepcopy(self.evaluation); value['results'][0]['record']['key'] = KEYS[2]; cases.append(value)
        value = copy.deepcopy(self.evaluation); value['results'][0]['exact'] = True; cases.append(value)
        value = copy.deepcopy(self.evaluation); value['results'][0]['candidates'][-1]['key'] = 'Unknown'; cases.append(value)
        value = copy.deepcopy(self.evaluation); value['detector']['model_sha256'] = 'changed'; cases.append(value)
        for number, value in enumerate(cases):
            with self.subTest(case=number), self.assertRaises(ValueError):
                study.checked_evaluation(value, self.records, self.detector)

    def files(self, root):
        paths = [root / name for name in ('development.json', 'evaluation.json', 'annotations.zip', 'result.json')]
        paths[0].write_text(json.dumps(self.dev)); paths[1].write_text(json.dumps(self.evaluation))
        paths[2].write_bytes(b'controlled annotation fixture')
        return paths

    def test_fit_uses_only_development_and_plan_precedes_evaluation_scoring(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.files(Path(temp)); plan_path = paths[-1].with_suffix('.plan.json')
            checked = study.checked_evaluation
            def observe(*args):
                self.assertTrue(plan_path.is_file())
                plan = json.loads(plan_path.read_text())
                self.assertFalse(plan['ui_promotion_allowed'])
                self.assertEqual({r['split'] for r in plan['fit_records']}, {'calibration'})
                return checked(*args)
            with patch.object(study, 'ARCHIVE_SHA256', study.sha256(paths[2])), \
                    patch.object(study, 'annotations', return_value=self.records), \
                    patch.object(study, 'fit_map', wraps=study.fit_map) as fit, \
                    patch.object(study, 'checked_evaluation', side_effect=observe):
                result = study.run(*paths)
                fit.assert_called_once()
                np.testing.assert_allclose(fit.call_args.args[0], [.3, .3, .6, .6, .9, .9])
                self.assertEqual(result['evaluation_independence'], 'exploratory_reuse')
                self.assertFalse(result['ui_promotion_allowed'])
                self.assertFalse(result['fit_changed_key_predictions'])
                self.assertEqual(result['status'], 'exploratory_complete')
                self.assertEqual(result['plan_sha256'], study.sha256(plan_path))
                with self.assertRaisesRegex(ValueError, 'fresh output'):
                    study.run(*paths)

    def test_duplicate_audio_or_wrong_model_cannot_produce_a_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.files(Path(temp))
            records = copy.deepcopy(self.records); records[-1]['md5'] = records[0]['md5']
            with patch.object(study, 'ARCHIVE_SHA256', study.sha256(paths[2])), \
                    patch.object(study, 'annotations', return_value=records):
                with self.assertRaisesRegex(ValueError, 'Repeated audio'): study.run(*paths)
            self.dev['detector']['library'] = 'Other model'
            paths[0].write_text(json.dumps(self.dev))
            with patch.object(study, 'ARCHIVE_SHA256', study.sha256(paths[2])), \
                    patch.object(study, 'annotations', return_value=self.records):
                with self.assertRaisesRegex(ValueError, 'pinned OpenKeyScan'): study.run(*paths)
            self.assertFalse(paths[-1].with_suffix('.plan.json').exists())

    def test_input_changed_after_freeze_keeps_plan_but_publishes_no_result(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.files(Path(temp)); checked = study.checked_evaluation
            def replace(*args):
                result = checked(*args)
                paths[1].write_text('replaced after reading')
                return result
            with patch.object(study, 'ARCHIVE_SHA256', study.sha256(paths[2])), \
                    patch.object(study, 'annotations', return_value=self.records), \
                    patch.object(study, 'checked_evaluation', side_effect=replace):
                with self.assertRaisesRegex(ValueError, 'input changed'): study.run(*paths)
            self.assertTrue(paths[-1].with_suffix('.plan.json').is_file())
            self.assertFalse(paths[-1].exists())


if __name__ == '__main__': unittest.main()
