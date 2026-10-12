import copy
import unittest

from benchmark_beat_tempo_incremental import validate_baseline, validate_resume
from benchmark_tempo import score


class IncrementalBeatTempoTests(unittest.TestCase):
    def setUp(self):
        self.records = [{'id': str(i), 'bpm': 128.} for i in range(3)]
        self.detector = {'model': 'fixture'}
        self.plan = {'records': self.records, 'detector': self.detector}
        self.row = {'record': self.records[0], 'detector': self.detector, 'status': 'estimated',
                    'bpm': 64., 'initial_bpm': 64., 'score': score(64., 128.), 'initial_score': score(64., 128.)}
        self.baseline = {'plan_sha256': 'plan', 'detector': self.detector, 'selected': 3,
                         'split': 'calibration', 'status': 'running', 'results': [self.row]}

    def test_partial_baseline_is_allowed_but_cannot_claim_finished_or_skip_a_failure(self):
        validate_baseline(self.baseline, self.plan, 'plan')
        self.baseline['status'] = 'finished'
        with self.assertRaises(ValueError): validate_baseline(self.baseline, self.plan, 'plan')
        self.baseline['status'] = 'running'
        self.baseline['results'].append({'record': self.records[2], 'status': 'failed'})
        with self.assertRaises(ValueError): validate_baseline(self.baseline, self.plan, 'plan')

    def test_changed_selection_detector_and_optimistic_scores_are_rejected(self):
        for change in ('record', 'detector', 'score'):
            altered = copy.deepcopy(self.baseline)
            if change == 'record': altered['results'][0]['record']['bpm'] = 64.
            elif change == 'detector': altered['detector']['model'] = 'different'
            else: altered['results'][0]['score']['accuracy1_4pct'] = True
            with self.assertRaises(ValueError): validate_baseline(altered, self.plan, 'plan')

    def test_resume_keeps_failures_and_requires_unchanged_paired_evidence(self):
        report = {'plan_sha256': 'plan', 'detector': self.detector, 'selected': 3,
                  'results': [{'record': self.records[0], 'detector': self.detector, 'status': 'failed'}],
                  'baseline_results': copy.deepcopy(self.baseline['results'])}
        validate_resume(report, self.plan, 'plan', self.baseline)
        self.baseline['results'][0]['bpm'] = 128.
        with self.assertRaises(ValueError): validate_resume(report, self.plan, 'plan', self.baseline)


if __name__ == '__main__':
    unittest.main()
