import copy
import json
import unittest

import numpy as np

from studio_beat_alignment import estimate_alignment

SR = 22050


def make_kicks(duration, bpm, sr, shift=0.0, amp=1.0, freq=60.0, drift_rate=0.0):
    """Kick train: 60 Hz decaying bursts on each beat, shifted by `shift` sec.

    Positive shift = events later. drift_rate (s/s) grows shift linearly.
    """
    n = int(duration * sr)
    t = np.arange(n) / sr
    y = np.zeros(n)
    beat = 60.0 / bpm
    k = 0
    while True:
        bt = k * beat
        if bt > duration:
            break
        cur_shift = shift + drift_rate * bt
        start = bt + cur_shift
        idx = int(round(start * sr))
        length = int(0.12 * sr)
        if 0 <= idx < n:
            tt = np.arange(min(length, n - idx)) / sr
            seg = np.sin(2 * np.pi * freq * tt) * np.exp(-tt / 0.03)
            # fast attack envelope
            seg *= 1.0 - np.exp(-tt / 0.002)
            y[idx:idx + len(seg)] += amp * seg
        k += 1
    return y


def add_offbeat_high(y, bpm, sr, amp=3.0, freq=7000.0):
    y = y.copy()
    beat = 60.0 / bpm
    n = len(y)
    k = 0
    while True:
        bt = (k + 0.5) * beat
        if bt * sr >= n:
            break
        idx = int(bt * sr)
        length = int(0.05 * sr)
        tt = np.arange(min(length, n - idx)) / sr
        seg = np.sin(2 * np.pi * freq * tt) * np.exp(-tt / 0.008)
        y[idx:idx + len(seg)] += amp * seg
        k += 1
    return y


class TestBeatAlignment(unittest.TestCase):
    def check_offset(self, bpm, delay, duration=8.0):
        # delay>0: incoming late -> offset should be -delay
        out = make_kicks(duration, bpm, SR, shift=0.0)
        inc = make_kicks(duration, bpm, SR, shift=delay)
        r = estimate_alignment(out, inc, SR, bpm)
        self.assertEqual(r["state"], "suggested", r)
        self.assertAlmostEqual(r["offset_seconds"], -delay, delta=0.015, msg=str(r))
        json.dumps(r)

    def test_late_and_early_two_bpms(self):
        for bpm in (120.0, 128.0):
            self.check_offset(bpm, 0.060)
            self.check_offset(bpm, -0.045)

    def test_aligned(self):
        out = make_kicks(8.0, 120.0, SR)
        inc = make_kicks(8.0, 120.0, SR)
        r = estimate_alignment(out, inc, SR, 120.0)
        self.assertEqual(r["state"], "aligned", r)
        self.assertLessEqual(abs(r["offset_seconds"]), 0.010)

    def test_repeated_strong_kick_accents_are_aligned_when_weaker_kicks_coincide(self):
        for beat_delay in (1, -1):
            duration, bpm = 16., 120.
            out = make_kicks(duration, bpm, SR, amp=.4)
            out += make_kicks(duration, bpm / 4, SR, amp=.6)
            inc = make_kicks(duration, bpm, SR, shift=.06, amp=.4)
            inc += make_kicks(duration, bpm / 4, SR, shift=beat_delay * .5 + .06, amp=.6)
            result = estimate_alignment(out, inc, SR, bpm)
            self.assertEqual(result['state'], 'suggested', result)
            self.assertAlmostEqual(result['offset_seconds'], -beat_delay * .5 - .06, delta=.015)
            self.assertEqual(result.get('alignment_kind'), 'kick accents')

    def test_equal_kicks_do_not_invent_a_bar_accent_and_markers_share_stereo_evidence(self):
        from studio_beat_alignment import kick_markers
        audio = make_kicks(16, 120, SR)
        result = estimate_alignment(audio, audio, SR, 120)
        self.assertEqual(result['state'], 'aligned')
        self.assertNotIn('kick_accent', result['diagnostics'])
        mono = kick_markers(audio, SR, 120)
        stereo = kick_markers(np.column_stack([audio, -audio]), SR, 120)
        np.testing.assert_allclose(mono, stereo, atol=1 / 1000)
        self.assertGreater(len(mono), 25)

    def test_bounded_input_and_low_frequency_half_beat_ambiguity(self):
        long_audio = np.zeros(SR * 91)
        with self.assertRaises(ValueError): estimate_alignment(long_audio, long_audio, SR, 120)
        audio = make_kicks(8, 240, SR)
        result = estimate_alignment(audio, audio, SR, 120)
        self.assertEqual(result['state'], 'uncertain', result)

    def test_independently_scaled(self):
        out = make_kicks(8.0, 122.0, SR, amp=0.4)
        inc = make_kicks(8.0, 122.0, SR, shift=0.05, amp=2.5)
        r = estimate_alignment(out, inc, SR, 122.0)
        self.assertEqual(r["state"], "suggested", r)
        self.assertAlmostEqual(r["offset_seconds"], -0.05, delta=0.015, msg=str(r))

    def test_opposite_polarity_stereo(self):
        mono_out = make_kicks(8.0, 120.0, SR)
        mono_in = make_kicks(8.0, 120.0, SR, shift=0.055)
        out = np.column_stack([mono_out, mono_out])
        inc = np.column_stack([mono_in, -mono_in])
        r = estimate_alignment(out, inc, SR, 120.0)
        self.assertEqual(r["state"], "suggested", r)
        self.assertAlmostEqual(r["offset_seconds"], -0.055, delta=0.015, msg=str(r))

    def test_silence_and_single_event(self):
        out = make_kicks(8.0, 120.0, SR)
        sil = np.zeros_like(out)
        self.assertEqual(estimate_alignment(out, sil, SR, 120.0)["state"], "uncertain")
        self.assertEqual(estimate_alignment(sil, sil, SR, 120.0)["state"], "uncertain")
        single = make_kicks(8.0, 120.0, SR)
        # keep only first kick in incoming
        beat_n = int((60 / 120.0) * SR)
        inc = np.zeros_like(out)
        inc[:beat_n] = single[:beat_n]
        self.assertEqual(estimate_alignment(out, inc, SR, 120.0)["state"], "uncertain")

    def test_loud_offbeat_high_frequency(self):
        out = make_kicks(8.0, 120.0, SR)
        inc = make_kicks(8.0, 120.0, SR, shift=0.05)
        inc = add_offbeat_high(inc, 120.0, SR, amp=4.0)
        out_h = add_offbeat_high(out, 120.0, SR, amp=4.0)
        r = estimate_alignment(out_h, inc, SR, 120.0)
        self.assertEqual(r["state"], "suggested", r)
        self.assertAlmostEqual(r["offset_seconds"], -0.05, delta=0.015, msg=str(r))

    def test_drifting_offset_uncertain(self):
        out = make_kicks(12.0, 120.0, SR)
        inc = make_kicks(12.0, 120.0, SR, shift=0.0, drift_rate=0.012)
        r = estimate_alignment(out, inc, SR, 120.0)
        self.assertEqual(r["state"], "uncertain", r)
        self.assertIsNone(r["offset_seconds"])

    def test_no_mutation_and_json(self):
        out = make_kicks(8.0, 120.0, SR)
        inc = make_kicks(8.0, 120.0, SR, shift=0.04)
        o0, i0 = out.copy(), inc.copy()
        r = estimate_alignment(out, inc, SR, 120.0)
        np.testing.assert_array_equal(out, o0)
        np.testing.assert_array_equal(inc, i0)
        json.dumps(r)
        self.assertNotIn("%", r["reason"])

    def test_invalid_inputs(self):
        out = make_kicks(4.0, 120.0, SR)
        with self.assertRaises(ValueError):
            estimate_alignment(out, out[:-10], SR, 120.0)
        with self.assertRaises(ValueError):
            estimate_alignment(np.array([np.nan, 0.0]), np.array([0.0, 0.0]), SR, 120.0)
        with self.assertRaises(ValueError):
            estimate_alignment(out, out, 0, 120.0)
        with self.assertRaises(ValueError):
            estimate_alignment(out, out, SR, 0)
        with self.assertRaises(ValueError):
            estimate_alignment(np.zeros((10, 3)), np.zeros((10, 3)), SR, 120.0)


if __name__ == "__main__":
    unittest.main()
