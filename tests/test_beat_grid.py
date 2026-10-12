import tempfile
from pathlib import Path
import unittest

from studio_beat_grid import display_grid, grid_settings, manual_settings, TapTempo
from studio_engine import Track


class BeatGridTests(unittest.TestCase):
    def test_manual_phase_extends_both_ways_and_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'song.wav'; path.write_bytes(b'original')
            track = Track('song', str(path), 'Song', 10, 120)
            track.beat_grid = grid_settings(track, 120, 4.125)
            grid = display_grid(track)
            self.assertEqual(grid['beats'][:3], [.125, .625, 1.125])
            self.assertEqual(grid['downbeats'], [.125, 2.125, 4.125, 6.125, 8.125])
            self.assertTrue(all(0 <= t < 10 for t in grid['beats']))
            track.bpm = 60
            self.assertEqual(manual_settings(track), {})
            track.bpm = 120
            path.write_bytes(b'new version')
            self.assertEqual(display_grid(track), {})

    def test_grid_bounds_and_taps_ignore_double_taps_and_reset_after_gap(self):
        tapper = TapTempo()
        self.assertIsNone(tapper.tap(0))
        self.assertIsNone(tapper.tap(.5))
        self.assertIsNone(tapper.tap(.51))
        self.assertIsNone(tapper.tap(1))
        result = tapper.tap(1.5)
        self.assertEqual(result['bpm'], 120)
        self.assertTrue(result['steady'])
        self.assertEqual(result['taps'], 4)
        self.assertIsNone(tapper.tap(5))
        self.assertEqual(tapper.times, [5])
        with self.assertRaises(ValueError):
            tapper.tap(float('nan'))


if __name__ == '__main__':
    unittest.main()
