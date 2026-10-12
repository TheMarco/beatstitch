import copy
import hashlib
import unittest

from benchmark_content import parse_labels, score
from tests.test_content_benchmark import observations
from validate_voice_threshold import (BASELINE, CANDIDATE, CRITERIA, approve_test,
                                       measure, summarize, validate_rows)


def row(name='song'):
    analysis = observations()
    analysis['windows'][0]['scores']['voice'] = .09
    analysis['windows'][0]['state'] = 'uncertain'
    annotations = '0 10 sing\n10 20 nosing'
    labels = parse_labels(annotations)
    record = {'name': name, 'labels_sha256': hashlib.sha256(annotations.encode()).hexdigest()}
    return {'status': 'measured', 'record': record, 'analysis': analysis,
            'annotation_text': annotations, 'baseline': measure(analysis, labels, BASELINE),
            'candidate': measure(analysis, labels, CANDIDATE)}


class VoiceThresholdTests(unittest.TestCase):
    def test_reference_repair_only_shortens_an_unavailable_final_nonvoice_tail(self):
        from repair_voice_reference import corrected_tail
        text = '0 5 nosing\n5 18 sing\n18 200 nosing\n'
        revised, correction = corrected_tail(text, 20.)
        self.assertEqual(revised, '0 5 nosing\n5 18 sing\n18 20.000000 nosing\n')
        self.assertEqual(correction['unavailable_nonvoice_seconds'], 180.)
        for invalid in ('0 200 sing', '0 30 sing\n30 200 nosing', '0 20.1 nosing', '0 19 nosing'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                corrected_tail(invalid, 20.)

    def test_baseline_reproduces_existing_scoring_and_candidate_uses_saved_scores(self):
        r = row(); original = score(r['analysis'], parse_labels(r['annotation_text']))
        self.assertEqual(r['baseline']['windows'], original['windows'])
        self.assertEqual(r['baseline']['seconds'], original['seconds'])
        self.assertEqual(r['baseline']['windows']['fn'], 1)
        self.assertEqual(r['candidate']['windows']['fn'], 0)
        self.assertEqual(r['analysis']['windows'][0]['state'], 'uncertain')

    def test_quiet_and_unannotated_windows_do_not_become_voice_evidence(self):
        r = row(); a = r['analysis']
        a['windows'][0].update(rms_db=-80., state='quiet')
        result = measure(a, parse_labels('0 3 sing\n12 20 nosing'), CANDIDATE)
        self.assertEqual(result['excluded_windows'], 3)
        self.assertEqual(result['seconds']['tp'], 0.)
        self.assertEqual(result['unannotated_seconds'], 9.)

    def test_song_bootstrap_is_paired_and_reproducible(self):
        rows = [row(str(i)) for i in range(16)]
        a = summarize(rows, 16); b = summarize(rows, 16)
        self.assertEqual(a, b)
        self.assertEqual(a['paired_recall_gain_interval']['unit'], 'song')
        self.assertEqual(a['paired_recall_gain_interval']['percentile_95'], [.5, .5])
        self.assertTrue(a['acceptance_passed'])
        self.assertFalse(summarize(rows[:-1], 16)['acceptance_passed'])
        self.assertFalse(summarize([*rows[:-1], {'status': 'failed'}], 16)['acceptance_passed'])

    def test_validation_must_pass_without_changing_candidate_before_test(self):
        rows = [row(str(i)) for i in range(16)]
        plan = {'phase': 'validation', 'thresholds': {'baseline': BASELINE, 'candidate': CANDIDATE},
                'criteria': CRITERIA, 'development_sha256': 'development', 'detector': {'id': 'x'},
                'records': [r['record'] for r in rows]}
        report = {'status': 'finished', 'results': rows}
        approve_test(report, plan, 'development', {'id': 'x'})
        for field in ('thresholds', 'criteria', 'phase', 'detector'):
            changed = copy.deepcopy(plan); changed[field] = 'changed'
            with self.subTest(field=field), self.assertRaises(ValueError):
                approve_test(report, changed, 'development', {'id': 'x'})
        report['status'] = 'running'
        with self.assertRaises(ValueError): approve_test(report, plan, 'development', {'id': 'x'})

    def test_saved_labels_and_results_cannot_be_silently_replaced(self):
        r = row(); plan = {'records': [r['record']]}; report = {'results': [r]}
        validate_rows(report, plan)
        changed = copy.deepcopy(report); changed['results'][0]['candidate']['windows']['tp'] += 1
        with self.assertRaises(ValueError): validate_rows(changed, plan)
        changed = copy.deepcopy(report); changed['results'][0]['annotation_text'] = '0 20 nosing'
        with self.assertRaises(ValueError): validate_rows(changed, plan)


if __name__ == '__main__':
    unittest.main()
