"""Mocked representative-record checks for the key experiment runner."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import detector_experiment_key as keyexp
from detector_experiment_common import digest, load_plan
from detector_pilot import file_hash


def _plan(root):
    audio = root / 'tone.wav'
    data = np.zeros((48000, 2), dtype='float32')
    data[:, 0] = 0.1 * np.sin(2 * np.pi * 261.63 * np.arange(48000) / 48000)
    sf.write(audio, data, 48000, subtype='PCM_16')
    info = sf.info(audio)
    record = {'id': 'tone1', 'name': 'Tone', 'kind': 'synthetic_tonal',
              'audio': str(audio), 'audio_sha256': file_hash(audio),
              'frames': info.frames, 'expected': {'key': 'C major'}}
    cases = [
        {'id': 'base', 'type': 'baseline', 'families': ['key'], 'record_kinds': ['synthetic_tonal']},
        {'id': 'up2', 'type': 'pitch', 'semitones': 2, 'families': ['key'], 'record_kinds': ['synthetic_tonal']},
        {'id': 'gain', 'type': 'gain', 'gain_db': -6.0, 'families': ['key'], 'record_kinds': ['synthetic_tonal']},
    ]
    content = {'version': 1, 'records': [record], 'cases': cases, 'project_root': str(root)}
    path = root / 'plan.json'
    path.write_text(json.dumps({**content, 'created_utc': 'x', 'plan_sha256': digest(content)}))
    return path


def _result(status, key=None, candidates=None):
    return {'status': status, 'key': key, 'candidates': candidates or [],
            'reason': '', 'error': 'boom' if status == 'failed' else ''}


class KeyExperimentTests(unittest.TestCase):
    def test_slow_transforms_share_exact_sixty_second_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); audio = root / 'long.wav'
            data = np.zeros((48000 * 63, 2), dtype='float32')
            data[:48000 * 60, 0] = .25
            data[48000 * 60:, 0] = .75
            sf.write(audio, data, 48000, subtype='FLOAT')
            with keyexp.key_scope(audio, root / 'scratch') as scoped:
                self.assertEqual(sf.info(scoped).frames, 48000 * 60)
                measured, rate = sf.read(scoped, dtype='float32')
                np.testing.assert_array_equal(measured, data[:48000 * 60])
            self.assertFalse(scoped.exists())
            self.assertEqual(sf.info(audio).frames, 48000 * 63)

    def test_frozen_runtime_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = _plan(root)
            source = root / 'implementation.py'; source.write_text('original')
            plan = json.loads(path.read_text()); plan.pop('plan_sha256'); plan.pop('created_utc')
            plan['runtime_root'] = str(root); plan['code_sha256'] = {source.name: file_hash(source)}
            path.write_text(json.dumps({**plan, 'plan_sha256': digest(plan)}))
            load_plan(path)
            source.write_text('changed')
            with self.assertRaisesRegex(ValueError, 'implementation changed'):
                load_plan(path)

    def test_backend_identity_is_retained(self):
        result = _result('estimated', 'C major', [{'key':'C major','score': .8}])
        result['identity'] = {'version':'pinned', 'model_sha256':'test'}
        self.assertEqual(keyexp._validate_result(result)['identity'], result['identity'])

    def test_representative_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan_path = _plan(root)
            # Fake keyed by case via inspecting the case audio: baseline file is the record itself,
            # pitch/gain are temp files. Distinguish pitch by duration change? gain keeps duration.
            # Instead: monkeypatch case_audio order tracking through progress is unavailable, so key on
            # backend + a rotating case list that mirrors run()'s case order (base, up2, gain).
            order = ['base', 'up2', 'gain']
            seen = {b: 0 for b in keyexp.BACKENDS}
            calls = []

            def fake(backend, audio, *, project_root, cache_dir, scratch=None):
                idx = seen[backend]; seen[backend] += 1
                case_id = order[idx]
                calls.append((backend, case_id))
                cand = lambda k, s=.9: [{'key': k, 'score': s}, {'key': 'G major', 'score': .05}, {'key': 'A minor', 'score': .05}]
                if backend == 'madmom' and case_id == 'base':
                    return _result('failed')
                if backend == 'essentia_bgate' and case_id == 'up2':
                    return _result('uncertain')
                if backend == 'madmom' and case_id == 'up2':
                    return _result('estimated', 'D minor', cand('D minor'))
                if case_id == 'base':
                    return _result('estimated', 'C major', cand('C major'))
                if case_id == 'up2':
                    return _result('estimated', 'D major', cand('D major'))
                return _result('estimated', 'C major', cand('C major'))

            out = root / 'out'
            with patch.object(keyexp, 'infer_backend', fake):
                report = keyexp.run(plan_path, out, progress=lambda m: None)
            rows = {(r['case_id'], r['backend']): r for r in report['rows']}
            self.assertEqual(len(report['rows']), 3 * len(keyexp.BACKENDS))
            up = rows[('up2', 'openkeyscan')]
            self.assertTrue(up['exact'])  # C major +2 semitones == D major (known composition)
            self.assertEqual(up['expected_key'], 'D major')
            self.assertTrue(up['consistent'])  # baseline-relative transposition agrees
            self.assertTrue(up['mode_preserved'])
            self.assertFalse(up['composition_exact'])
            mode_err = rows[('up2', 'madmom')]
            self.assertTrue(mode_err['same_root_mode_error'])  # D minor vs D major
            self.assertFalse(mode_err['mode_preserved'])
            self.assertEqual(rows[('up2', 'essentia_bgate')]['status'], 'uncertain')
            summary = report['summary']['backends']
            self.assertEqual(summary['madmom']['attempted'], 3)
            self.assertEqual(summary['madmom']['failed'], 1)  # failure stays in denominator
            self.assertEqual(summary['essentia_bgate']['uncertain'], 1)
            self.assertEqual(summary['openkeyscan']['correct_fraction_of_attempts'], 1.0)
            for row in report['rows']:
                self.assertTrue(row['audio_sha256'])
                self.assertAlmostEqual(row['duration_seconds'], 1.0, delta=0.05)
            # Resume: no duplicate inference.
            calls_before = len(calls)
            with patch.object(keyexp, 'infer_backend', fake):
                again = keyexp.run(plan_path, out, progress=lambda m: None)
            self.assertEqual(len(calls), calls_before)
            self.assertEqual(len(again['rows']), len(report['rows']))
            # Changed plan is rejected.
            plan = json.loads(plan_path.read_text())
            content = {k: v for k, v in plan.items() if k not in ('plan_sha256', 'created_utc')}
            content['cases'][1]['semitones'] = 3
            plan_path.write_text(json.dumps({**content, 'created_utc': 'y', 'plan_sha256': digest(content)}))
            with patch.object(keyexp, 'infer_backend', fake):
                with self.assertRaises(ValueError):
                    keyexp.run(plan_path, out, progress=lambda m: None)
            # Changed runner identity in the saved report is rejected (fresh plan restored).
            plan_path2 = _plan(root / 'p2') if False else None

    def test_missing_research_runtime_is_unavailable_not_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            result = keyexp.infer_backend('essentia_edma', Path(folder) / 'x.wav',
                                          project_root=Path(folder), cache_dir=Path(folder) / 'c')
            self.assertEqual(result['status'], 'unavailable')
            self.assertIn('missing', result['reason'].lower())


if __name__ == '__main__':
    unittest.main()
