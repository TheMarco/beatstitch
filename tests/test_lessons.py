"""Lesson audio, bounded loading, uninterrupted switching and project isolation."""
import copy
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import djmix
from desktop_app import Studio
from studio_blend_choices import ChoiceTransport
from studio_engine import Clip, Session, Track
from studio_lessons import LESSONS, asset_root, lesson_audio
from tests.test_grid_audio import Device, Sink


class Output(Device):
    def description(self): return 'Test headphones'


class LessonAssetsTests(unittest.TestCase):
    def test_actual_audio_is_aligned_matched_distinct_and_has_headroom(self):
        for lesson in LESSONS:
            with self.subTest(lesson=lesson['id']):
                report = lesson_audio(lesson['id']); folder = Path(report['folder'])
                self.assertEqual(report['duration'], 16)
                audio = [sf.read(folder / f'{name}.wav', dtype='float32', always_2d=True)[0]
                         for name in ('current', 'draft')]
                self.assertEqual(audio[0].shape, (16 * djmix.SR, 2))
                self.assertEqual(audio[0].shape, audio[1].shape)
                self.assertTrue(all(np.isfinite(a).all() for a in audio))
                self.assertGreater(float(np.sqrt(np.mean((audio[0] - audio[1]) ** 2))), .002)
                self.assertTrue(all(np.max(np.abs(a[[0, -1]])) < 1e-5 for a in audio))
                measured = [djmix.meter(folder / f'{name}.wav') for name in ('current', 'draft')]
                self.assertLess(abs(measured[0]['lufs'] - measured[1]['lufs']), .2)
                self.assertTrue(all(m['true_peak_db'] <= -2.8 for m in measured))

    def test_missing_changed_and_oversize_audio_fail_before_transport_loading(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(djmix.MixError, 'missing or damaged'): lesson_audio('keys', root)
            shutil.copytree(asset_root() / 'keys', root / 'keys')
            path = root / 'keys/current.wav'
            with path.open('r+b') as f:
                f.seek(-1, 2); f.write(b'X')
            with self.assertRaisesRegex(djmix.MixError, 'missing or damaged'): lesson_audio('keys', root)
        with patch('studio_lessons.sf.info') as info:
            info.return_value.samplerate = djmix.SR; info.return_value.channels = 2
            info.return_value.frames = 21 * djmix.SR
            with self.assertRaisesRegex(djmix.MixError, 'missing or damaged'): lesson_audio('keys')
        with self.assertRaises(djmix.MixError): lesson_audio('../anything')

    def test_clashing_melody_has_the_expected_semitone_difference_in_audio(self):
        folder = Path(lesson_audio('keys')['folder'])
        values = []
        for name in ('current', 'draft'):
            audio, _ = sf.read(folder / f'{name}.wav', always_2d=True)
            mono = audio[2 * djmix.SR:14 * djmix.SR].mean(axis=1)
            spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2
            frequencies = np.fft.rfftfreq(len(mono), 1 / djmix.SR)
            # C-sharp 4 belongs to A's melody; B moved it down to C4.
            values.append(spectrum[np.abs(frequencies - 277.18) < 2].sum())
        self.assertGreater(values[0], 5 * values[1])


class LessonWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        bands = patch.object(Studio, 'load_bands'); bands.start(); self.addCleanup(bands.stop)
        self.w = Studio(Path(self.temp.name)); self.addCleanup(self.close)
        self.w.session = Session(tracks={'x': Track('x', 'missing.wav', 'My song', 120, 120, cue_out=120)},
                                 clips=[Clip('x', 0, 120)])
        self.w.selected_id = self.w.session.clips[0].id; self.w.mix_cursor = 11.; self.w.refresh()
        self.before = copy.deepcopy(self.w.session.to_dict())
        self.output = patch('studio_lessons_ui.QMediaDevices'); self.devices = self.output.start(); self.addCleanup(self.output.stop)
        self.devices.defaultAudioOutput.return_value = Output(); self.devices.audioOutputs.return_value = [Output()]
        transport = patch('studio_lessons_ui.ChoiceTransport', side_effect=lambda parent: ChoiceTransport(parent, sink_factory=Sink))
        transport.start(); self.addCleanup(transport.stop)
        self.w.show_lessons(); self.d = self.w.lessons_dialog; self.app.processEvents()

    def close(self):
        if self.w.lessons_dialog: self.w.lessons_dialog.reject()
        if self.w.help_window: self.w.help_window.close()
        self.w.close(); self.app.processEvents()

    def test_switching_uses_one_clock_and_lesson_changes_do_not_autoplay(self):
        d = self.d; self.assertTrue(d.ready); self.assertFalse(d.player.playing)
        d.toggle(); p = d.player; sink = p.sink
        sink.consume(480); p._pump()
        before, starts, resets = p.position_frame(), p.start_count, p.reset_count
        d.choices.buttons()[1].click(); sink.consume(480); p._pump()
        self.assertGreater(p.position_frame(), before)
        self.assertEqual((p.start_count, p.reset_count), (starts, resets))
        self.assertEqual(p.mixer.target, (0., 1.))
        d.lesson.setCurrentIndex(2)
        self.assertFalse(p.playing); self.assertEqual(p.position(), 0)
        self.assertTrue(d.choices.buttons()[0].isChecked())
        d.volume.setValue(12); self.assertNotEqual(self.w.volume.value(), 12)
        d.reject(); self.app.processEvents()
        self.assertEqual(self.w.session.to_dict(), self.before); self.assertEqual(self.w.mix_cursor, 11)
        self.assertEqual(self.w.history, []); self.assertIsNone(self.w.lessons_dialog)

    def test_disconnect_pauses_and_reconnect_waits_for_listen(self):
        d = self.d; d.toggle(); d.player.sink.consume(480); d.player._pump()
        self.devices.defaultAudioOutput.return_value = None; d.refresh_output()
        self.assertFalse(d.player.playing); self.assertFalse(d.play.isEnabled())
        self.assertIn('Output unavailable', d.status.text())
        self.devices.defaultAudioOutput.return_value = Output(); d.refresh_output()
        self.assertTrue(d.play.isEnabled()); self.assertFalse(d.player.playing)
        d.show_help(); self.assertEqual(d.help_window.current_topic, 'timing')

    def test_focused_buttons_own_space_and_failed_lesson_can_be_replaced(self):
        d = self.d; b = d.choices.buttons()[1]; b.setFocus(); self.app.processEvents()
        QTest.keyClick(b, Qt.Key.Key_Space)
        self.assertTrue(b.isChecked()); self.assertFalse(d.player.playing)
        with patch('studio_lessons_ui.lesson_audio', side_effect=djmix.MixError('Example unavailable')):
            d.lesson.setCurrentIndex(1)
        self.assertFalse(d.play.isEnabled()); self.assertFalse(d.seek.isEnabled())
        self.assertIn('Example unavailable', d.status.text())
        d.lesson.setCurrentIndex(2); self.assertTrue(d.play.isEnabled())
        self.assertFalse(d.player.playing)


if __name__ == '__main__': unittest.main()
