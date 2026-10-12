"""Transport routing checks: mix, source and transition listening must stay distinct."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch
import numpy as np

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from desktop_app import Studio
from studio_engine import Clip, Session, Track


class Player:
    def __init__(self):
        self.url, self.pos = QUrl(), 0
        self.state = QMediaPlayer.PlaybackState.PausedState
        self.loops = QMediaPlayer.Loops.Once
        self.play_count = 0

    def source(self): return self.url
    def setSource(self, url): self.url, self.pos = url, 0
    def position(self): return self.pos
    def setPosition(self, value): self.pos = value
    def duration(self): return 300000
    def playbackState(self): return self.state
    def mediaStatus(self): return QMediaPlayer.MediaStatus.LoadedMedia
    def pause(self): self.state = QMediaPlayer.PlaybackState.PausedState
    def stop(self): self.pause(); self.pos = 0
    def play(self): self.state = QMediaPlayer.PlaybackState.PlayingState; self.play_count += 1
    def setLoops(self, loops): self.loops = loops


class ListeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with patch.object(Studio, "load_bands"):
            self.w = Studio(self.root)
        self.addCleanup(self.w.close)
        self.real_player = self.w.player
        self.w.player = Player()
        self.w.source_audition.player = Player()
        source = self.root / "song.wav"
        source.touch()
        self.track = Track("a", str(source), "Song", 240, 128, "C minor", cue_out=240)
        self.w.session = Session(tracks={"a": self.track}, clips=[Clip("a", 0, 240) for _ in range(3)])
        self.w.selected_id = self.w.session.clips[2].id
        self.w.refresh()

    def record(self, kind="mix", index=None, start=0, duration=300):
        path = self.root / f"{kind}-{index}.wav"
        path.touch()
        result = dict(path=str(path), revision=self.w.revision, kind=kind, transition=index,
                      start=start, duration=duration, title=kind)
        self.w.rendered_assets[(kind, index)] = result
        return result

    def test_main_play_prepares_full_mix_from_unrendered_playhead(self):
        w = self.w
        w.seek_timeline(75)
        with patch.object(w, "start_render") as render, patch.object(w, "preview") as preview:
            w.toggle_play()
            self.assertIsNone(render.call_args.args[1])
            self.assertEqual(w.mix_cursor, 75)
            preview.assert_not_called()

    def test_mix_cursor_survives_transition_and_main_play_restores_mix(self):
        w = self.w
        mix = self.record()
        w.activate_audio(mix, 75, True)
        w.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)
        self.assertEqual(w.player.position(), 75000)
        blend = self.record("transition", 2, 210, 30)
        w.activate_audio(blend, 210, True)
        w.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)
        w.player.setPosition(12000)
        w.sync_position()
        self.assertEqual(w.mix_cursor, 75)
        w.toggle_play()
        w.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)
        self.assertEqual((w.listen_kind, w.player.position()), ("mix", 75000))
        self.assertEqual(w.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)

    def test_cached_transition_loops_but_main_mix_does_not(self):
        w = self.w
        self.record("transition", 2, 442.5, 30)
        w.loop_blend.setChecked(True)
        with patch.object(w, "start_render") as render:
            w.preview()
            w.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)
            render.assert_not_called()
        self.assertEqual(w.player.loops, QMediaPlayer.Loops.Infinite)
        w.activate_audio(self.record(), 0, True)
        self.assertEqual(w.player.loops, QMediaPlayer.Loops.Once)

    def test_edit_never_plays_stale_mix_and_preserves_cursor(self):
        w = self.w
        w.activate_audio(self.record(), 75, True)
        w.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)
        w.set_transition_value("in", "blend_bars", 12)
        self.assertEqual(w.mix_cursor, 75)
        count = w.player.play_count
        with patch.object(w, "start_render") as render:
            w.toggle_play()
            render.assert_called_once()
        self.assertEqual(w.player.play_count, count)

    def test_metadata_and_disabled_pitch_edits_keep_playing_but_audio_edits_stop(self):
        w = self.w
        w.activate_audio(self.record(), 75, True)
        w.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)
        self.track.name, self.track.key = 'New title', 'G minor'
        self.track.beat_grid = {'bpm': 128, 'anchor': 2}
        w.session.clips[0].semitones = 3  # Transposition is disabled.
        w.changed()
        self.assertEqual(w.audio_revision, w.revision)
        self.assertEqual(w.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(w.player.position(), 75000)
        w.session.clips[0].transpose = True
        w.changed()
        self.assertEqual(w.audio_revision, -1)
        self.assertEqual(w.player.playbackState(), QMediaPlayer.PlaybackState.PausedState)

    def test_unchanged_pair_keeps_playing_when_upstream_duration_changes(self):
        from studio_render_cache import listening_recipe
        w = self.w
        recipe = listening_recipe(w.session, 'transition', 2)
        record = {**self.record('transition', 2), **recipe}
        w.activate_audio(record, recipe['start'] + 2, True)
        w.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)
        w.session.clips[0].cue_out -= 10
        w.changed()
        self.assertEqual(w.audio_revision, w.revision)
        self.assertAlmostEqual(w.audio_origin, recipe['start'] - 10)
        self.assertEqual(w.player.position(), 2000)
        self.assertEqual(w.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        w.session.clips.insert(0, Clip('a', 0, 240))
        w.changed()
        self.assertEqual(w.audio_revision, w.revision)
        self.assertEqual(w.listen_transition, 3)
        self.assertEqual(w.loaded_label, 'Transition 3 → 4')
        # A changed source is different sound, even without an app edit.
        Path(self.track.path).write_bytes(b'changed source')
        self.assertFalse(w.refresh_prepared_audio())
        self.assertEqual(w.audio_revision, -1)

    def test_appending_song_preserves_prepared_pair_but_invalidates_full_mix(self):
        w = self.w
        blend = w.cached_audio('transition', 1)  # No audio yet.
        self.assertIsNone(blend)
        self.record('mix')
        w.cached_audio('mix')
        self.record('transition', 1)
        blend = w.cached_audio('transition', 1)
        w.session.clips.append(Clip('a', 0, 240))
        w.changed()
        self.assertEqual(w.cached_audio('transition', 1)['path'], blend['path'])
        self.assertIsNone(w.cached_audio('mix'))

    def test_source_audition_does_not_add_or_modify_clips(self):
        w = self.w
        original = w.session.to_dict()
        source = w.source_audition
        source.set_track(self.track)
        source.listen_at(210)
        source.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)
        self.assertEqual(source.player.position(), 210000)
        self.assertEqual(source.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(w.session.to_dict(), original)
        source.seek_to(5000)
        self.assertEqual(source.player.position(), 5000)
        with patch.object(w, "start_render"):
            w.toggle_play()
        self.assertEqual(source.player.playbackState(), QMediaPlayer.PlaybackState.PausedState)

    def test_review_visits_each_transition_without_changing_mix(self):
        w = self.w
        original = w.session.to_dict()
        with patch.object(w, "preview") as preview:
            w.review_transitions()
            self.assertEqual(w.transition_index(), 1)
            w.next_review_transition()
            self.assertEqual(w.transition_index(), 2)
            self.assertEqual(preview.call_count, 2)
            w.next_review_transition()
        self.assertFalse(w.reviewing)
        self.assertEqual(w.session.to_dict(), original)

    def prepared_review(self):
        from studio_engine import preview_range
        for index in (1, 2):
            start, end = preview_range(self.w.session, index)
            self.record('transition', index, start / 48000, (end - start) / 48000)
        self.w.review_transitions()
        self.w.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)

    def test_review_waits_for_decision_and_revisits_unchecked_only(self):
        w = self.w
        original = w.session.to_dict()
        self.prepared_review()
        self.assertTrue(w.review_keep.isEnabled())
        w.media_ready(QMediaPlayer.MediaStatus.EndOfMedia)
        QApplication.processEvents()
        self.assertEqual(w.transition_index(), 1)
        w.keep_review_transition()
        w.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)
        self.assertEqual(w.transition_index(), 2)
        self.assertEqual(len(w.listening_review.kept), 1)
        self.assertEqual(w.review_keep.text(), 'Keep and finish')
        self.assertIn('finish this pass', w.review_keep.toolTip())
        self.assertEqual(w.review_next.text(), 'Finish')
        self.assertIn('unchecked blends', w.review_next.toolTip())
        w.next_review_transition()
        self.assertFalse(w.reviewing)
        self.assertIn('1 still to check', w.review_label.text())
        w.resume_review()
        self.assertEqual(w.transition_index(), 2)
        w.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)
        w.keep_review_transition()
        self.assertEqual(len(w.listening_review.kept), 2)
        self.assertIn('0 still to check', w.review_label.text())
        self.assertEqual(w.session.to_dict(), original)
        self.assertEqual(w.history, [])

    def test_review_change_returns_to_same_pair_with_undo_and_requires_listening(self):
        w = self.w
        self.prepared_review()
        w.keep_review_transition()
        w.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)
        original = w.session.to_dict()
        w.change_review_transition()
        editor = w.transition_editor
        self.assertIsNotNone(editor)
        editor.bass.setChecked(False)
        with patch.object(w, 'start_render') as render:
            editor.apply_changes()
            QApplication.processEvents()
            self.assertTrue(w.reviewing)
            self.assertEqual(w.transition_index(), 2)
            self.assertEqual(render.call_args.kwargs['transition'], 2)
        self.assertFalse(w.review_keep.isEnabled())
        # The preceding pair's unchanged sound keeps its listening decision.
        self.assertEqual(len(w.listening_review.kept), 1)
        self.assertEqual(w.history[-1]['action'], 'Edit 2 → 3 blend')
        w.undo()
        self.assertEqual(w.session.to_dict(), original)
        self.assertTrue(w.reviewing)

    def test_review_pitch_edit_invalidates_both_neighbors_and_source_changes_cannot_be_kept(self):
        w = self.w
        self.prepared_review()
        w.keep_review_transition()
        w.media_ready(QMediaPlayer.MediaStatus.LoadedMedia)
        w.keep_review_transition()
        self.assertEqual(len(w.listening_review.kept), 2)
        middle = w.session.clips[1]
        w.edit(lambda: (setattr(middle, 'transpose', True), setattr(middle, 'semitones', 1)))
        self.assertEqual(len(w.listening_review.kept), 0)
        w.undo()
        self.prepared_review()
        Path(self.track.path).write_bytes(b'new audio source')
        w.keep_review_transition()
        self.assertEqual(w.transition_index(), 1)
        self.assertFalse(w.review_keep.isEnabled())
        self.assertEqual(len(w.listening_review.kept), 0)

    def test_ending_review_while_preparing_does_not_start_late_playback(self):
        w = self.w
        with patch.object(w, 'job') as job:
            w.review_transitions()
        finished = job.call_args.args[1]
        w.dismiss_review()
        with patch.object(w, 'activate_audio') as activate:
            finished(dict(path=str(self.root / 'late.wav'), duration=30, reused_tracks=0, processed_tracks=2,
                          lufs=-18, true_peak_db=-2))
        activate.assert_not_called()
        self.assertFalse(w.reviewing)
        self.assertTrue(w.listening_review.started)

    def test_review_choices_reconcile_order_and_reset_with_project(self):
        w = self.w
        self.prepared_review()
        w.keep_review_transition()
        # Appending preserves the kept pair; it appears in the next remaining pass.
        w.edit(lambda: w.session.clips.append(Clip('a', 0, 240)))
        self.assertEqual(len(w.listening_review.kept), 1)
        # Removing the current pair stops the walk without accepting a different pair.
        w.edit(lambda: w.session.clips.pop(2))
        self.assertFalse(w.reviewing)
        self.assertEqual(len(w.listening_review.kept), 1)
        w.reset_listening()
        self.assertFalse(w.listening_review.started)
        self.assertEqual(w.listening_review.kept, {})

    def test_discarding_review_draft_replays_same_pair_but_manual_navigation_pauses_walk(self):
        w = self.w
        self.prepared_review()
        original = w.session.to_dict()
        w.change_review_transition()
        w.transition_editor.bass.setChecked(False)
        w.transition_editor.reject()
        QApplication.processEvents()
        self.assertTrue(w.reviewing)
        self.assertEqual(w.listen_transition, 1)
        self.assertEqual(w.session.to_dict(), original)
        w.select_transition(2)
        self.assertFalse(w.reviewing)
        self.assertEqual(w.listening_review.kept, {})

    def test_real_qt_load_applies_pending_mix_seek_before_play(self):
        w = self.w
        w.player = self.real_player
        w.audio.setMuted(True)
        record = self.record(duration=6)
        with wave.open(record["path"], "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            audio.writeframes(bytes(6 * 48000 * 4))
        w.activate_audio(record, 2, False)
        for _ in range(100):
            if w.pending_playback is None:
                break
            QTest.qWait(20)
        self.assertIsNone(w.pending_playback)
        self.assertEqual(w.player.position(), 2000)
        self.assertEqual(w.mix_cursor, 2)
        self.assertNotEqual(w.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)

    def test_real_qt_paused_end_seek_stays_at_end_and_play_restarts(self):
        from PySide6.QtCore import Qt
        from studio_render_cache import audio_recipe
        w = self.w; w.player = self.real_player; w.audio.setMuted(True)
        self.track.duration = 6
        w.session.clips = [Clip('a', 0, 6)]
        w.session.tempo_enabled = True
        w.session.tempo_points = [{'beat': 0, 'bpm': 124}, {'beat': 12, 'bpm': 132}]
        w.refresh()
        recipe = audio_recipe(w.session)
        record = {**self.record(duration=recipe['duration']), **recipe}
        with wave.open(record['path'], 'wb') as audio:
            audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(48000)
            audio.writeframes(bytes(round(recipe['duration'] * 48000) * 4))
        w.activate_audio(record, 2, False)
        for _ in range(100):
            if w.pending_playback is None: break
            QTest.qWait(20)
        self.assertIsNone(w.pending_playback)
        w.show(); w.activateWindow(); w.timeline.setFocus(); QApplication.processEvents()
        with patch.object(w, 'cached_audio', return_value=record):
            QTest.keyClick(w.timeline, Qt.Key.Key_End)
        for _ in range(100):
            if w.pending_playback is None: break
            QTest.qWait(20)
        self.assertIsNone(w.pending_playback)
        self.assertAlmostEqual(w.player.position() / 1000, recipe['duration'], delta=.002)
        self.assertAlmostEqual(w.mix_cursor, 6, delta=.002)
        self.assertNotEqual(w.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        w.toggle_play()
        self.assertLess(w.player.position(), 50)
        self.assertEqual(w.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)

    def test_export_reuses_master_and_writes_tracklist_without_changing_playback(self):
        w = self.w
        self.track.duration = 4
        for clip in w.session.clips:
            clip.cue_out, clip.blend_bars = 4, 1
        w.refresh()
        original = w.session.to_dict()
        duration = w.timeline.duration()
        master = self.record(duration=duration)
        with wave.open(master["path"], "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            tone = (3000 * np.sin(2 * np.pi * 440 * np.arange(round(duration * 48000)) / 48000)).astype('<i2')
            audio.writeframes(np.column_stack([tone, tone]).tobytes())
        destination = self.root / "finished.mp3"
        def synchronous_job(work, finished, **kwargs):
            finished(work(lambda _: None, lambda _: None))
        with patch.object(w, "job", side_effect=synchronous_job), \
             patch("desktop_app.render_session", side_effect=AssertionError("Cached full mix should be reused")):
            w.start_export(destination, "mp3", True)
        self.assertTrue(destination.is_file())
        self.assertTrue(destination.with_suffix(".tracklist.csv").is_file())
        self.assertEqual(w.player.play_count, 0)
        self.assertEqual(w.session.to_dict(), original)
        self.assertIn("finished.mp3", w.notice.message.text())


if __name__ == "__main__": unittest.main()
