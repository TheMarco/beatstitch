"""Mocked timing-experiment checks; no real model inference."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import detector_experiment_timing as timing
from detector_experiment_common import event_score, map_events
from detector_pilot import digest, file_hash

RATE = 48000
BEATS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5]
DOWN = [1.0, 3.0, 5.0]


def write_audio(path, seconds=8):
    audio = np.zeros((int(seconds * RATE), 2), dtype='float32')
    sf.write(path, audio, RATE, subtype='FLOAT')
    return path


def make_plan(folder, kind='synthetic_timing', cases=None, expected=None):
    audio = write_audio(Path(folder) / 'song.wav')
    record = {'id': 'rec1', 'name': 'Known beat', 'kind': kind,
              'audio': str(audio), 'audio_sha256': file_hash(audio),
              'frames': int(8 * RATE),
              'expected': expected if expected is not None else {'bpm': 120., 'beats': BEATS, 'downbeats': DOWN}}
    plan = {'version': 1, 'records': [record],
            'cases': cases or [{'id': 'base', 'type': 'baseline', 'families': ['timing'],
                                'record_kinds': [kind]}]}
    content = {k: v for k, v in plan.items()}
    plan['plan_sha256'] = digest(content); plan['created_utc'] = 'test'
    path = Path(folder) / 'plan.json'; path.write_text(json.dumps(plan))
    return path


def beat_this_payload(beats=BEATS, down=DOWN, bpm=120., grid_beats=None, coverage=1.):
    return {'tempo': {'bpm': bpm, 'state': 'estimated'},
            'raw': {'beats': list(beats), 'downbeats': list(down),
                    'beat_strength': [0.9] * len(beats), 'downbeat_strength': [0.9] * len(down)},
            'grid': {'beats': list(grid_beats if grid_beats is not None else beats),
                     'downbeats': list(down), 'bars': [{'start': 1., 'end': 3.}],
                     'coverage': coverage, 'state': 'supported', 'bpm': bpm}}


def librosa_payload(beats=BEATS, bpm=120.):
    return {'bpm': bpm, 'beats': list(beats), 'downbeats': None}


def row(report, case='base', backend='beat_this_raw'):
    return next(r for r in report['rows'] if r['case_id'] == case and r['backend'] == backend)


class TimingExperimentTests(unittest.TestCase):
    def test_event_maps_and_one_to_one_scoring(self):
        self.assertEqual(map_events([2., 3.], {'type': 'tempo', 'speed': 2.}, 8.), [1., 1.5])
        self.assertEqual(map_events(BEATS, {'type': 'prefix', 'offset': 0.5}, 9.)[:2], [1.5, 2.0])
        self.assertEqual(map_events(BEATS, {'type': 'crop', 'offset': 1.}, 7.)[:3], [0.0, 0.5, 1.0])
        score = event_score([1., 2., 3.], [1., 2.01, 4.], 0.03)
        self.assertEqual((score['matched'], score['missed'], score['extra']), (2, 1, 1))

    def test_mocked_known_beat_and_grid_support_is_not_accuracy(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = make_plan(folder)
            grid_beats = [b + 0.1 for b in BEATS]  # supported grid, wrong at 30 ms
            with patch.object(timing, '_run_beat_this', return_value=beat_this_payload(grid_beats=grid_beats)) as bt, \
                 patch.object(timing, '_run_librosa', return_value=librosa_payload()):
                report = timing.run(plan, Path(folder) / 'out', progress=lambda m: None)
            raw = row(report)
            self.assertEqual(raw['beat_scores']['30ms']['f1'], 1.0)
            self.assertEqual(raw['beat_scores']['70ms']['f1'], 1.0)
            self.assertTrue(raw['tempo']['within_2_percent'])
            self.assertEqual(raw['downbeat_scores']['30ms']['matched'], 3)
            grid = row(report, backend='beat_this_grid')
            self.assertEqual(grid['grid']['grid_coverage'], 1.0)
            self.assertFalse(grid['grid']['grid_support_is_accuracy'])
            self.assertEqual(grid['beat_scores']['30ms']['f1'], 0.0)
            self.assertTrue(any(w['kind'] == 'missed_beats' for w in grid['weaknesses']))
            lib = row(report, backend='librosa')
            self.assertEqual(lib['downbeats_status'], 'unavailable')
            self.assertIsNone(lib['events']['downbeats'])
            self.assertEqual(bt.call_count, 1)  # raw+grid share one inference

    def test_missed_extra_and_half_double_flagged(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = make_plan(folder)
            beats = BEATS[:-1] + [7.9]  # one missed, one extra
            with patch.object(timing, '_run_beat_this', return_value=beat_this_payload(beats=beats, bpm=60.)), \
                 patch.object(timing, '_run_librosa', return_value=librosa_payload(beats=beats, bpm=60.)):
                report = timing.run(plan, Path(folder) / 'out', progress=lambda m: None)
            raw = row(report)
            self.assertEqual((raw['beat_scores']['30ms']['missed'], raw['beat_scores']['30ms']['extra']), (1, 1))
            self.assertTrue(raw['tempo']['half_double_ambiguity'])
            self.assertFalse(raw['tempo']['within_2_percent'])

    def test_real_prefix_crop_consistency_counts_extra(self):
        cases = [{'id': 'base', 'type': 'baseline', 'families': ['timing'], 'record_kinds': ['real']},
                 {'id': 'pre', 'type': 'prefix', 'offset': 0.5, 'families': ['timing'], 'record_kinds': ['real']},
                 {'id': 'crop', 'type': 'crop', 'offset': 1.0, 'families': ['timing'], 'record_kinds': ['real']}]
        def fake_bt(path, duration, cache, progress):
            if duration > 8.2: beats = [b + 0.5 for b in BEATS if b + 0.5 < duration] + [0.25]
            elif duration < 7.8: beats = [b - 1. for b in BEATS if b - 1. >= 0]
            else: beats = list(BEATS)
            beats = sorted(beats)
            return beat_this_payload(beats=beats, down=[b for b in DOWN if b in beats or True][:3])
        def fake_lib(path, duration):
            if duration > 8.2: beats = [b + 0.5 for b in BEATS if b + 0.5 < duration]
            elif duration < 7.8: beats = [b - 1. for b in BEATS if b - 1. >= 0]
            else: beats = list(BEATS)
            return librosa_payload(beats=sorted(beats))
        with tempfile.TemporaryDirectory() as folder:
            plan = make_plan(folder, kind='real', cases=cases, expected={})
            with patch.object(timing, '_run_beat_this', side_effect=fake_bt), \
                 patch.object(timing, '_run_librosa', side_effect=fake_lib):
                report = timing.run(plan, Path(folder) / 'out', progress=lambda m: None)
            pre = row(report, case='pre', backend='librosa')
            self.assertEqual(pre['reference_type'], 'transformation_consistency')
            self.assertEqual(pre['beat_scores']['30ms']['f1'], 1.0)
            pre_bt = row(report, case='pre', backend='beat_this_raw')
            self.assertEqual(pre_bt['beat_scores']['30ms']['extra'], 1)  # prefix extra counted
            crop = row(report, case='crop', backend='librosa')
            self.assertEqual(crop['beat_scores']['30ms']['f1'], 1.0)

    def test_resume_guard_no_duplicate_inference(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = make_plan(folder); out = Path(folder) / 'out'
            with patch.object(timing, '_run_beat_this', return_value=beat_this_payload()) as bt, \
                 patch.object(timing, '_run_librosa', return_value=librosa_payload()) as lib:
                timing.run(plan, out, progress=lambda m: None)
                first = (bt.call_count, lib.call_count, len(json.loads((out / 'timing-report.json').read_text())['rows']))
                timing.run(plan, out, progress=lambda m: None)
                self.assertEqual((bt.call_count, lib.call_count), first[:2])
                self.assertEqual(len(json.loads((out / 'timing-report.json').read_text())['rows']), first[2])
            changed = json.loads(Path(plan).read_text()); changed['records'][0]['name'] = 'Changed'
            changed['plan_sha256'] = digest({k: v for k, v in changed.items() if k not in ('plan_sha256', 'created_utc')})
            Path(plan).write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                timing.run(plan, out, progress=lambda m: None)


if __name__ == '__main__':
    unittest.main()
