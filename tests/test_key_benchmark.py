"""Ensure benchmark exclusions and download errors cannot inflate the evidence."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from benchmark_key_confidence import REVISION, annotations, download, summarize, wilson


class KeyBenchmarkTests(unittest.TestCase):
    def test_failure_and_abstention_are_retained_in_denominator(self):
        rows = [
            {'status': 'estimated', 'exact': True, 'top_three': True, 'candidates': [{'correlation': .9}]},
            {'status': 'estimated', 'exact': False, 'top_three': True, 'candidates': [{'correlation': .85}]},
            {'status': 'abstained'}, {'status': 'failed'}]
        result = summarize(rows)
        self.assertEqual(result['accuracy_of_estimates'], .5)
        self.assertEqual(result['correct_fraction_of_attempts'], .25)
        self.assertEqual((result['failed'], result['abstained'], result['top_three']), (1, 1, 2))
        self.assertEqual(result['correlation_bins'][-1]['accuracy'], .5)
        self.assertIsNone(result['correlation_bins'][0]['accuracy'])

    def test_empty_results_do_not_imply_perfect_accuracy(self):
        result = summarize([])
        self.assertIsNone(result['accuracy_of_estimates'])
        self.assertIsNone(result['wilson_95'])
        lower, upper = wilson(1, 1)
        self.assertLess(lower, .21)
        self.assertEqual(upper, 1.)

    def test_annotation_import_groups_duplicate_audio_and_normalizes_enharmonics(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'annotations.zip'
            prefix = f'giantsteps-key-dataset-{REVISION}/'
            with zipfile.ZipFile(path, 'w') as archive:
                for i in range(604):
                    identity = f'{100000 + i}.LOFI'
                    key = 'Bb minor' if i % 2 else 'A# minor'
                    digest = hashlib.md5(str(max(1, i)).encode()).hexdigest()
                    archive.writestr(prefix + 'annotations/key/' + identity + '.key', key)
                    archive.writestr(prefix + 'md5/' + identity + '.md5', digest)
            rows = {row['id']: row for row in annotations(path)}
            self.assertEqual(rows['100000.LOFI']['key'], rows['100001.LOFI']['key'])
            self.assertNotEqual(rows['100000.LOFI']['original_label'], rows['100001.LOFI']['original_label'])
            self.assertEqual(rows['100000.LOFI']['split'], rows['100001.LOFI']['split'])
            self.assertEqual({row['split'] for row in rows.values()}, {'calibration', 'validation', 'test'})

    def test_bad_download_never_publishes_unverified_audio(self):
        with tempfile.TemporaryDirectory() as folder, patch('urllib.request.urlopen', return_value=io.BytesIO(b'bad')):
            path = Path(folder) / 'song.mp3'
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                download('https://example.invalid/audio', path, maximum=10, md5=hashlib.md5(b'good').hexdigest())
            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix('.mp3.download').exists())

    def test_existing_partial_download_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder, patch('urllib.request.urlopen', return_value=io.BytesIO(b'new')):
            path = Path(folder) / 'song.mp3'
            partial = path.with_suffix('.mp3.download'); partial.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):
                download('https://example.invalid/audio', path, maximum=10)
            self.assertEqual(partial.read_bytes(), b'keep')


if __name__ == '__main__': unittest.main()
