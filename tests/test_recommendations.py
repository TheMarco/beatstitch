"""Advice must reduce settings risks without guessing tempo or breaking a neighbor."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QPushButton

from desktop_app import Studio
from studio_engine import Clip, Session, Track
from studio_recommendations import pitch_advice, review_mix, tempo_advice, transition_harmony


def session(keys=("C minor", "D minor", "G minor"), bpms=(120, 132, 128)):
    tracks = {str(i): Track(str(i), f"{i}.wav", f"Song {i + 1}", 120, bpm, key, cue_out=120)
              for i, (key, bpm) in enumerate(zip(keys, bpms))}
    return Session(target_bpm=120, tracks=tracks, clips=[Clip(identity, 0, 120) for identity in tracks])


def voice_project(root):
    from tests.test_overlap_content import voice_content
    project = session(keys=('C major',) * 3, bpms=(120,) * 3)
    for identity, track in project.tracks.items():
        path = Path(root) / (identity + '.wav'); path.write_bytes(b'fixture'); track.path = str(path)
    project.tracks['0'].content = voice_content(project.tracks['0'], [(110., 120.)])
    project.tracks['1'].content = voice_content(project.tracks['1'], [(0., 10.)])
    return project


class RecommendationTests(unittest.TestCase):
    def test_tempo_balances_percent_changes_and_reports_every_song_without_mutation(self):
        project = session()
        before = copy.deepcopy(project.to_dict())
        advice = tempo_advice(project)
        self.assertEqual(advice["target"], 125.7)
        self.assertTrue(advice["can_apply"])
        self.assertEqual(len(advice["rows"]), 3)
        self.assertAlmostEqual(advice["current_max"], 100 * (1 - 120 / 132))
        self.assertLess(advice["suggested_max"], 4.8)
        self.assertEqual(project.to_dict(), before)
        project.target_bpm = advice["target"]
        self.assertFalse(tempo_advice(project)["can_apply"])

    def test_tempo_preference_is_a_bound_and_octave_readings_are_not_guessed(self):
        project = session(bpms=(100, 150, 140))
        advice = tempo_advice(project)
        self.assertFalse(advice["within_preference"])
        self.assertFalse(advice["can_apply"])
        self.assertIn("No shared tempo", advice["reason"])
        project = session(bpms=(65, 128, 130))
        advice = tempo_advice(project)
        self.assertFalse(advice["available"])
        self.assertIn("half/double", advice["reason"])
        self.assertEqual(project.tracks["0"].bpm, 65)
        self.assertFalse(tempo_advice(Session())["available"])

    def test_shift_must_not_break_a_compatible_next_blend(self):
        project = session(keys=("C minor", "D minor", "A minor"))
        before = copy.deepcopy(project.to_dict())
        self.assertEqual(transition_harmony(project, 2)["state"], "compatible")
        self.assertEqual(transition_harmony(project, 2, {project.clips[1].id: -2})["state"], "clash")
        advice = pitch_advice(project, 1)
        self.assertIsNone(advice["suggestion"])
        self.assertIn("without worsening", advice["reason"])
        self.assertEqual(project.to_dict(), before)

    def test_shift_can_help_one_and_preserve_the_other_including_shifted_neighbors(self):
        project = session()
        advice = pitch_advice(project, 1)["suggestion"]
        self.assertEqual(advice["semitones"], -2)
        self.assertEqual([p["state"] for p in advice["after"]], ["compatible", "compatible"])
        self.assertEqual(advice["improved"], [1])
        # Advice uses the next clip's heard pitch, not just its original label.
        project.clips[2].transpose = True
        project.clips[2].semitones = 2  # Gm -> Am; -2 on Dm would now break this blend.
        self.assertIsNone(pitch_advice(project, 1)["suggestion"])

    def test_unknown_and_percussion_are_not_passed_off_as_verified_harmony(self):
        project = session(keys=("C minor", "D minor", ""))
        advice = pitch_advice(project, 1)
        self.assertEqual(advice["suggestion"]["semitones"], -2)
        self.assertIn("not verified", advice["reason"])
        self.assertEqual(advice["suggestion"]["after"][1]["state"], "unknown")
        project.clips[1].intro_percussive = True
        self.assertIsNone(pitch_advice(project, 1)["suggestion"])
        self.assertEqual(transition_harmony(project, 1)["state"], "percussion")
        self.assertIsNone(pitch_advice(session(), 1, max_shift=0)["suggestion"])
        self.assertIsNone(pitch_advice(session(), 1, max_shift=1)["suggestion"])

    def test_partial_improvement_explicitly_names_the_remaining_clash(self):
        project = session(keys=("C minor", "D minor", "D major"))
        advice = pitch_advice(project, 1)
        self.assertEqual(advice["suggestion"]["semitones"], -2)
        self.assertIn("2 → 3 still has a possible key clash", advice["reason"])

    def test_review_can_offer_an_outgoing_change_and_does_not_mark_unknown_checks_good(self):
        project = session(keys=("C minor", "D minor", "A minor"))
        report = review_mix(project)
        proposal = report["transitions"][0]["suggestion"]
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["id"], project.clips[0].id)  # Incoming change is blocked; opener may move.
        self.assertTrue(report["count"])

    def test_voice_warnings_are_separate_from_related_keys_and_refresh_after_cue_edits(self):
        with tempfile.TemporaryDirectory() as root:
            project = voice_project(root); before = copy.deepcopy(project.to_dict())
            with patch('studio_content.analyze_content', side_effect=AssertionError('Review must not start analysis')):
                report = review_mix(project)
            first = report['transitions'][0]
            self.assertEqual(first['state'], 'compatible')
            self.assertEqual(first['voices']['state'], 'possible_conflict')
            self.assertEqual(first['voices']['seconds'], 4.)
            self.assertEqual(report['voice_pending'], 1)
            self.assertEqual(project.to_dict(), before)
            project.clips[1].intro_percussive = True
            marked = review_mix(project)['transitions'][0]
            self.assertEqual(marked['state'], 'percussion')
            self.assertEqual(marked['voices']['state'], 'possible_conflict')
            project.clips[1].cue_in = 12.
            later = review_mix(project)
            self.assertEqual(later['transitions'][0]['voices']['state'], 'not_flagged')
            self.assertEqual(later['count'], report['count'] - 1)
            Path(project.tracks['0'].path).write_bytes(b'replacement audio')
            stale = review_mix(project)
            self.assertEqual(stale['transitions'][0]['voices']['state'], 'not_checked')
            self.assertEqual(stale['voice_pending'], 2)

    def test_invalid_overlap_keeps_review_readable_and_explains_voice_check_limit(self):
        project = session(); project.clips[1].cue_out = 1.
        report = review_mix(project)
        evidence = report['transitions'][0]['voices']
        self.assertEqual(evidence['state'], 'not_checked')
        self.assertFalse(evidence['can_check'])
        self.assertIn('longer than the available audio', evidence['reason'])
        self.assertTrue(any(kind == 'layout' and 'Shorten it' in note for song in report['songs'] for kind, note in song['notes']))
        project.tempo_enabled = True
        project.tempo_points = [{'beat': 0., 'bpm': 120.}, {'beat': 32., 'bpm': 130.}]
        curved = review_mix(project)
        self.assertTrue(any(kind == 'layout' for song in curved['songs'] for kind, _ in song['notes']))
        self.assertTrue(any(kind == "missing" for song in report["songs"] for kind, _ in song["notes"]))


class RecommendationControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(Studio, "load_bands"):
            self.w = Studio(Path(self.temp.name))
        self.addCleanup(self.w.close)
        self.w.session = session()
        self.w.selected_id = self.w.session.clips[1].id
        self.w.refresh()

    def test_review_application_is_explicit_undoable_and_limits_do_not_change_audio(self):
        w = self.w
        before = copy.deepcopy(w.session.to_dict())
        w.library_tabs.setCurrentIndex(1)
        self.assertEqual(w.session.to_dict(), before)
        tempo = tempo_advice(w.session)
        button = next(b for b in w.mix_review.findChildren(QPushButton) if b.text() == f'Use {tempo["target"]:g} BPM')
        button.click()
        self.assertEqual(w.session.target_bpm, tempo["target"])
        self.assertEqual(w.act_undo.text(), "Undo Change mix tempo")
        w.undo()
        self.assertEqual(w.session.to_dict(), before)
        revision = w.revision
        w.mix_review.pitch_limit.setValue(0)
        self.assertFalse(w.match_btn.isEnabled())
        self.assertEqual(w.session.to_dict(), before)
        self.assertEqual(w.revision, revision)
        self.assertEqual(w.prefs["suggested_pitch_limit"], 0)

    def test_match_key_checks_both_neighbors_and_stale_review_actions_are_ignored(self):
        w = self.w
        self.assertTrue(w.match_btn.isEnabled())
        w.match_btn.click()
        self.assertEqual(w.selected_clip().shift, -2)
        w.undo()
        w.session.tracks["2"].key = "A minor"
        w.refresh()
        self.assertFalse(w.match_btn.isEnabled())
        self.assertNotIn("Try", w.harmony(1)[1])
        before = copy.deepcopy(w.session.to_dict())
        w.review_action("pitch", w.selected_id, -2)
        self.assertEqual(w.session.to_dict(), before)
        self.assertIn("without worsening", w.match_btn.toolTip())

    def test_voice_review_opens_exact_pair_without_reanalyzing_saved_evidence(self):
        from studio_transition_editor import TransitionEditor
        w = self.w; w.session = voice_project(self.temp.name); w.refresh()
        before = copy.deepcopy(w.session.to_dict())
        button = next(b for b in w.mix_review.findChildren(QPushButton, 'reviewVoices')
                      if b.property('clipId') == w.session.clips[1].id)
        self.assertEqual(button.text(), 'Review voices…')
        self.assertIn('1 voice check still pending', w.mix_review.summary.text())
        with patch.object(TransitionEditor, 'check_voices') as check:
            button.click(); d = w.transition_editor
            self.assertIsNotNone(d); self.assertEqual(d.index, 1); check.assert_not_called()
        self.assertIn('Possible voices together', d.voices.summary.text())
        self.assertEqual(w.session.to_dict(), before)
        d.reject()
        button = next(b for b in w.mix_review.findChildren(QPushButton, 'reviewVoices')
                      if b.property('clipId') == w.session.clips[2].id)
        self.assertEqual(button.text(), 'Check voices…')
        with patch.object(TransitionEditor, 'check_voices') as check:
            button.click(); d = w.transition_editor
            self.assertEqual(d.index, 2); check.assert_called_once_with()
        self.assertEqual(w.session.to_dict(), before)
        d.reject()


if __name__ == "__main__":
    unittest.main()
