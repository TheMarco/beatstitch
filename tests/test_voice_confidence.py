"""Evidence boundaries for exploratory voice-score calibration; no audio inference."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

import calibrate_voice_confidence as confidence
from benchmark_content import catalog, file_hash, score
from select_key_confidence import apply_map
from studio_content import GROUPS, MODEL_SHA256, VERSION, state
from validate_voice_threshold import measure, reserved_catalog


def observations(voice=.1):
    scores = {'voice': voice, 'drums': .5, 'pitched': .1, 'music': .8}
    return {'version': VERSION, 'model_sha256': MODEL_SHA256, 'duration': 20,
            'windows': [{'start': start, 'end': start + 10, 'scores': dict(scores),
                         'rms_db': -20, 'state': state(scores, -20)} for start in (0, 5, 10)]}


class VoiceConfidenceMathTests(unittest.TestCase):
    def test_annotation_gaps_are_excluded_and_labels_do_not_change_features(self):
        audio = observations()
        one = confidence.window_examples(audio, '0 20 sing\n')
        zero = confidence.window_examples(audio, '0 20 nosing\n')
        self.assertEqual(one['scores'], zero['scores'])
        self.assertEqual(one['outcomes'], [1, 1, 1]); self.assertEqual(zero['outcomes'], [0, 0, 0])
        gap = confidence.window_examples(audio, '0 5 sing\n6 20 nosing\n')
        self.assertEqual(gap['excluded_windows'], 2); self.assertEqual(gap['outcomes'], [0])
        with self.assertRaisesRegex(ValueError, 'fully annotated'):
            confidence.window_examples(audio, '0 1 nosing\n')

    def test_invalid_scores_and_unrepaired_annotation_overrun_are_rejected(self):
        audio = observations(); audio['windows'][0]['scores']['voice'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'Invalid saved'):
            confidence.window_examples(audio, '0 20 sing\n')
        with self.assertRaisesRegex(ValueError, 'beyond'):
            confidence.window_examples(observations(), '0 30 nosing\n')

    def test_fit_is_monotone_and_evaluation_does_not_refit(self):
        songs = [{'scores': [.01, .1, .2, .8], 'outcomes': [0, 1, 1, 1], 'excluded_windows': 0},
                 {'scores': [.01, .1, .2, .8], 'outcomes': [0, 0, 1, 0], 'excluded_windows': 0}]
        fitted = confidence.frozen_model(songs); before = copy.deepcopy(fitted)
        mapped = apply_map(np.linspace(0, 1, 30), fitted['model'])
        self.assertTrue(np.all(np.diff(mapped) >= 0))
        with patch.object(confidence, 'fit_map', side_effect=AssertionError('No held-out fitting')):
            result = confidence.evaluate(songs, fitted)
        self.assertEqual(fitted, before)
        self.assertEqual(result['windows'], 8)
        self.assertEqual(sum(b['windows'] for b in result['mapped']['bins']), 8)

    def test_uncertainty_resamples_songs_not_individual_overlapping_windows(self):
        frozen = {'model': {'method': 'logit', 'coefficients': [0., 0.]}, 'training_prevalence': .5}
        songs = [{'scores': [.2] * 3, 'outcomes': [label] * 3, 'excluded_windows': 0} for label in (0, 1)]
        result = confidence.evaluate(songs, frozen)
        repeated = [{**s, 'scores': s['scores'] * 50, 'outcomes': s['outcomes'] * 50} for s in songs]
        again = confidence.evaluate(repeated, frozen)
        self.assertEqual(result['mapped']['bins'][5]['contributing_songs'], 2)
        self.assertEqual(result['mapped']['bins'][5]['song_bootstrap_95'], [0., 1.])
        np.testing.assert_allclose(result['paired_brier']['raw']['song_bootstrap_95'],
                                   again['paired_brier']['raw']['song_bootstrap_95'])


class VoiceConfidenceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.archive_path = self.root / 'fixture.zip'
        with zipfile.ZipFile(self.archive_path, 'w') as archive:
            for split, count in [('train', 61), ('valid', 16), ('test', 16)]:
                names = [f'{split}-{i}.ogg' for i in range(count)]
                archive.writestr('jamendo/filelists/' + split, '\n'.join(names))
                for i, name in enumerate(names):
                    archive.writestr('jamendo/audio/' + name, (split + ':' + str(i)).encode())
                    archive.writestr('jamendo/labels/' + name.replace('.ogg', '.lab'),
                                     '0 20 sing\n' if i % 2 else '0 20 nosing\n')
        self.archive = zipfile.ZipFile(self.archive_path); self.addCleanup(self.archive.close)
        self.archive_sha = file_hash(self.archive_path)
        self.detector = {'version': VERSION, 'model_sha256': MODEL_SHA256, 'rate': 32000,
                         'window': 10, 'step': 5, 'groups': {k: list(v) for k, v in GROUPS.items()},
                         'sources': {'fixture': 'same'}, 'runtime': {'fixture': 'same'}}
        self.paths = {}
        for phase in ('train', 'validation', 'test'):
            records = catalog(self.archive) if phase == 'train' else reserved_catalog(self.archive, phase)
            plan = {'split' if phase == 'train' else 'phase': phase, 'records': records,
                    'detector': self.detector, 'archive_sha256': self.archive_sha}
            rows = []
            for i, record in enumerate(records):
                raw = self.archive.read(record['labels']).decode()
                labels = confidence.parse_labels(raw)
                audio = observations(.3 if i % 3 else .03)
                row = {'record': record, 'status': 'measured', 'analysis': audio, 'annotation_text': raw,
                       'audio_sha256': hashlib.sha256(self.archive.read(record['audio'])).hexdigest()}
                if phase == 'train': row['score'] = score(audio, labels)
                else: row.update(baseline=measure(audio, labels, .2), candidate=measure(audio, labels, .08))
                rows.append(row)
            path = self.root / (phase + '.json'); self.paths[phase] = path
            self.write(path, {'status': 'finished', 'results': rows}, plan)

    def write(self, path, report, plan):
        plan_path = path.with_suffix('.plan.json'); plan_path.write_text(json.dumps(plan))
        report['plan_sha256'] = file_hash(plan_path); path.write_text(json.dumps(report))

    def load(self, phase, previous=()):
        return confidence.load_partition(self.paths[phase], self.archive, phase, self.archive_sha,
                                         self.detector, previous)

    def test_full_partitions_pass_and_reuse_of_a_song_is_rejected(self):
        train, _ = self.load('train'); valid, _ = self.load('validation', train)
        test, _ = self.load('test', train + valid)
        self.assertEqual([len(train), len(valid), len(test)], [61, 16, 16])
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            self.load('validation', train + valid[:1])

    def test_incomplete_failed_or_duplicate_results_cannot_disappear(self):
        path = self.paths['train']; original, plan = confidence.load_report(path)
        for change in ('missing', 'failed', 'duplicate'):
            with self.subTest(change=change):
                report = copy.deepcopy(original)
                if change == 'missing': report['results'].pop()
                elif change == 'failed': report['results'][0]['status'] = 'failed'
                else: report['results'][0] = copy.deepcopy(report['results'][1])
                self.write(path, report, plan)
                with self.assertRaises(ValueError): self.load('train')

    def test_changed_audio_labels_or_detector_are_rejected(self):
        path = self.paths['validation']; original, original_plan = confidence.load_report(path)
        for change in ('audio', 'labels', 'detector'):
            with self.subTest(change=change):
                report, plan = copy.deepcopy(original), copy.deepcopy(original_plan)
                if change == 'audio': report['results'][0]['audio_sha256'] = 'changed'
                elif change == 'labels': report['results'][0]['annotation_text'] = '0 20 sing\n'
                else: plan['detector']['step'] = 4
                self.write(path, report, plan)
                with self.assertRaises(ValueError): self.load('validation')

    def test_only_the_documented_missing_nonvoice_tail_can_be_corrected(self):
        path = self.paths['test']; report, plan = confidence.load_report(path)
        row = report['results'][0]; raw = row['annotation_text']
        revised, correction = confidence.corrected_tail(raw, 12)
        row['original_annotation_text'] = raw; row['annotation_text'] = revised
        audio = row['analysis']; audio['duration'] = 12
        audio['windows'] = [audio['windows'][0], {**copy.deepcopy(audio['windows'][0]), 'start': 2, 'end': 12}]
        correction.update(source=row['record']['name'], audio_sha256=row['audio_sha256'],
                          original_labels_sha256=row['record']['labels_sha256'],
                          revised_labels_sha256=hashlib.sha256(revised.encode()).hexdigest())
        row['record']['labels_sha256'] = correction['revised_labels_sha256']
        plan['records'][0] = row['record']; report['reference_correction'] = plan['reference_correction'] = correction
        labels = confidence.parse_labels(revised)
        row.update(baseline=measure(audio, labels, .2), candidate=measure(audio, labels, .08))
        self.write(path, report, plan)
        songs, _ = self.load('test'); self.assertEqual(len(songs), 16)
        row['annotation_text'] = '0 1 sing\n1 12 nosing\n'
        row['record']['labels_sha256'] = hashlib.sha256(row['annotation_text'].encode()).hexdigest()
        correction['revised_labels_sha256'] = row['record']['labels_sha256']
        self.write(path, report, plan)
        with self.assertRaisesRegex(ValueError, 'preserve every earlier'):
            self.load('test')

    def test_failure_after_freezing_is_recorded_without_claiming_completion(self):
        out = self.root / 'failed.json'; original_loader = confidence.load_partition
        def fail_validation(path, archive, phase, *args, **kwargs):
            if phase == 'validation': raise ValueError('Unusable held-out evidence')
            return original_loader(path, archive, phase, *args, **kwargs)
        with patch.object(confidence, 'ARCHIVE_BYTES', self.archive_path.stat().st_size), \
                patch.object(confidence, 'ARCHIVE_MD5', file_hash(self.archive_path, 'md5')), \
                patch.object(confidence, 'load_partition', side_effect=fail_validation):
            with self.assertRaisesRegex(ValueError, 'Unusable'):
                confidence.run(self.archive_path, self.paths['train'], self.paths['validation'], self.paths['test'], out)
        result = json.loads(out.read_text())
        self.assertEqual(result['status'], 'failed'); self.assertEqual(list(result['partitions']), ['train'])
        self.assertFalse(result['ui_promotion_allowed'])

    def test_map_is_saved_before_held_out_scores_are_read_and_output_is_not_overwritten(self):
        out = self.root / 'experiment.json'
        original_loader = confidence.load_partition
        held_out = []
        def guarded(path, archive, phase, *args, **kwargs):
            if phase != 'train':
                plan = json.loads(out.with_suffix('.plan.json').read_text())
                self.assertIn('model', plan); self.assertEqual(plan['training_songs'], 61)
                self.assertFalse(plan['ui_promotion_allowed']); held_out.append(phase)
            return original_loader(path, archive, phase, *args, **kwargs)
        with patch.object(confidence, 'ARCHIVE_BYTES', self.archive_path.stat().st_size), \
                patch.object(confidence, 'ARCHIVE_MD5', file_hash(self.archive_path, 'md5')), \
                patch.object(confidence, 'load_partition', side_effect=guarded):
            report = confidence.run(self.archive_path, self.paths['train'], self.paths['validation'], self.paths['test'], out)
            before = out.read_bytes()
            with self.assertRaisesRegex(ValueError, 'fresh output'):
                confidence.run(self.archive_path, self.paths['train'], self.paths['validation'], self.paths['test'], out)
            self.assertEqual(out.read_bytes(), before)
        self.assertEqual(held_out, ['validation', 'test'])
        self.assertEqual(report['status'], 'exploratory_complete'); self.assertFalse(report['ui_promotion_allowed'])


if __name__ == '__main__': unittest.main()
