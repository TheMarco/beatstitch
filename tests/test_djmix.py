"""Focused checks for pitch, timing, and user-controlled harmonic decisions."""

import argparse
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np

import djmix


class PlanningTests(unittest.TestCase):
    def test_enharmonic_and_relative_keys(self):
        self.assertEqual(djmix.parse_key("Bb minor"), djmix.parse_key("A#m"))
        self.assertEqual(djmix.relationship(djmix.parse_key("C"), djmix.parse_key("Am")),
                         "relative major/minor")
        self.assertIsNone(djmix.relationship(djmix.parse_key("C"), djmix.parse_key("Cm")))
        with self.assertRaises(djmix.MixError):
            djmix.parse_key("8A")

    def test_suggestions_do_not_apply_a_shift(self):
        advice = djmix.harmonic_advice("Cm", "Dm", 0, 0, "musical", "musical")
        self.assertEqual(advice["processed_b"], "D minor")
        self.assertEqual(advice["suggested_b_shifts"][0]["semitones"], -2)
        advice = djmix.harmonic_advice("Cm", "Dm", 0, 0, "percussive", "musical")
        self.assertEqual(advice["suggested_b_shifts"], [])
        self.assertIn("tonal tails", advice["caution"])

    def test_cues_stay_in_source_time_and_full_mix_keeps_remainder(self):
        settings = djmix.Settings(120, 128, 124, cue_a=32, cue_b=7.5, bars=8, context_bars=4)
        preview = djmix.plan(settings, 90, 100)
        self.assertEqual(preview["a"]["start"], 24)
        self.assertEqual(preview["b"]["start"], 7.5)
        self.assertAlmostEqual(preview["overlap_start_seconds"], 8 * 120 / 124, places=4)
        settings.full = True
        full = djmix.plan(settings, 90, 100)
        self.assertEqual(full["a"]["start"], 0)
        self.assertEqual(full["b"]["end"], 100)
        expected = 32 * 120 / 124 + (100 - 7.5) * 128 / 124
        self.assertAlmostEqual(full["duration_seconds"], expected, places=4)

    def test_rejects_impossible_or_nonfinite_settings(self):
        with self.assertRaises(djmix.MixError):
            djmix.plan(djmix.Settings(120, 120, 120, cue_a=20, bars=16), 30, 60)
        with self.assertRaises(djmix.MixError):
            djmix.plan(djmix.Settings(float("nan"), 120, 120, cue_a=0), 60, 60)
        for value in ("nan", "inf", "-1", "1:60", "1:2:3:4"):
            with self.assertRaises(argparse.ArgumentTypeError):
                djmix.seconds(value)


@unittest.skipUnless(all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe", "rubberband")),
                     "Audio tools are required for the integration checks.")
class EngineTests(unittest.TestCase):
    def test_independent_tempo_and_pitch_and_stereo_coherence(self):
        # Frequency proves pitch; exact duration independently proves the tempo ratio.
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            t = np.arange(djmix.SR * 4) / djmix.SR
            tone = 0.1 * np.sin(2 * np.pi * 440 * t)
            raw = work / "input.raw"
            np.column_stack((tone, tone)).astype("<f4").tofile(raw)
            source, processed = work / "source.wav", work / "processed.wav"
            djmix.export_wav(raw, source, raw=True)
            djmix.transform(source, processed, 120, 126, 2, formants=False)
            decoded = djmix.ffmpeg("-i", str(processed), "-f", "f32le", "-").stdout
            samples = np.frombuffer(decoded, dtype="<f4").reshape(-1, 2)
            self.assertLessEqual(abs(len(samples) - round(4 * 120 / 126 * djmix.SR)), 2)
            middle = samples[djmix.SR:3 * djmix.SR, 0]
            spectrum = np.abs(np.fft.rfft(middle * np.hanning(len(middle))))
            hz = np.argmax(spectrum) * djmix.SR / len(middle)
            expected = 440 * 2 ** (2 / 12)
            self.assertLess(abs(hz - expected), 0.6)
            self.assertLess(float(np.max(np.abs(samples[:, 0] - samples[:, 1]))), 1e-6)
            print(f"\nPitch check: expected {expected:.2f} Hz, measured {hz:.2f} Hz; "
                  f"duration {len(samples)/djmix.SR:.6f}s.")

    def test_cropped_nonzero_cue_retains_beat_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "clicks.wav"
            raw = work / "clicks.raw"
            samples = np.zeros((djmix.SR * 10, 2), dtype=np.float32)
            length = round(0.05 * djmix.SR)
            t = np.arange(length) / djmix.SR
            burst = 0.2 * np.sin(2 * np.pi * 1000 * t) * np.exp(-t / 0.008)
            for cue in np.arange(0.25, 10, 0.5):
                start = round(cue * djmix.SR)
                samples[start:start + length] += burst[:len(samples[start:start + length]), None]
            samples.astype("<f4").tofile(raw)
            djmix.export_wav(raw, source, raw=True)
            selection = {"start": 2.25, "end": 6.25, "frames": round(4 * 120 / 126 * djmix.SR)}
            processed = djmix.prepare(source, selection, 10, 120, 126, 0, True,
                                      work, "timing", 0)
            audio = np.abs(djmix.raw_audio(processed)[:, 0])
            errors = []
            for beat in range(1, 8):
                expected = round(beat * 60 / 126 * djmix.SR)
                radius = round(0.04 * djmix.SR)
                window = audio[expected-radius:expected+radius]
                self.assertGreater(float(window.max()), 0.05)
                peak = np.argmax(window) + expected - radius
                errors.append(abs(peak - expected) / djmix.SR)
            self.assertLess(max(errors), 0.015)
            print(f"\nBeat timing check: maximum attack offset {1000*max(errors):.2f} ms.")


if __name__ == "__main__":
    unittest.main()
