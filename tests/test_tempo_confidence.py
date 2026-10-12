"""Confidence must distinguish tempo errors without using labels as features."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from benchmark_tempo import score
from evaluate_tempo_confidence import check_resume, confidence_result
from select_tempo_confidence import (feature_row, fit, load_partition, predict,
                                     select)


class TempoConfidenceTests(unittest.TestCase):
    def beat(self, bpm=120.):
        return {'bpm': bpm, 'beat_tempo': {'bpm': bpm, 'coverage': .8,
                                         'window_bpm_p10_p90': [119., 121.]}}

    def test_features_use_only_observations_and_keep_octave_disagreement(self):
        beat = self.beat()
        opening = {'status': 'estimated', 'initial_bpm': 120.}
        original = feature_row(beat, opening)
        beat.update(record={'bpm': 5000}, score={'accuracy1_4pct': False})
        opening.update(record={'bpm': 3000}, score={'accuracy1_4pct': True})
        self.assertEqual(original, feature_row(beat, opening))
        for bpm in (60., 240.):
            opening['initial_bpm'] = bpm
            self.assertEqual(feature_row(beat, opening)[2], 0.)
        self.assertEqual(feature_row(beat, {'status': 'failed'})[2], 0.)
        for value in (float('nan'), -1, 1.01):
            beat['beat_tempo']['coverage'] = value
            with self.assertRaises(ValueError):
                feature_row(beat, opening)

    def test_controlled_correctness_signal_produces_finite_monotone_estimates(self):
        values = np.repeat([[.1, .2, .2], [.5, .6, .6], [.9, .99, 1.]], 100, axis=0)
        outcomes = np.concatenate([np.arange(100) < hits for hits in (20, 55, 95)])
        for name in ('coverage', 'beats', 'agreement'):
            model = fit(values, outcomes, name)
            predicted = predict(values, model)
            self.assertTrue(np.isfinite(predicted).all())
            self.assertTrue(np.all(np.diff(predicted) >= 0))
            self.assertGreater(predicted[-1] - predicted[0], .25)
            self.assertLess(np.mean((predicted-outcomes)**2), np.mean((outcomes.mean()-outcomes)**2))
            model['coefficients'][0] = -1
            with self.assertRaises(ValueError):
                predict(values, model)
        with self.assertRaises(ValueError):
            fit(values, np.ones(len(values)), 'beats')
        with self.assertRaises(ValueError):
            predict([[np.nan, 0, 0]], {'name': 'coverage', 'coefficients': [1, 0]})

    def test_final_test_rejected_before_any_prediction_file_is_opened(self):
        with patch('select_tempo_confidence.read_planned_report') as read:
            with self.assertRaisesRegex(ValueError, 'final-test'):
                load_partition('never-read', None, 'never-read', 'test')
            read.assert_not_called()

    def fixture(self):
        records = [{'id': str(i), 'bpm': 120., 'md5': str(i)*32, 'split': 'validation'} for i in range(2)]
        detector, opening_detector = {'fixed': 'beat'}, {'fixed': 'opening'}
        beats = [dict(self.beat(), record=records[0], status='estimated', detector=detector,
                      initial_bpm=120., initial_score=score(120., 120.), score=score(120., 120.)),
                 dict(record=records[1], status='abstained', detector=detector)]
        openings = [dict(record=r, status='estimated', detector=opening_detector,
                         initial_bpm=120., bpm=120., initial_score=score(120., 120.), score=score(120., 120.))
                    for r in records]
        report = {'status': 'finished', 'selected': 2, 'split': 'validation',
                  'results': beats, 'baseline_results': openings}
        plan = {'split': 'validation', 'records': records, 'candidate_detector': detector,
                'baseline_detector': opening_detector}
        return report, plan, records, detector

    def test_partition_guards_keep_abstentions_and_reject_inconsistent_evidence(self):
        report, plan, records, detector = self.fixture()
        def load(data=report, frozen=plan):
            with patch('select_tempo_confidence.annotations', return_value=records), patch(
                    'select_tempo_confidence.read_planned_report', return_value=(data, frozen)):
                return load_partition(Path('validation'), None, 'archive', 'validation', detector)
        x, y, ids, counts, *_ = load()
        self.assertEqual(x.shape, (1, 3))
        self.assertEqual(y.tolist(), [1])
        self.assertEqual(ids, ['0'])
        self.assertEqual(counts, {'attempted': 2, 'estimated': 1, 'abstained': 1, 'failed': 0})
        cases = []
        bad = copy.deepcopy(report); bad['results'].pop(); cases.append(bad)
        bad = copy.deepcopy(report); bad['results'][0]['score']['accuracy1_4pct'] = False; cases.append(bad)
        bad = copy.deepcopy(report); bad['results'][0]['detector'] = {}; cases.append(bad)
        bad = copy.deepcopy(report); bad['baseline_results'].reverse(); cases.append(bad)
        bad = copy.deepcopy(report); bad['results'][0]['beat_tempo']['bpm'] = 60.; cases.append(bad)
        for case in cases:
            with self.subTest(case=cases.index(case)), self.assertRaises(ValueError):
                load(case)
        bad_plan = copy.deepcopy(plan); bad_plan['records'][0]['split'] = 'test'
        with self.assertRaises(ValueError):
            load(frozen=bad_plan)

    def test_selection_requires_both_metrics_and_prefers_simpler_ties(self):
        measured = {name: {'brier': .2, 'binary_log_loss': .5}
                    for name in ('constant', 'coverage', 'beats', 'agreement')}
        self.assertIsNone(select(measured))
        measured['agreement']['brier'] = .1
        self.assertIsNone(select(measured))
        measured['agreement']['binary_log_loss'] = .4
        self.assertEqual(select(measured), 'agreement')
        measured['coverage'] = dict(measured['agreement'])
        self.assertEqual(select(measured), 'coverage')

    def test_reserved_run_cannot_resume_changed_or_incomplete_evidence_as_finished(self):
        report, plan, records, _ = self.fixture()
        for record in records:
            record['split'] = 'test'
        report.update(split='test', plan_sha256='frozen', status='finished')
        check_resume(report, plan, 'frozen')
        with self.assertRaises(ValueError):
            check_resume(report, plan, 'changed')
        report['results'].pop()
        with self.assertRaises(ValueError):
            check_resume(report, plan, 'frozen')
        report['baseline_results'].pop()
        with self.assertRaisesRegex(ValueError, 'entire reserved'):
            check_resume(report, plan, 'frozen')
        report['status'] = 'partial'
        check_resume(report, plan, 'frozen')

    def test_final_acceptance_needs_complete_data_benefit_and_no_failed_records(self):
        after, before = [], []
        for i in range(120):
            correct = i % 3 != 0
            beat = self.beat(120. if correct else 60.)
            beat.update(record={'id': str(i)}, status='estimated',
                        score={'accuracy1_4pct': correct})
            after.append(beat)
            before.append({'status': 'estimated', 'initial_bpm': 120.})
        model = {'name': 'agreement', 'coefficients': [0., 0., 8., -4.]}
        measured = confidence_result(before, after, model, .7, complete=True)
        self.assertTrue(measured['acceptance_passed'])
        self.assertEqual(sum(b['count'] for b in measured['bands']), 120)
        self.assertLess(measured['brier_difference']['paired_bootstrap_95'][1], 0.)
        self.assertFalse(confidence_result(before, after, model, .7, complete=False)['acceptance_passed'])
        self.assertFalse(confidence_result(before[:50], after[:50], model, .7, complete=True)['acceptance_passed'])
        before[0]['status'] = 'failed'
        self.assertFalse(confidence_result(before, after, model, .7, complete=True)['acceptance_passed'])


if __name__ == '__main__':
    unittest.main()
