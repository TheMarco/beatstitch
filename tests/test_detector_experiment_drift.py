"""Mocked drift-runner checks; no real detector inference or DSP rendering."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import detector_experiment_drift as drift
import studio_timing
from detector_experiment_common import event_score
from detector_pilot import digest, file_hash
from studio_engine import Clip, Track

RATE = 48000
UNIFORM = [1.0 + 0.5 * i for i in range(12)]  # 1.0 .. 6.5 at 120 BPM


def nonuniform():
    beats = [0.5]
    for i in range(15):
        beats.append(beats[-1] + 0.52 - 0.004 * i)
    return beats


def write_audio(path, seconds=10):
    sf.write(path, np.zeros((int(seconds * RATE), 2), dtype='float32'), RATE, subtype='FLOAT')
    return path


def make_plan(folder, beats, bpm, seconds=10, kind='synthetic_timing'):
    audio = write_audio(Path(folder) / 'song.wav', seconds)
    record = {'id': 'rec1', 'name': 'Known', 'kind': kind, 'audio': str(audio),
              'audio_sha256': file_hash(audio), 'frames': int(seconds * RATE),
              'expected': {'bpm': bpm, 'beats': list(beats), 'downbeats': list(beats[::4])}}
    plan = {'version': 1, 'records': [record], 'cases': []}
    plan['plan_sha256'] = digest({k: v for k, v in plan.items()})
    plan['created_utc'] = 'test'
    path = Path(folder) / 'plan.json'
    path.write_text(json.dumps(plan))
    return path, audio


def fake_prepare(calls):
    def prepare(track, clip, target, work, label, gain):
        calls.append({'pitch': clip.shift, 'target': target, 'label': label})
        ratio = track.bpm / target
        frames = int(round((studio_timing.corrected_time(track, clip, clip.cue_out)
                            - studio_timing.corrected_time(track, clip, clip.cue_in)) * ratio * RATE))
        raw = Path(work) / f'{label}.raw'
        np.zeros((frames, 2), dtype='<f4').tofile(raw)
        return raw
    return prepare


def uncertain_timing():
    return {'bpm': None, 'spread_percent': 0.0, 'coverage': 0.0, 'regions': [],
            'map': [], 'state': 'uncertain', 'windows': 3, 'version': 1, 'source': []}


class DriftRunnerTests(unittest.TestCase):
    def test_known_event_mapping_and_jitter(self):
        beats = nonuniform()
        points, native_bpm, desired = drift.build_oracle_map(beats, 10.0)
        mapped = np.interp(beats, [p[0] for p in points], [p[1] for p in points])
        np.testing.assert_allclose(mapped, desired, atol=1e-9)
        self.assertEqual(event_score(beats, beats, 0.03)['f1'], 1.0)
        jitter = drift.timing_jitter(beats, points)
        self.assertGreater(jitter['source']['p95_abs_ms'], 10.0)
        self.assertAlmostEqual(jitter['corrected']['p95_abs_ms'], 0.0, places=6)
        self.assertTrue(100.0 < native_bpm < 140.0)
        with tempfile.TemporaryDirectory() as folder:
            audio = write_audio(Path(folder) / 'a.wav')
            track = Track(id='t', path=str(audio), name='t', duration=10.0, bpm=native_bpm,
                          timing={'version': studio_timing.VERSION,
                                  'source': studio_timing.signature(audio),
                                  'state': 'drift', 'bpm': native_bpm, 'map': points})
            clip = Clip(track_id='t', cue_in=beats[4], cue_out=beats[12], stabilize=True)
            self.assertIsNotNone(studio_timing.mapping(track, clip))  # production-valid

    def test_mocked_detector_scores_known_anchors(self):
        with tempfile.TemporaryDirectory() as folder:
            plan, _ = make_plan(folder, UNIFORM, 120.0, seconds=8)
            points, _, _ = drift.build_oracle_map(UNIFORM, 8.0)
            timing = {'bpm': 120.0, 'spread_percent': 1.0, 'coverage': 0.9,
                      'regions': [{'start': UNIFORM[0], 'end': UNIFORM[-1], 'p95_ms': 5.0,
                                   'beats': list(UNIFORM), 'grid': list(UNIFORM)}],
                      'map': points, 'state': 'drift', 'windows': 5, 'version': 1, 'source': []}
            with patch.object(studio_timing, 'analyze', return_value=timing) as analyze:
                report = drift.run(plan, Path(folder) / 'out', progress=lambda m: None)
            row = next(r for r in report['rows'] if r['evaluation'] == 'detector')
            self.assertEqual(analyze.call_count, 1)
            self.assertEqual(row['status'], 'completed')
            self.assertTrue(row['correction_supported'])
            self.assertEqual(row['beat_scores']['30ms']['f1'], 1.0)
            self.assertIsNotNone(row['jitter']['corrected'])
            self.assertFalse(report['calibrated_confidence_promoted'])

    def test_unsupported_detector_result_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            plan, _ = make_plan(folder, UNIFORM, 120.0, seconds=8)
            with patch.object(studio_timing, 'analyze', return_value=uncertain_timing()):
                report = drift.run(plan, Path(folder) / 'out', progress=lambda m: None)
            row = next(r for r in report['rows'] if r['evaluation'] == 'detector')
            self.assertEqual(row['status'], 'uncertain')
            self.assertFalse(row['correction_supported'])
            self.assertEqual(row['timing']['map'], [])
            self.assertEqual(row['timing']['state'], 'uncertain')
            self.assertEqual(row['beat_scores']['30ms']['matched'], 0)
            self.assertEqual(row['beat_scores']['30ms']['missed'], len(UNIFORM))
            self.assertEqual(report['summary']['denominator_detector_rows'], 1)
            self.assertEqual(report['summary']['uncertain'], 1)

    def test_oracle_dsp_separate_from_detector_and_resume_guard(self):
        beats = nonuniform()
        with tempfile.TemporaryDirectory() as folder:
            plan, _ = make_plan(folder, beats, None, seconds=10)
            out = Path(folder) / 'out'
            calls = []
            with patch.object(studio_timing, 'analyze', return_value=uncertain_timing()) as analyze, \
                 patch.object(studio_timing, 'prepare', side_effect=fake_prepare(calls)) as prepare, \
                 patch.object(drift, 'measure_rendered_attacks', side_effect=lambda path, expected:
                              {'observed_seconds': list(map(float, expected))}), \
                 patch('detector_experiment_transient.markers', side_effect=lambda path, expected:
                       {'times':list(map(float, expected))}):
                report = drift.run(plan, out, progress=lambda m: None)
                # Resume reuses completed/uncertain rows: no new inference or rendering.
                drift.run(plan, out, progress=lambda m: None)
                self.assertEqual((analyze.call_count, prepare.call_count), (1, 2))
            self.assertEqual([c['pitch'] for c in calls], [0, 2])
            self.assertTrue(all(c['target'] == 128.0 for c in calls))
            detector = next(r for r in report['rows'] if r['evaluation'] == 'detector')
            self.assertEqual(detector['status'], 'uncertain')  # detector failure does not block oracle
            oracle = [r for r in report['rows'] if r['evaluation'] == 'oracle_dsp']
            self.assertEqual(len(oracle), 2)
            for row in oracle:
                self.assertEqual(row['status'], 'completed')
                self.assertFalse(row['detector_involved'])
                self.assertGreater(row['excerpts']['corrected']['frames'], 0)
                self.assertTrue((out / row['excerpts']['corrected']['path']).is_file())
            self.assertEqual(report['summary']['denominator_oracle_rows'], 2)
            saved = json.loads((out / drift.REPORT_NAME).read_text())
            saved['runner_sha256'] = '0' * 64  # a changed implementation wrote this report
            (out / drift.REPORT_NAME).write_text(json.dumps(saved))
            with patch.object(studio_timing, 'analyze', return_value=uncertain_timing()):
                with self.assertRaises(ValueError):
                    drift.run(plan, out, progress=lambda m: None)


if __name__ == '__main__':
    unittest.main()
