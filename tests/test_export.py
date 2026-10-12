"""Focused purpose-based offline export checks."""
import copy
import csv
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import djmix
import studio_export
from studio_engine import Clip, Session, Track, layout


def make_master(folder: Path, seconds: float = 2.0, name: str = "master.wav") -> Path:
    t = np.arange(round(seconds * djmix.SR)) / djmix.SR
    tone = 0.1 * np.sin(2 * np.pi * 440 * t)
    raw = folder / (name + ".raw")
    np.column_stack([tone, tone]).astype("<f4").tofile(raw)
    master = folder / name
    djmix.export_wav(raw, master, raw=True)
    raw.unlink()
    return master


def project():
    tracks = {str(i): Track(str(i), f"{i}.wav", f"Song {i}", 20, 120, cue_out=20)
              for i in range(3)}
    return Session(target_bpm=120, tracks=tracks,
                   clips=[Clip(identity, 0, 20, blend_bars=2) for identity in tracks])


class PresetTests(unittest.TestCase):
    def test_presets_and_estimates(self):
        self.assertEqual(set(studio_export.PRESETS), {"wav", "flac", "mp3"})
        self.assertEqual(studio_export.PRESETS["wav"]["label"], "High-quality master")
        self.assertEqual(studio_export.PRESETS["wav"]["extension"], ".wav")
        self.assertEqual(studio_export.PRESETS["flac"]["label"], "Lossless, smaller file")
        self.assertEqual(studio_export.PRESETS["flac"]["extension"], ".flac")
        self.assertEqual(studio_export.PRESETS["mp3"]["label"], "Easy sharing")
        self.assertEqual(studio_export.PRESETS["mp3"]["extension"], ".mp3")
        for key in ("wav", "flac"):
            self.assertIn("48", studio_export.PRESETS[key]["description"])
            self.assertIn("24", studio_export.PRESETS[key]["description"])
        self.assertIn("stereo", studio_export.PRESETS["wav"]["description"].lower())
        self.assertIn("320", studio_export.PRESETS["mp3"]["description"])
        self.assertIn("stereo", studio_export.PRESETS["mp3"]["description"].lower())
        self.assertEqual(studio_export.estimate_bytes(10, "wav"), 10 * 48000 * 2 * 3)
        self.assertEqual(studio_export.estimate_bytes(10, "mp3"), 10 * 40000)
        flac = studio_export.estimate_bytes(10, "flac")
        self.assertIsInstance(flac, int)
        self.assertLess(flac, studio_export.estimate_bytes(10, "wav"))
        doc = studio_export.estimate_bytes.__doc__ or ""
        self.assertIn("approximate", doc.lower())
        self.assertIn("PCM", doc)
        self.assertIn("24", doc)
        self.assertIn("stereo", doc.lower())
        with self.assertRaises(djmix.MixError):
            studio_export.estimate_bytes(5, "ogg")
        with self.assertRaises(djmix.MixError):
            studio_export.estimate_bytes(-1, "wav")


class ExportAudioTests(unittest.TestCase):
    def test_real_flac_mp3_wav_output(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            master = make_master(folder)
            before = master.read_bytes()
            for preset in ("flac", "mp3", "wav"):
                dest = folder / f"out.{preset}"
                result = studio_export.export_audio(master, dest, preset, title='Demo "quote"')
                self.assertEqual(Path(result["path"]), dest)
                self.assertIn("duration", result)
                self.assertIn("lufs", result)
                self.assertIn("true_peak_db", result)
                self.assertTrue(dest.is_file())
                self.assertLessEqual(result["true_peak_db"], -0.99)
                info = djmix.probe(dest)
                if preset == "mp3":
                    self.assertLessEqual(abs(info["duration"] - 2.0), 0.11)
                else:
                    self.assertLessEqual(abs(info["duration"] - 2.0), 2.5 / djmix.SR)
            self.assertEqual(master.read_bytes(), before)
            self.assertEqual(list(folder.glob("*.csv")), [])

    def test_validates_ids_extensions_and_protected_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            master = make_master(folder)
            with self.assertRaisesRegex(djmix.MixError, "preset"):
                studio_export.export_audio(master, folder / "o.ogg", "ogg")
            with self.assertRaisesRegex(djmix.MixError, ".flac"):
                studio_export.export_audio(master, folder / "o.mp3", "flac")
            with self.assertRaisesRegex(djmix.MixError, "not found"):
                studio_export.export_audio(folder / "missing.wav", folder / "o.wav", "wav")
            with self.assertRaisesRegex(djmix.MixError, "protected"):
                studio_export.export_audio(master, master, "wav")
            protected = folder / "keep.wav"
            protected.write_bytes(b"keep")
            with self.assertRaisesRegex(djmix.MixError, "protected"):
                studio_export.export_audio(master, protected, "wav",
                                            protected_sources=[protected])
            try:
                link = folder / "link.wav"
                os.link(master, link)
            except OSError:
                self.skipTest("hardlinks unavailable")
            else:
                with self.assertRaisesRegex(djmix.MixError, "protected"):
                    studio_export.export_audio(master, link, "wav")

    def test_preserves_master_and_destination_on_cancel_or_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            master = make_master(folder)
            master_before = master.read_bytes()
            dest = folder / "out.mp3"
            dest.write_bytes(b"old-bytes")
            def cancel(_message):
                return False
            with self.assertRaisesRegex(djmix.MixError, "[Cc]ancel"):
                studio_export.export_audio(master, dest, "mp3", progress=cancel)
            self.assertEqual(dest.read_bytes(), b"old-bytes")
            self.assertEqual(master.read_bytes(), master_before)
            def boom(_message):
                raise djmix.MixError("stop")
            with self.assertRaises(djmix.MixError):
                studio_export.export_audio(master, dest, "mp3", progress=boom)
            self.assertEqual(dest.read_bytes(), b"old-bytes")
            bad = folder / "bad.wav"
            bad.write_text("not audio")
            bad_dest = folder / "old.wav"
            bad_dest.write_bytes(b"old-wav")
            with self.assertRaises(djmix.MixError):
                studio_export.export_audio(bad, bad_dest, "wav")
            self.assertEqual(bad_dest.read_bytes(), b"old-wav")
            self.assertFalse([p for p in folder.iterdir() if p.name.startswith("dj-export-")])

    def test_cancel_after_encoding_does_not_publish_partial_export(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            master = make_master(folder)
            destination = folder / "out.mp3"
            destination.write_bytes(b"keep my export")
            stages = []
            def progress(stage):
                stages.append(stage)
                return stage != "Verifying export"
            with self.assertRaisesRegex(djmix.MixError, "cancelled"):
                studio_export.export_audio(master, destination, "mp3", progress=progress)
            self.assertIn("Verifying export", stages)
            self.assertEqual(destination.read_bytes(), b"keep my export")
            self.assertEqual(list(folder.glob("dj-export-*")), [])


class TracklistTests(unittest.TestCase):
    def test_rows_follow_layout_overlaps_without_mutation_or_io(self):
        session = project()
        before = copy.deepcopy(session.to_dict())
        positions = layout(session)
        with patch("djmix.probe", side_effect=AssertionError("no io")), \
             patch("djmix.meter", side_effect=AssertionError("no io")):
            rows = studio_export.tracklist_rows(session)
        self.assertEqual(len(rows), 3)
        for index, (row, pos, clip) in enumerate(zip(rows, positions, session.clips)):
            self.assertEqual(row["number"], index + 1)
            self.assertEqual(row["title"], session.tracks[clip.track_id].name)
            self.assertAlmostEqual(row["start_seconds"], pos["start"] / djmix.SR, places=6)
            self.assertAlmostEqual(row["end_seconds"], pos["end"] / djmix.SR, places=6)
        self.assertLess(rows[1]["start_seconds"], rows[0]["end_seconds"])
        self.assertEqual(session.to_dict(), before)

    def test_write_tracklist_csv_quoting_format_and_source_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            session = project()
            session.tracks["0"].name = 'Say "hi", yo'
            session.tracks["1"].name = "Line1\nLine2"
            dest = folder / "tracks.csv"
            before = copy.deepcopy(session.to_dict())
            result = studio_export.write_tracklist(session, dest)
            self.assertEqual(result, dest)
            self.assertEqual(session.to_dict(), before)
            with dest.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, ["number", "title", "start", "end"])
                rows = list(reader)
            self.assertEqual([r["title"] for r in rows],
                             ['Say "hi", yo', "Line1\nLine2", "Song 2"])
            for row in rows:
                self.assertRegex(row["start"], r"^\d+:\d{2}:\d{2}\.\d{3}$")
                self.assertRegex(row["end"], r"^\d+:\d{2}:\d{2}\.\d{3}$")
            self.assertEqual(rows[0]["start"], "0:00:00.000")
            source = folder / "song.wav"
            source.write_bytes(b"x")
            session.tracks["0"].path = str(source)
            with self.assertRaisesRegex(djmix.MixError, "source songs"):
                studio_export.write_tracklist(session, source)


if __name__ == "__main__":
    unittest.main()
