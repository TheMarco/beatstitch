"""Focused mocked checks for the content experiment runner; no model inference."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import detector_experiment_content as experiment
from detector_experiment_common import RATE
from detector_pilot import digest, file_hash
from studio_content import MODEL_SHA256, VERSION, state


def write_wav(path, seconds=10):
    audio = np.zeros((round(seconds * RATE), 2), dtype='float32')
    audio[:, 0] = .1 * np.sin(2 * np.pi * 220 * np.arange(len(audio)) / RATE)
    audio[:, 1] = audio[:, 0]
    sf.write(path, audio, RATE, subtype='FLOAT')
    return len(audio)


def analysis(voice=.5):
    scores = {'voice': voice, 'drums': .1, 'pitched': .4, 'music': .8}
    return {'version': VERSION, 'model_sha256': MODEL_SHA256, 'duration': 10.,
            'windows': [{'start': 0., 'end': 10., 'scores': scores, 'rms_db': -18.,
                         'state': state(scores, -18.)}]}


def make_plan(root, records, cases):
    plan = {'version': 1, 'records': records, 'cases': cases}
    plan['plan_sha256'] = digest(plan)
    path = Path(root) / 'plan.json'
    path.write_text(json.dumps(plan))
    return path


def record(root, rid, kind, expected):
    frames = write_wav(Path(root) / f'{rid}.wav')
    return {'id': rid, 'name': rid, 'kind': kind, 'audio': str(Path(root) / f'{rid}.wav'),
            'audio_sha256': file_hash(Path(root) / f'{rid}.wav'), 'frames': frames,
            'expected': expected}


CASES = [{'type': t, 'families': ['content'],
          'record_kinds': ['real', 'synthetic_tonal', 'content_reference'],
          **({'gain_db': -12.} if t == 'gain' else {}),
          **({'offset': 1.} if t == 'prefix' else {})}
         for t in ('baseline', 'gain', 'swap', 'prefix')]

TONAL = {'duration': 8., 'state': 'uncertain', 'keys': [], 'candidates': [], 'tonal': False,
         'harmonic_fraction': .01, 'reason': 'ambiguous'}


class ContentExperimentTests(unittest.TestCase):
    def run_plan(self, records, cases=CASES):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        plan = make_plan(root, records, cases)
        with patch.object(experiment, 'analyze_content', lambda *a, **k: analysis()), \
                patch.object(experiment, 'tonal_evidence', lambda audio, rate=22050: dict(TONAL)):
            return experiment.run(plan, root / 'out', progress=lambda _: None), root

    def test_voice_labeled_gain_and_channel_invariance(self):
        rec = None
        with tempfile.TemporaryDirectory() as td:
            rec = record(td, 'voice', 'content_reference',
                         {'voice': True, 'voice_scope': 'entire_window_any_singing'})
            report, _ = self.run_plan([rec])
        rows = {r['case']: r for r in report['rows']}
        self.assertEqual(rows['baseline']['voice_evaluation']['status'], 'scored')
        self.assertTrue(rows['baseline']['voice_evaluation']['windows'][0]['prompt_08_correct'])
        self.assertEqual(rows['gain']['voice_evaluation']['status'], 'scored')
        self.assertEqual(rows['swap']['voice_evaluation']['status'], 'scored')
        self.assertEqual(report['summary']['threshold_crossings_vs_baseline'], 0)
        self.assertEqual(rows['prefix']['voice_evaluation']['status'], 'consistency_only')

    def test_instrument_only_negative_and_unknown_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            inst = record(td, 'inst', 'synthetic_tonal',
                          {'voice': False, 'voice_scope': 'entire_window_any_singing',
                           'pitched': True, 'key': 'A minor'})
            unknown = record(td, 'unk', 'real', {})
            report, _ = self.run_plan([inst, unknown],
                                      [c for c in CASES if c['type'] == 'baseline'])
        rows = {r['record']: r for r in report['rows']}
        self.assertEqual(rows['inst']['voice_evaluation']['label_voice_any_singing'], False)
        self.assertEqual(rows['unk']['voice_evaluation']['status'], 'excluded')
        # Ambiguous local key never proves drums-only on a pitched synthetic.
        self.assertTrue(rows['inst']['tonal']['missed_constructed_tonal_evidence'])
        self.assertFalse(rows['inst']['tonal']['drums_only_proven'])
        self.assertEqual(rows['inst']['tonal']['concentration'], 'unavailable (production tonal_evidence does not return it)')

    def test_resume_rejects_changed_runner_and_rows_are_incremental(self):
        with tempfile.TemporaryDirectory() as td:
            rec = record(td, 'voice', 'content_reference',
                         {'voice': True, 'voice_scope': 'entire_window_any_singing'})
            plan = make_plan(td, [rec], [c for c in CASES if c['type'] == 'baseline'])
            out = Path(td) / 'out'
            with patch.object(experiment, 'analyze_content', lambda *a, **k: analysis()), \
                    patch.object(experiment, 'tonal_evidence', lambda a, rate=22050: dict(TONAL)):
                first = experiment.run(plan, out, progress=lambda _: None)
                second = experiment.run(plan, out, progress=lambda _: None)
            self.assertEqual(len(first['rows']), len(second['rows']))
            self.assertTrue((out / 'content-report.json').exists())
            self.assertEqual(second['summary']['failed'], 0)


if __name__ == '__main__':
    unittest.main()
