"""Focused offscreen checks for the detector pilot review workbench.

Builds one frozen pilot fixture (tiny decoded WAV, real cached peaks and a
prediction file) and proves: suggestions stay hidden until a saved reference
is revealed (with exposure recorded first), saving + Next preserves explicit
markers, and unsaved edits block navigation on Cancel but not Discard.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtMultimedia import QMediaPlayer

import detector_pilot as dp
import detector_pilot_review as review
from studio_cache import digest, source_signature


def build_pilot(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    duration = 30.0
    source = root / "song.wav"
    audio = np.zeros((int(duration * 48000), 2), dtype="float32")
    audio[:, 0] = 0.1 * np.sin(2 * np.pi * 220 * np.arange(len(audio)) / 48000)
    sf.write(source, audio, 48000, subtype="PCM_16")
    record_id = "testrecord000001"
    record = {
        "id": record_id, "name": "Test Song",
        "source": source_signature(source), "source_sha256": dp.file_hash(source),
        "duration": duration, "sample_rate": 48000, "channels": 2,
        "audio": f"audio/{record_id}.wav", "waveform": f"waveforms/{record_id}.npz",
        "result": f"results/{record_id}.json",
        "creator_group": "unknown", "generated_music": "unknown",
    }
    tasks = dp.listening_tasks(record)
    plan = {"version": dp.VERSION, "dataset": str(root), "records": [record], "tasks": tasks,
            "detectors": {}, "purpose": "development_pilot_only", "ui_promotion_allowed": False,
            "reference_status": "human_labels_required", "protocol": {}}
    pilot_file = root / "pilot.json"
    pilot_file.write_text(json.dumps({**plan, "created_utc": dp.utc_now(),
                                      "plan_sha256": digest(plan)}, indent=2))
    decoded = dp.child(pilot_file, record["audio"]); decoded.parent.mkdir(parents=True, exist_ok=True)
    sf.write(decoded, audio, 48000, subtype="PCM_16")
    peaks = np.full(int(duration * dp.PEAK_RATE), 0.4, dtype="float32"); peaks[::50] = 0.9
    wave_path = dp.child(pilot_file, record["waveform"]); wave_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(wave_path, peaks=peaks, source_sha256=record["source_sha256"])
    beginning = next(t for t in tasks if t["name"] == "Beginning")
    prediction = {"version": 1, "plan_sha256": digest(plan), "source_sha256": record["source_sha256"],
                  "stages": {
                      "source_key": {"status": "completed", "result": {"state": "estimated", "reason": "fixture",
                                     "candidates": [{"key": "C major", "score": 0.5},
                                                    {"key": "G major", "score": 0.3},
                                                    {"key": "A minor", "score": 0.2}]}},
                      "beats": {"status": "completed", "result": {"tempo": {"bpm": 123.4},
                                "raw_events": {"beats": [0.5, 1.0, 1.5, 2.0], "downbeats": [0.5]},
                                "grid": {}}},
                      "content": {"status": "completed", "result": {"windows": [
                          {"start": 0.0, "end": 10.0, "state": "voice",
                           "scores": {"voice": 0.9, "drums": 0.1, "pitched": 0.2, "music": 0.5}}]}},
                      "local_keys": {"status": "completed", "result": {beginning["id"]: {
                          "state": "estimated", "keys": ["C major"], "parts": [], "reason": "fixture"}}}}}
    result_path = dp.child(pilot_file, record["result"]); result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(prediction))
    return pilot_file, record, tasks


def nav_choice(choice_text):
    """Patch the unsaved-changes dialog to click the named button."""
    def fake_exec(self):
        return 0

    def fake_clicked(self):
        return next(b for b in self.buttons() if b.text() == choice_text)

    return patch.object(QMessageBox, "exec", fake_exec), patch.object(QMessageBox, "clickedButton", fake_clicked)


class PilotReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        pilot_file, record, tasks = build_pilot(Path(temp.name))
        window = review.PilotReviewWindow(pilot_file)
        window.show()
        self.app.processEvents()
        self.addCleanup(window.close)
        # Deterministic reviewer, loading that reviewer's (empty) answers.
        window.reviewer = "Tester"
        window.reviewer_edit.setText("Tester")
        window._load_task()
        return window, pilot_file, record, tasks

    def test_suggestions_hidden_until_saved_reveal_records_exposure_first(self):
        window, pilot_file, _record, _tasks = self.make_window()
        # Starts on the Beginning passage; nothing suggested anywhere.
        self.assertEqual(window._task["name"], "Beginning")
        self.assertFalse(window.suggestions_label.isVisible())
        self.assertFalse(window.waveform.show_predictions)
        nav_text = " ".join(window.task_list.item(r).text() for r in range(window.task_list.count()))
        self.assertNotIn("BPM", nav_text)
        self.assertNotIn("C major", nav_text)
        self.assertNotIn("123", nav_text)
        self.assertFalse(window.reveal_button.isEnabled())
        # Save a reference, then reveal: exposure must be recorded while hidden.
        window._set_combo(window.voice_combo, "yes")
        window._on_edited()
        self.assertTrue(window._save_current())
        self.assertTrue(window.reveal_button.isEnabled())
        seen = {}
        real_mark = dp.mark_predictions_exposed

        def mark_and_observe(*args, **kwargs):
            seen["suggestions_visible"] = window.suggestions_label.isVisible()
            seen["wave_predictions"] = window.waveform.show_predictions
            return real_mark(*args, **kwargs)

        with patch.object(dp, "mark_predictions_exposed", mark_and_observe):
            window.reveal_button.click()
        self.assertFalse(seen["suggestions_visible"])
        self.assertFalse(seen["wave_predictions"])
        self.assertTrue(window.suggestions_label.isVisible())
        self.assertTrue(window.waveform.show_predictions)
        self.assertIn("123.4", window.suggestions_label.text())
        self.assertIn("C major", window.suggestions_label.text())
        self.assertIn("not accuracy", window.suggestions_label.text())
        exposures = dp.load_reviews(pilot_file)["exposures"]
        self.assertEqual(len(exposures), 1)
        # Changing task hides suggestions again.
        other = next(t for t in window.pilot["tasks"] if t["id"] != window.current_task_id)
        window._select_task(other["id"])
        self.assertFalse(window.waveform.show_predictions)
        self.assertFalse(window.suggestions_label.isVisible())

    def test_save_and_next_preserves_explicit_markers(self):
        window, pilot_file, _record, _tasks = self.make_window()
        task_id = window.current_task_id
        window._set_combo(window.voice_combo, "yes")
        window.waveform.set_reference_beats(
            [{"time": 2.0, "downbeat": False}, {"time": 1.0, "downbeat": False}])
        window.waveform.set_selected(0)  # 1.0s after sorting
        window._mark_downbeat()
        window._on_edited()
        self.assertEqual(window.timing_combo.currentData(), "annotated")
        self.assertFalse(window.timing_complete_check.isChecked())  # partial stays partial
        window.save_next_button.click()
        self.assertNotEqual(window.current_task_id, task_id)
        saved = dp.latest_reference(pilot_file, task_id, "Tester")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["reference"]["beats"],
                         [{"time": 1.0, "downbeat": True}, {"time": 2.0, "downbeat": False}])
        self.assertTrue((pilot_file.parent / "references.json").is_file())
        self.assertFalse(window._is_dirty())
        window._select_task(task_id)
        self.assertEqual(window.waveform.reference_beats,
                         [{"time": 1.0, "downbeat": True}, {"time": 2.0, "downbeat": False}])

    def test_unsaved_edits_block_navigation_on_cancel_but_not_discard(self):
        window, _pilot, _record, _tasks = self.make_window()
        first = window.current_task_id
        other = next(t for t in window.pilot["tasks"] if t["id"] != first)
        window.notes_edit.setPlainText("unsaved note")
        self.assertTrue(window._is_dirty())
        cancel_exec, cancel_clicked = nav_choice("Cancel")
        with cancel_exec, cancel_clicked:
            window._select_task(other["id"])
        self.assertEqual(window.current_task_id, first)
        discard_exec, discard_clicked = nav_choice("Discard")
        with discard_exec, discard_clicked:
            window._select_task(other["id"])
        self.assertEqual(window.current_task_id, other["id"])
        self.assertEqual(window.notes_edit.toPlainText(), "")

    def test_wide_waveform_keeps_transients_at_their_source_time(self):
        wave = review.PilotWaveform()
        peaks = np.zeros(1000); peaks[500] = 1.
        wave.set_task(peaks, 50., 60.)
        displayed = wave._pooled(2000)
        self.assertEqual(int(np.argmax(displayed)), 1000)

    def test_non_looping_playback_pauses_at_the_passage_boundary(self):
        window, _pilot, _record, _tasks = self.make_window()
        window._pending_seek = None
        window.loop_check.setChecked(False)
        with patch.object(window.player, 'playbackState', return_value=QMediaPlayer.PlaybackState.PlayingState), \
             patch.object(window.player, 'pause') as pause, patch.object(window.player, 'setPosition'):
            window._on_position(10500)
        self.assertTrue(pause.called)
        self.assertEqual(window.waveform.position, 10.)

    def test_save_during_alias_change_preserves_original_reviewer(self):
        window, pilot, _record, _tasks = self.make_window()
        task_id = window.current_task_id
        window._set_combo(window.voice_combo, 'yes')
        window.reviewer_edit.setText('Other reviewer')
        save_exec, save_clicked = nav_choice('Save')
        with save_exec, save_clicked:
            window._on_reviewer_finished()
        self.assertEqual(dp.latest_reference(pilot, task_id, 'Tester')['reference']['voice'], 'yes')
        self.assertIsNone(dp.latest_reference(pilot, task_id, 'Other reviewer'))
        self.assertEqual(window.voice_combo.currentData(), 'not_reviewed')


if __name__ == "__main__":
    unittest.main()
