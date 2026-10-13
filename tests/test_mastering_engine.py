"""Production mastering helpers: atomicity, matching, no reprocessing."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import djmix
from studio_mastering import VERSION, core_filters, limiter_gain, master_file, prepare_comparison
from studio_mastering_audio import load_mastering_folder


def _synthetic(path, seconds=10, level=.028):
    t = np.arange(djmix.SR * seconds) / djmix.SR
    wave = level * np.sin(2 * np.pi * 440 * t)
    for beat in np.arange(.25, seconds, .5):
        i = round(beat * djmix.SR)
        kt = np.arange(min(4500, len(wave) - i)) / djmix.SR
        wave[i:i + len(kt)] += .55 * np.cos(2 * np.pi * 65 * kt) * np.exp(-kt * 65)
    stereo = np.column_stack((wave, -.7 * wave)).astype('float32')
    sf.write(path, stereo, djmix.SR, subtype='PCM_24')
    return len(stereo)


class MasteringEngineTests(unittest.TestCase):
    def test_comparison_restores_previous_files_if_publication_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, master, other = (root / name for name in ('mix.wav', 'master.wav', 'other.wav'))
            _synthetic(source, seconds=2); _synthetic(other, seconds=2, level=.05)
            master_file(source, master)
            folder = root / 'compare'; prepare_comparison(source, master, folder)
            before = {p.name: p.read_bytes() for p in folder.iterdir()}
            import studio_mastering
            replace = studio_mastering.os.replace
            def fail_midway(src, dst):
                if Path(dst) == folder / 'mastered.wav' and Path(src).parent.name.startswith('.comparison-'):
                    raise OSError('Simulated publishing failure')
                return replace(src, dst)
            with patch('studio_mastering.os.replace', side_effect=fail_midway):
                with self.assertRaises(OSError): prepare_comparison(other, master, folder)
            self.assertEqual({p.name: p.read_bytes() for p in folder.iterdir()}, before)

    def test_decisions_match_prototype(self):
        self.assertEqual(VERSION, 1)
        self.assertAlmostEqual(limiter_gain({'lufs': -24, 'true_peak_db': -1.2}), 2)
        self.assertAlmostEqual(limiter_gain({'lufs': -22, 'true_peak_db': -10}), 6)
        filters, _ = core_filters({'rms_p95_db': -20, 'crest_median_db': 6, 'sub20_power_fraction': .002})
        self.assertEqual(filters, ['anull'])
        filters, _ = core_filters({'rms_p95_db': -20, 'crest_median_db': 13, 'sub20_power_fraction': .03})
        self.assertTrue(filters[0].startswith('highpass='))
        self.assertIn('ratio=1.2', filters[1])
        self.assertIn('attack=30', filters[1])
        self.assertIn('release=180', filters[1])

    def test_master_and_comparison_end_to_end(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, dest = root / 'mix.wav', root / 'master.wav'
            frames = _synthetic(source)
            before = source.read_bytes()
            result = master_file(source, dest, progress=lambda _: None)
            self.assertEqual(source.read_bytes(), before)
            json.dumps(result, allow_nan=False)
            self.assertEqual((result['frames'], sf.info(dest).frames), (frames, frames))
            self.assertLessEqual(result['true_peak_db'], -1.0)
            self.assertEqual(result['mastering']['target_lufs'], -14.0)
            self.assertIn('analysis', result['mastering'])

            folder = root / 'compare'
            report = prepare_comparison(source, dest, folder, progress=lambda _: None)
            json.dumps(report, allow_nan=False)
            self.assertLessEqual(abs(report['matched']['lufs'] - report['current']['lufs']), .15)
            self.assertLessEqual(report['matched_gain_db'], 0)
            self.assertLessEqual(report['current_gain_db'], 0)
            # Mastered copied verbatim, not reprocessed.
            self.assertEqual((folder / 'mastered.wav').read_bytes(), dest.read_bytes())
            data = load_mastering_folder(folder)
            self.assertEqual(len(data.current), frames)
            self.assertEqual(len(data.mastered), frames)
            self.assertEqual(len(data.matched), frames)
            data.close()
            # Cache reuse avoids meter/DSP.
            with patch('studio_mastering.djmix.meter', side_effect=AssertionError('reprocessed')):
                self.assertEqual(prepare_comparison(source, dest, folder, progress=lambda _: None)['matched'],
                                 report['matched'])

    def test_cancellation_preserves_destination_and_previous_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, dest = root / 'mix.wav', root / 'master.wav'
            _synthetic(source, seconds=2)
            dest.write_bytes(b'previous-master')
            def boom(msg):
                raise RuntimeError('cancelled')
            with self.assertRaises(RuntimeError):
                master_file(source, dest, progress=boom)
            self.assertEqual(dest.read_bytes(), b'previous-master')
            with self.assertRaises(Exception):
                master_file(source, source, progress=lambda _: None)

            master_file(source, dest, progress=lambda _: None)
            folder = root / 'compare'
            prepare_comparison(source, dest, folder, progress=lambda _: None)
            snapshot = {p.name: p.read_bytes() for p in folder.iterdir()}
            other = root / 'other.wav'
            _synthetic(other, seconds=2, level=.05)
            # Different pair (cache miss) failing via progress keeps old folder.
            def boom2(msg):
                if 'Measuring' in msg:
                    raise RuntimeError('cancelled')
            with self.assertRaises(RuntimeError):
                prepare_comparison(other, dest, folder, progress=boom2)
            self.assertEqual({p.name: p.read_bytes() for p in folder.iterdir()}, snapshot)
            with self.assertRaises(Exception):
                prepare_comparison(folder / 'current.wav', dest, folder, progress=lambda _: None)


if __name__ == '__main__':
    unittest.main()
