"""Real-filter checks for delay, stereo integrity and fair mastering auditions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import djmix
from prototype_mastering import analyse_dynamics, core_filters, finish_mix, limiter_gain, render_filter


class MasteringTests(unittest.TestCase):
    def test_gain_preserves_peak_budget_and_does_not_force_target(self):
        self.assertAlmostEqual(limiter_gain({'lufs': -24, 'true_peak_db': -1.2}), 2)
        self.assertAlmostEqual(limiter_gain({'lufs': -22, 'true_peak_db': -10}), 6)
        self.assertAlmostEqual(limiter_gain({'lufs': -10, 'true_peak_db': -1}), -4)

    def test_compression_and_sub_cleanup_are_measured_decisions(self):
        filters, _ = core_filters({'rms_p95_db': -20, 'crest_median_db': 6, 'sub20_power_fraction': .002})
        self.assertEqual(filters, ['anull'])
        filters, _ = core_filters({'rms_p95_db': -20, 'crest_median_db': 13, 'sub20_power_fraction': .03})
        self.assertTrue(filters[0].startswith('highpass='))
        self.assertIn('ratio=1.2', filters[1])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'anti-phase.wav'
            t = np.arange(djmix.SR * 2) / djmix.SR
            wave = .1 * np.sin(2 * np.pi * 10 * t)
            sf.write(path, np.column_stack((wave, -wave)), djmix.SR, subtype='FLOAT')
            analysis = analyse_dynamics(path)
            self.assertGreater(analysis['sub20_power_fraction'], .5)
            self.assertTrue(np.isfinite(analysis['rms_p95_db']))

    def test_oversampled_limiter_compensates_delay_and_flushes_final_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, out = (Path(temporary) / name for name in ('input.wav', 'output.wav'))
            frames = djmix.SR * 2
            audio = np.zeros((frames, 2), dtype='float32')
            for at in (12000, frames - 30):
                audio[at] = (.6, -.3)
            sf.write(source, audio, djmix.SR, subtype='FLOAT')
            render_filter(source, out, ['aresample=192000',
                'alimiter=limit=0.89:attack=5:release=80:level=false:latency=true',
                'aresample=48000'], frames)
            result, rate = sf.read(out, dtype='float32')
            self.assertEqual(rate, djmix.SR)
            self.assertEqual(result.shape, audio.shape)
            for at in (12000, frames - 30):
                segment = result[at - 10:at + 11, 0]
                self.assertEqual(int(np.argmax(abs(segment))) + at - 10, at)
                self.assertGreater(abs(float(result[at, 0])), .4)
            np.testing.assert_allclose(result[:, 1], -.5 * result[:, 0], atol=2e-7)

    def test_real_finishing_matches_loudness_preserves_input_and_reuses_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'original.wav'
            t = np.arange(djmix.SR * 12) / djmix.SR
            wave = .028 * np.sin(2 * np.pi * 440 * t)
            for beat in np.arange(.25, 12, .5):
                i = round(beat * djmix.SR)
                kick_t = np.arange(min(4500, len(wave) - i)) / djmix.SR
                wave[i:i + len(kick_t)] += .55 * np.cos(2 * np.pi * 65 * kick_t) * np.exp(-kick_t * 65)
            stereo = np.column_stack((wave, -.7 * wave)).astype('float32')
            sf.write(source, stereo, djmix.SR, subtype='PCM_24')
            before = source.read_bytes()
            folder = root / 'finished'
            report = finish_mix(source, folder, progress=lambda _: None)
            self.assertEqual(source.read_bytes(), before)
            self.assertLessEqual(report['mastered']['true_peak_db'], -1.0)
            self.assertLessEqual(abs(report['matched']['lufs'] - report['current']['lufs']), .15)
            for variant in ('current', 'mastered', 'matched'):
                self.assertEqual(sf.info(folder / f'{variant}.wav').frames, len(stereo))
            self.assertEqual(json.loads((folder / 'report.json').read_text())['recipe'], report['recipe'])
            with patch('prototype_mastering.djmix.meter', side_effect=AssertionError('Do not reprocess a cached comparison')):
                reused = finish_mix(source, folder, progress=lambda _: None)
            self.assertEqual(reused['mastered'], report['mastered'])


if __name__ == '__main__':
    unittest.main()
