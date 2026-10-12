import copy
import unittest

from benchmark_tempo import score, summarize
from validate_tempo_detectors import check_development, check_resume


class TempoValidationTests(unittest.TestCase):
    def setUp(self):
        self.detector = {'model': 'fixture'}
        self.records = [{'id': str(i), 'bpm': 128., 'split': 'calibration'} for i in range(2)]
        self.rows = [{'record': r, 'detector': self.detector, 'status': 'estimated',
                      'initial_bpm': 128., 'bpm': 128., 'initial_score': score(128., 128.),
                      'score': score(128., 128.), 'timing': {'state': 'steady', 'bpm': None}}
                     for r in self.records]
        self.baseline_plan = {'records': self.records, 'detector': self.detector}
        self.candidate_plan = {**self.baseline_plan, 'baseline_plan_sha256': 'baseline'}
        self.baseline = {'status': 'finished', 'selected': 2, 'split': 'calibration',
                         'plan_sha256': 'baseline', 'detector': self.detector, 'results': self.rows}
        self.candidate = {**self.baseline, 'plan_sha256': 'candidate', 'baseline_results': self.rows}

    def test_requires_complete_unchanged_development_and_identical_pairs(self):
        def check(baseline, candidate, records=None, detector=None):
            check_development(baseline, self.baseline_plan, candidate, self.candidate_plan,
                              records if records is not None else self.records,
                              detector or self.detector, self.detector)
        check(self.baseline, self.candidate)
        for change in ('unfinished', 'missing', 'changed_pair', 'new_detector', 'wrong_partition'):
            before, after = copy.deepcopy(self.baseline), copy.deepcopy(self.candidate)
            records, detector = self.records, self.detector
            if change == 'unfinished': before['status'] = 'running'
            elif change == 'missing': after['results'] = after['results'][:1]
            elif change == 'changed_pair': before['results'][0]['bpm'] = 64.
            elif change == 'new_detector': detector = {'model': 'changed'}
            else: records = [{**r, 'split': 'test'} for r in self.records]
            with self.subTest(change=change), self.assertRaises(ValueError):
                check(before, after, records, detector)

    def test_resume_preserves_failed_pairs_and_cannot_drop_or_reorder_recordings(self):
        records = [{**r, 'split': 'validation'} for r in self.records]
        plan = {'records': records, 'baseline_detector': self.detector, 'candidate_detector': self.detector}
        failure = {'record': records[0], 'detector': self.detector, 'status': 'failed'}
        report = {'status': 'running', 'split': 'validation', 'selected': 2,
                  'plan_sha256': 'plan', 'baseline_results': [failure], 'results': [failure]}
        check_resume(report, plan, 'plan')
        self.assertEqual(summarize(report['results'])['accuracy_denominator'], 1)
        for change in ('unpaired', 'reordered', 'premature_finish', 'test_partition', 'new_plan'):
            altered = copy.deepcopy(report)
            if change == 'unpaired': altered['baseline_results'] = []
            elif change == 'reordered': altered['results'][0]['record'] = records[1]
            elif change == 'premature_finish': altered['status'] = 'finished'
            elif change == 'test_partition': altered['split'] = 'test'
            else: altered['plan_sha256'] = 'changed'
            with self.subTest(change=change), self.assertRaises(ValueError):
                check_resume(altered, plan, 'plan')


if __name__ == '__main__':
    unittest.main()
