import copy
import hashlib
import io
import unittest
import zipfile

from benchmark_content import catalog, metrics, parse_labels, score, summarize, validate_resume
from studio_content import MODEL_SHA256, VERSION, state


def observations():
    windows = []
    for start, voice in [(0., .8), (5., .7), (10., .01)]:
        scores = {'voice': voice, 'drums': .1, 'pitched': .4, 'music': .9}
        windows.append({'start': start, 'end': start + 10, 'scores': scores,
                        'rms_db': -18., 'state': state(scores, -18.)})
    return {'version': VERSION, 'model_sha256': MODEL_SHA256, 'duration': 20., 'windows': windows}


class ContentBenchmarkTests(unittest.TestCase):
    def test_merged_prompts_are_not_double_counted_and_gaps_stay_unknown(self):
        labels = parse_labels('0 3 nosing\n3 7 sing\n9 20 nosing\n')
        result = score(observations(), labels)
        self.assertEqual({k: result['seconds'][k] for k in ('tp', 'fp', 'fn', 'tn')},
                         {'tp': 4., 'fp': 9., 'fn': 0., 'tn': 5.})
        self.assertEqual(result['unannotated_seconds'], 2.)
        self.assertEqual(result['excluded_windows'], 2)
        self.assertEqual(result['windows']['tn'], 1)

    def test_any_voice_window_scoring_is_distinct_from_boundary_precision(self):
        result = score(observations(), parse_labels('0 9.5 nosing\n9.5 10 sing\n10 20 nosing'))
        self.assertEqual(result['windows']['tp'], 2)
        self.assertEqual(result['windows']['fp'], 0)
        self.assertEqual(result['seconds']['tp'], .5)
        self.assertEqual(result['seconds']['fp'], 14.5)
        self.assertLess(result['seconds']['precision'], .04)

    def test_invalid_labels_and_out_of_audio_reference_are_rejected(self):
        for text in ('', '0 1 music', '-1 1 sing', '0 nan sing', '0 2 sing\n1 3 nosing'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_labels(text)
        with self.assertRaisesRegex(ValueError, 'beyond'):
            score(observations(), parse_labels('0 21 sing'))
        result = score(observations(), parse_labels('0 20.05 nosing'))
        self.assertEqual(result['annotated_seconds'], 20.)

    def test_missing_denominators_are_unknown_and_failures_remain_visible(self):
        self.assertIsNone(metrics(dict.fromkeys(('tp', 'fp', 'fn', 'tn'), 0))['precision'])
        result = summarize([{'status': 'failed'}], 61)
        self.assertEqual((result['measured'], result['failed'], result['pending']), (0, 1, 60))
        self.assertIsNone(result['seconds']['recall'])

    def test_catalog_never_reads_reserved_annotations_or_audio(self):
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, 'w') as archive:
            for split, count in [('train', 61), ('valid', 16), ('test', 16)]:
                names = [f'{split}-{i}.ogg' for i in range(count)]
                archive.writestr('jamendo/filelists/' + split, '\n'.join(names))
                if split == 'train':
                    for name in names:
                        archive.writestr('jamendo/audio/' + name, b'fixture')
                        archive.writestr('jamendo/labels/' + name[:-4] + '.lab', '0 10 sing')
        with zipfile.ZipFile(memory) as archive:
            records = catalog(archive)
        self.assertEqual(len(records), 61)
        self.assertTrue(all(r['name'].startswith('train-') for r in records))

    def test_resume_rejects_label_score_or_selection_changes(self):
        annotations = '0 20 sing'
        record = {'name': 'song', 'labels_sha256': hashlib.sha256(annotations.encode()).hexdigest()}
        plan = {'records': [record]}
        row = {'record': record, 'status': 'measured', 'annotation_text': annotations,
               'analysis': observations(), 'score': score(observations(), parse_labels(annotations))}
        report = {'plan_sha256': 'abc', 'results': [row]}
        validate_resume(report, plan, 'abc')
        for field in ('labels', 'score', 'selection'):
            changed = copy.deepcopy(report)
            if field == 'labels': changed['results'][0]['annotation_text'] = '0 20 nosing'
            if field == 'score': changed['results'][0]['score']['seconds']['tp'] = 999
            if field == 'selection': changed['results'][0]['record']['name'] = 'another'
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_resume(changed, plan, 'abc')


if __name__ == '__main__':
    unittest.main()
