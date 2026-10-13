"""Listening notes stay bound to the exact audio recipe that was heard."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import djmix
from studio_cache import source_signature
from studio_engine import Clip, Session, Track
from studio_listening_notes import capture_note, latest_note, note_status, validate_notes
from studio_render_cache import listening_recipe
from studio_source_identity import fingerprint as file_fingerprint


def make_session(root, count=3):
    tracks, clips = {}, []
    for n in range(count):
        path = root / f"song{n}.wav"
        path.write_bytes(f"tiny source fixture {n} ".encode() * 8)
        tid = f"track{n}"
        tracks[tid] = Track(tid, str(path), f"Song {n}", 60.0, 120.0, "C minor", cue_out=60.0)
        tracks[tid].fingerprint = file_fingerprint(path)
        clips.append(Clip(tid, 0.0, 60.0))
    session = Session(tracks=tracks, clips=clips)
    session.listening_notes = []
    return session


class ListeningNotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = make_session(self.root)
        self.recipe = listening_recipe(self.session, "transition", 1)

    def capture(self, **kw):
        return capture_note(self.session, 1, self.recipe["fingerprint"],
                            kw.get("judgments", {"timing": "fits"}), kw.get("comment", "ok"))

    def test_creation_preserves_unchecked_and_provenance(self):
        note = self.capture()
        self.assertEqual(note["judgments"], {"timing": "fits", "harmony": "unchecked", "content": "unchecked"})
        self.assertEqual(note["pair"], [self.session.clips[0].id, self.session.clips[1].id])
        self.assertEqual(note["fingerprint"], self.recipe["fingerprint"])
        self.assertEqual(note["recipe"]["start"], self.recipe["start"])
        self.assertEqual(note["recipe"]["duration"], self.recipe["duration"])
        self.assertEqual(note["provenance"], "user_listening_note")
        self.assertEqual(note["estimate_exposure"], "normal_interface")
        self.assertIs(note["reference_annotation"], False)
        ids = {c.id for c in self.session.clips}
        self.assertTrue({s["clip_id"] for s in note["sources"]} <= ids)
        self.assertIn(self.session.clips[0].id, [s["clip_id"] for s in note["sources"]])
        self.assertIn(self.session.clips[1].id, [s["clip_id"] for s in note["sources"]])
        for entry in note["sources"]:
            track = self.session.tracks[entry["track_id"]]
            self.assertEqual(entry["source_signature"], source_signature(track.path))
            self.assertEqual(entry["sha256"], hashlib.sha256(Path(track.path).read_bytes()).hexdigest())
        validate_notes([note])
        # Stale loaded audio and bad indexes are rejected in plain language.
        with self.assertRaises(djmix.MixError):
            capture_note(self.session, 1, "0" * 64, {}, "")
        with self.assertRaises(djmix.MixError):
            capture_note(self.session, 9, self.recipe["fingerprint"], {}, "")
        # A track whose stored hash no longer matches the file exposes no hash.
        self.session.tracks["track0"].fingerprint = {"sha256": "a" * 64, "source": ["elsewhere", 1, 2]}
        note2 = self.capture()
        self.assertIsNone(next(s for s in note2["sources"] if s["track_id"] == "track0")["sha256"])

    def test_audio_changes_invalidate_but_metadata_does_not(self):
        note = self.capture()
        self.assertEqual(note_status(note, self.session), "current")
        saved = copy.deepcopy(self.session)

        def fresh():
            return copy.deepcopy(saved)

        cases = []
        s = fresh(); s.clips[1].bass_swap = False; cases.append(("bass", s))
        s = fresh(); s.clips[1].transpose = True; s.clips[1].semitones = 2; cases.append(("pitch", s))
        s = fresh(); s.tracks["track1"].bpm = 124.0; cases.append(("track bpm", s))
        s = fresh(); s.target_bpm = 132.0; cases.append(("target bpm", s))
        s = fresh(); s.mastering_enabled = True; cases.append(("mastering", s))
        for name, changed in cases:
            with self.subTest(change=name):
                self.assertEqual(note_status(note, changed), "changed")
        s = fresh(); s.tracks["track0"].name = "Renamed"; s.tracks["track0"].key = "G major"
        self.assertEqual(note_status(note, s), "current")

    def test_append_source_mutation_missing_and_removed(self):
        note = self.capture()
        self.session.clips.append(Clip("track2", 0.0, 60.0, id="extra-clip"))
        # Reuse an existing track for the appended clip; first blend is unchanged.
        self.assertEqual(note_status(note, self.session), "current")
        s2 = make_session(self.root)
        note2 = capture_note(s2, 1, listening_recipe(s2, "transition", 1)["fingerprint"], {}, "")
        Path(s2.tracks["track0"].path).write_bytes(b"mutated source with new bytes")
        self.assertEqual(note_status(note2, s2), "changed")
        Path(s2.tracks["track0"].path).unlink()
        self.assertEqual(note_status(note2, s2), "unavailable")
        s3 = make_session(self.root)
        note3 = capture_note(s3, 1, listening_recipe(s3, "transition", 1)["fingerprint"], {}, "")
        del s3.clips[0]
        self.assertEqual(note_status(note3, s3), "removed")
        self.assertEqual(latest_note(s3, 1), None)

    def test_latest_note_and_json_roundtrip(self):
        first = self.capture()
        second = capture_note(self.session, 1, self.recipe["fingerprint"], {"harmony": "problem"}, "later")
        self.session.listening_notes = [first, second]
        self.assertIs(latest_note(self.session, 1), second)
        self.assertIsNone(latest_note(self.session, 2))
        blob = json.dumps(self.session.listening_notes)
        self.assertEqual(validate_notes(json.loads(blob)), self.session.listening_notes)
        reopened = Session.from_dict(json.loads(json.dumps(self.session.to_dict())))
        self.assertEqual(reopened.listening_notes, self.session.listening_notes)
        self.assertEqual(note_status(second, reopened), 'current')
        legacy = self.session.to_dict(); legacy['version'] = 15; del legacy['listening_notes']
        self.assertEqual(Session.from_dict(legacy).listening_notes, [])

    def test_mastered_note_includes_later_source_and_changed_capture_is_rejected(self):
        from unittest.mock import patch
        self.session.mastering_enabled = True
        recipe = listening_recipe(self.session, 'transition', 1)
        note = capture_note(self.session, 1, recipe['fingerprint'], {}, '')
        self.assertEqual({s['clip_id'] for s in note['sources']}, {c.id for c in self.session.clips})
        # A file replacement after recipe capture must not yield mixed provenance.
        with patch('studio_listening_notes.listening_recipe', side_effect=[recipe, {**recipe, 'fingerprint': '0'*64}]):
            with self.assertRaises(djmix.MixError):
                capture_note(self.session, 1, recipe['fingerprint'], {}, '')

    def test_validation_rejects_malformed(self):
        good = self.capture()
        def bad(mutate):
            note = copy.deepcopy(good); mutate(note); return [note]
        mutators = [
            lambda n: n.update(version=2),
            lambda n: n.update(id="not-a-uuid"),
            lambda n: n.update(pair=["only"]),
            lambda n: n.update(fingerprint="xyz"),
            lambda n: n.update(recorded_at="yesterday"),
            lambda n: n.update(judgments={"timing": "fits"}),
            lambda n: n["judgments"].update(timing="great"),
            lambda n: n["judgments"].update(timing=[]),
            lambda n: n["judgments"].update(extra="fits"),
            lambda n: n.update(comment="x" * 2001),
            lambda n: n.update(provenance="detector"),
            lambda n: n.update(estimate_exposure="hidden"),
            lambda n: n.update(reference_annotation=True),
            lambda n: n.update(confidence=0.9),
            lambda n: n.update(sources="nope"),
            lambda n: n.update(recipe={"start": -1, "duration": 2}),
        ]
        for mutate in mutators:
            with self.assertRaises(djmix.MixError):
                validate_notes(bad(mutate))
        with self.assertRaises(djmix.MixError):
            validate_notes([good, good])
        with self.assertRaises(djmix.MixError):
            validate_notes("nope")
        with self.assertRaises(djmix.MixError):
            validate_notes([good] * 2001)


if __name__ == "__main__":
    unittest.main()
