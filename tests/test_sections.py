"""Section-change scoring: boundaries, phrase evidence, validation."""
import copy
import math
import unittest

import numpy as np

from studio_sections import analyze_sections

FPS = 50
BAR = 2.0  # seconds per bar
BINS = 8


def make_bars(n, start_number=0, gap_after=None, gap_size=2.0):
    bars = []
    t = 0.0
    for i in range(n):
        bars.append({"start": t, "end": t + BAR, "number": start_number + i})
        t += BAR
        if gap_after is not None and i == gap_after:
            t += gap_size
    return bars


def make_features(n_bars, blocks, energy_blocks=None, seed=0):
    """Constant-per-block logmel; blocks maps bar index -> feature offset."""
    rng = np.random.default_rng(seed)
    frames = int(n_bars * BAR * FPS)
    logmel = 0.01 * rng.standard_normal((frames, BINS))
    rms = np.full(frames, 0.5)
    for start_bar, end_bar, offset in blocks:
        s = int(start_bar * BAR * FPS)
        e = int(end_bar * BAR * FPS)
        logmel[s:e, : BINS // 2] += offset
    if energy_blocks:
        for start_bar, end_bar, level in energy_blocks:
            s = int(start_bar * BAR * FPS)
            e = int(end_bar * BAR * FPS)
            rms[s:e] = level
    return logmel, rms


def assert_finite_python(test, result):
    test.assertIsInstance(result, dict)
    for b in result["boundaries"]:
        test.assertIsInstance(b["time"], float)
        test.assertIsInstance(b["bar_index"], int)
        test.assertIsInstance(b["change"], float)
        test.assertIsInstance(b["energy_before_db"], float)
        test.assertIsInstance(b["energy_after_db"], float)
        test.assertIsInstance(b["label"], str)
        for key in ("time", "change", "energy_before_db", "energy_after_db"):
            test.assertTrue(math.isfinite(b[key]), key)
    phrase = result["phrase"]
    if phrase is not None:
        test.assertIsInstance(phrase["bars"], int)
        test.assertIsInstance(phrase["phase"], int)
        test.assertIsInstance(phrase["agreement"], float)
        test.assertIsInstance(phrase["anchor"], float)
        test.assertIsInstance(phrase["support"], int)


class TestSections(unittest.TestCase):
    def test_four_bars_are_enough_for_one_two_sided_boundary(self):
        mel, rms = make_features(4, [(0, 2, 0.0), (2, 4, 2.0)])
        result = analyze_sections(make_bars(4), mel, rms)
        self.assertEqual([b['bar_index'] for b in result['boundaries']], [2])

    def test_constant_audio_yields_nothing(self):
        bars = make_bars(16)
        logmel = np.ones((int(16 * BAR * FPS), BINS))
        rms = np.full(int(16 * BAR * FPS), 0.5)
        result = analyze_sections(bars, logmel, rms)
        self.assertEqual(result["boundaries"], [])
        self.assertIsNone(result["phrase"])

    def test_repeated_changes_yield_boundaries_and_phrase(self):
        bars = make_bars(32)
        logmel, rms = make_features(
            32,
            [(0, 8, 0.0), (8, 16, 2.0), (16, 24, 0.0), (24, 32, 2.0)],
            [(0, 8, 0.5), (8, 16, 0.25), (16, 24, 0.5), (24, 32, 0.25)],
        )
        result = analyze_sections(bars, logmel, rms)
        assert_finite_python(self, result)
        found = [b["bar_index"] for b in result["boundaries"]]
        self.assertEqual(found, [8, 16, 24])
        labels = [b["label"] for b in result["boundaries"]]
        self.assertEqual(labels[0], "Energy falls")
        self.assertEqual(labels[1], "Energy rises")
        # change is a raw score, not a probability: texture jumps exceed 1.
        self.assertTrue(all(b["change"] > 1.0 for b in result["boundaries"]))
        phrase = result["phrase"]
        self.assertIsNotNone(phrase)
        self.assertEqual(phrase["bars"], 8)
        self.assertEqual(phrase["phase"], 0)
        self.assertEqual(phrase["support"], 3)
        self.assertGreaterEqual(phrase["agreement"], 0.65)
        self.assertAlmostEqual(phrase["anchor"], 8 * BAR)

    def test_isolated_change_yields_no_phrase(self):
        bars = make_bars(12)
        logmel, rms = make_features(12, [(0, 6, 0.0), (6, 12, 2.0)])
        result = analyze_sections(bars, logmel, rms)
        self.assertEqual(len(result["boundaries"]), 1)
        self.assertEqual(result["boundaries"][0]["bar_index"], 6)
        self.assertEqual(result["boundaries"][0]["label"], "Texture changes")
        self.assertIsNone(result["phrase"])

    def test_energy_only_jump_labels(self):
        bars = make_bars(12)
        logmel = np.zeros((int(12 * BAR * FPS), BINS))
        rms = np.full(int(12 * BAR * FPS), 0.1)
        rms[int(6 * BAR * FPS):] = 0.4  # +12 dB
        result = analyze_sections(bars, logmel, rms)
        self.assertEqual(len(result["boundaries"]), 1)
        b = result["boundaries"][0]
        self.assertEqual(b["bar_index"], 6)
        self.assertEqual(b["label"], "Energy rises")
        self.assertAlmostEqual(b["energy_after_db"] - b["energy_before_db"], 12.04, places=1)

    def test_gap_does_not_become_boundary(self):
        bars = make_bars(12, gap_after=5, gap_size=2.0)
        logmel, rms = make_features(
            12, [(0, 6, 0.0), (6, 12, 2.0)], seed=1
        )
        # Realign frames to gapped timeline: rebuild on true span.
        span = bars[-1]["end"]
        frames = int(math.ceil(span * FPS))
        full_mel = np.zeros((frames, BINS))
        full_rms = np.full(frames, 0.5)
        for i, bar in enumerate(bars):
            s = int(math.floor(bar["start"] * FPS))
            e = int(math.ceil(bar["end"] * FPS))
            if i >= 6:
                full_mel[s:e, : BINS // 2] += 2.0
        result = analyze_sections(bars, full_mel, full_rms)
        for b in result["boundaries"]:
            self.assertNotEqual(b["bar_index"], 6)
        self.assertIsNone(result["phrase"])

    def test_no_mutation(self):
        bars = make_bars(12)
        logmel, rms = make_features(12, [(0, 6, 0.0), (6, 12, 2.0)])
        bars_copy = copy.deepcopy(bars)
        mel_copy = logmel.copy()
        rms_copy = rms.copy()
        analyze_sections(bars, logmel, rms)
        self.assertEqual(bars, bars_copy)
        np.testing.assert_array_equal(logmel, mel_copy)
        np.testing.assert_array_equal(rms, rms_copy)

    def test_empty_valid_input(self):
        result = analyze_sections([], np.zeros((0, BINS)), np.zeros((0,)))
        self.assertEqual(result, {"boundaries": [], "phrase": None})

    def test_invalid_data_rejected(self):
        good_bars = make_bars(8)
        good_mel = np.zeros((int(8 * BAR * FPS), BINS))
        good_rms = np.full(int(8 * BAR * FPS), 0.5)
        bad_cases = [
            # overlapping bars
            ([{"start": 0.0, "end": 2.0, "number": 0},
              {"start": 1.0, "end": 3.0, "number": 1}], good_mel, good_rms, FPS),
            # unsorted bars
            ([{"start": 2.0, "end": 4.0, "number": 1},
              {"start": 0.0, "end": 2.0, "number": 0}], good_mel, good_rms, FPS),
            # non-positive length
            ([{"start": 1.0, "end": 1.0, "number": 0}], good_mel, good_rms, FPS),
            # non-finite start
            ([{"start": float("nan"), "end": 2.0, "number": 0}],
             good_mel, good_rms, FPS),
            # non-integer number
            ([{"start": 0.0, "end": 2.0, "number": 1.5}], good_mel, good_rms, FPS),
            # frame mismatch
            (good_bars, good_mel, good_rms[:-1], FPS),
            # negative rms
            (good_bars, good_mel, -np.ones_like(good_rms), FPS),
            # non-finite logmel
            (good_bars, np.full_like(good_mel, np.inf), good_rms, FPS),
            # bad frame rate
            (good_bars, good_mel, good_rms, 0),
            (good_bars, good_mel, good_rms, -50),
        ]
        for bars, mel, rms, fps in bad_cases:
            with self.assertRaises(ValueError):
                analyze_sections(bars, mel, rms, fps)

    def test_no_genre_labels(self):
        bars = make_bars(32)
        logmel, rms = make_features(
            32,
            [(0, 8, 0.0), (8, 16, 2.0), (16, 24, 0.0), (24, 32, 2.0)],
            [(0, 8, 0.5), (8, 16, 0.25), (16, 24, 0.5), (24, 32, 0.25)],
        )
        result = analyze_sections(bars, logmel, rms)
        allowed = {"Energy rises", "Energy falls", "Texture changes"}
        self.assertTrue(result["boundaries"])
        for b in result["boundaries"]:
            self.assertIn(b["label"], allowed)


if __name__ == "__main__":
    unittest.main()
