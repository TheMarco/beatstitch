import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from ci.check_optional_workers import (RATE, STEMS, check_package, isolated_environment,
                                       run_worker, validate_sections, validate_stems)


class OptionalPackageCheckTests(unittest.TestCase):
    def test_sections_reject_bad_labels_overlap_and_nonfinite_boundaries(self):
        good = {'sections': [{'start': 0., 'end': 12., 'label': 'intro'}], 'device': 'cpu'}
        self.assertEqual(validate_sections(good)['regions'], good['sections'])
        for regions in ([], [{'start': 0., 'end': float('nan'), 'label': 'intro'}],
                        [{'start': False, 'end': 12., 'label': 'intro'}],
                        [{'start': 0., 'end': 13., 'label': 'drop'}],
                        [{'start': 0., 'end': 5., 'label': 'unknown'}],
                        [{'start': 0., 'end': 8., 'label': 'intro'},
                         {'start': 7., 'end': 12., 'label': 'drop'}]):
            with self.subTest(regions=regions), self.assertRaises(ValueError):
                validate_sections({'sections': regions})

    def test_stems_must_be_complete_aligned_finite_and_not_all_silent(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary); frames = 80
            result = {'frames': frames, 'rate': RATE, 'channels': 2}
            for name in STEMS:
                sf.write(work/(name+'.wav'), np.zeros((frames, 2)), RATE, subtype='FLOAT')
            with self.assertRaisesRegex(ValueError, 'silent'):
                validate_stems(result, work, frames)
            sf.write(work/'drums.wav', np.full((frames, 2), .01), RATE, subtype='FLOAT')
            self.assertGreater(validate_stems(result, work, frames)['peak_amplitudes']['drums'], 0)
            for data, rate in ((np.ones((frames - 1, 2)), RATE),
                               (np.ones((frames, 2)), 44100),
                               (np.full((frames, 2), np.nan), RATE)):
                sf.write(work/'bass.wav', data, rate, subtype='FLOAT')
                with self.assertRaisesRegex(ValueError, 'misaligned'):
                    validate_stems(result, work, frames)

    def test_real_worker_error_and_timeout_do_not_pass_and_child_is_reaped(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            command = 'import pathlib; pathlib.Path("result.json").write_text(\'{"error":"missing model"}\')'
            with self.assertRaisesRegex(ValueError, 'missing model'):
                run_worker(Path(sys.executable), ['-c', command], work, work/'worker.log')
            (work/'result.json').unlink()
            children, original = [], subprocess.Popen
            def launch(*args, **kwargs):
                process = original(*args, **kwargs); children.append(process); return process
            with patch('ci.check_optional_workers.subprocess.Popen', side_effect=launch):
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_worker(Path(sys.executable), ['-c', 'import time; time.sleep(20)'],
                               work, work/'timeout.log', timeout=.1)
            self.assertTrue(children)
            self.assertTrue(all(child.poll() is not None for child in children))

    def test_existing_outputs_are_preserved_and_failed_worker_is_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); exe = root/'app'; exe.write_bytes(b'fixture')
            resources = root/'_internal'; resources.mkdir()
            (resources/'section-model-manifest.json').write_text('{}')
            unrelated = root/'unrelated'; unrelated.mkdir()
            with self.assertRaisesRegex(ValueError, 'Resources must belong'):
                check_package(exe, unrelated, root/'not-created', sections=True)
            self.assertFalse((root/'not-created').exists())
            existing = root/'existing'; existing.mkdir()
            sentinel = existing/'important.txt'; sentinel.write_text('Keep')
            with self.assertRaises(FileExistsError):
                check_package(exe, resources, existing, sections=True)
            self.assertEqual(sentinel.read_text(), 'Keep')
            with patch('ci.check_optional_workers.run_worker', side_effect=ValueError('missing dependency')):
                report = check_package(exe, resources, root/'new', sections=True)
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['checks'][0]['status'], 'failed')
            self.assertIn('missing dependency', report['checks'][0]['error'])
            self.assertEqual(json.loads((root/'new/report.json').read_text()), report)

    def test_child_cannot_inherit_developer_tools_or_model_cache(self):
        with patch.dict('os.environ', {'PYTHONPATH': '/developer', 'PATH': '/developer/bin',
                                     'HF_HOME': '/developer/models', 'LD_LIBRARY_PATH': '/developer/lib'}):
            env = isolated_environment(Path('/temporary'))
        self.assertEqual(env['PATH'], '')
        self.assertNotIn('PYTHONPATH', env)
        self.assertNotIn('LD_LIBRARY_PATH', env)
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        self.assertEqual(env['HF_HOME'], str(Path('/temporary/hf')))


if __name__ == '__main__':
    unittest.main()
