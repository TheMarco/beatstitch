"""Audio alignment, cache validity and uninterrupted three-way monitoring."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication

import djmix
from studio_ab_audio import FADE_FRAMES, RATE
from studio_engine import Clip, Session, Track, layout, render_session
from studio_transition_audio import BlendMixer, BlendTransport, pair_range, prepare_blend
from tests.test_ab_audio import Device, Sink


class MonitoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_end_seek_stays_at_last_loop_sample_and_nonloop_keeps_exact_end(self):
        from studio_blend_choices import ChoiceTransport
        audio = np.column_stack((np.arange(960), -np.arange(960))).astype(np.float32) / 5000
        for transport in (BlendTransport, ChoiceTransport):
            with self.subTest(transport=transport.__name__):
                player = transport(sink_factory=Sink)
                try:
                    player.mixer = player.MIXER(audio, np.zeros_like(audio))
                    player.select(player.DEFAULT); player.set_loop(True)
                    player.seek(20)
                    self.assertEqual(player.position_frame(), len(audio) - 1)
                    self.assertEqual(player.position(), 20)
                    self.assertFalse(player.playing)
                    # Resuming from End plays the final sample before wrapping.
                    heard = np.frombuffer(player.mixer.read(2), dtype='<f4').reshape(-1, 2)
                    np.testing.assert_allclose(heard, audio[[-1, 0]], atol=1e-7)
                    player.set_loop(False); player.seek(20)
                    self.assertEqual(player.position_frame(), len(audio))
                    self.assertEqual(player.position(), 20)
                    self.assertEqual(player.mixer.read(1), b'')
                    player.seek(0); self.assertEqual(player.position_frame(), 0)
                finally:
                    player.close()

    def test_monitor_switches_preserve_clock_and_mix_gain(self):
        outgoing = np.full((2000, 2), .2, dtype=np.float32)
        incoming = np.full_like(outgoing, .4)
        mixer = BlendMixer(outgoing, incoming)
        first = np.frombuffer(mixer.read(300), dtype='<f4')
        np.testing.assert_allclose(first, .6, atol=1e-7)
        mixer.select('outgoing')
        second = np.frombuffer(mixer.read(300), dtype='<f4').reshape(-1, 2)
        self.assertLessEqual(abs(np.diff(second[:, 0])).max(), .4 / FADE_FRAMES + 1e-7)
        np.testing.assert_allclose(second[-20:], .2, atol=1e-7)
        mixer.select('incoming')
        third = np.frombuffer(mixer.read(300), dtype='<f4').reshape(-1, 2)
        np.testing.assert_allclose(third[-20:], .4, atol=1e-7)
        self.assertEqual(mixer.frame, 900)

    def test_loop_partial_writes_switch_pause_and_disable_use_one_sink(self):
        # Stereo channels distinguish which part of the source clock is playing.
        audio = np.column_stack((np.arange(900), -np.arange(900))).astype(np.float32) / 5000
        player = BlendTransport(sink_factory=Sink)
        player.mixer = BlendMixer(audio, np.zeros_like(audio))
        player.set_loop(True)
        player.set_device(Device())
        player.play()
        sink = player.sink
        starts, resets, stream = player.start_count, player.reset_count, player.stream
        for i in range(170):
            player.select('blend' if i % 2 else 'outgoing')
            if i == 20:
                player.pause()
                position = player.position()
                sink.consume(300)
                self.assertEqual(player.position(), position)
                player.play()
            sink.consume(48)
            player._pump()
            self.assertIs(player.stream, stream)
            self.assertEqual(player.start_count, starts)
            self.assertEqual(player.reset_count, resets)
            self.assertLess(player.position(), 20)
        self.assertGreater(player.position_frame(), 3 * len(audio))
        player.set_loop(False)
        for _ in range(300):
            sink.consume(48)
            player._pump()
            if not player.playing:
                break
        self.assertFalse(player.playing)
        played = np.frombuffer(sink.history, dtype='<f4').reshape(-1, 2)
        self.assertEqual(len(played) % len(audio), 0)
        np.testing.assert_allclose(played, np.tile(audio, (len(played) // len(audio), 1)), atol=1e-7)
        self.assertEqual(player.position(), round(len(audio) * 1000 / RATE))
        player.play()
        self.assertTrue(player.playing)
        player.seek(7)
        self.assertEqual(player.origin, 336)
        player.close()


class PreparedBlendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache'
        t = np.arange(RATE * 15) / RATE
        tracks = {}
        for i, frequency in enumerate((93, 211, 325, 445)):
            audio = .06 * np.sin(2 * np.pi * frequency * t) + .02 * np.sin(2 * np.pi * 1900 * t)
            path = self.root / f'{i}.wav'
            sf.write(path, np.column_stack((audio, audio)), RATE, subtype='FLOAT')
            tracks[str(i)] = Track(str(i), str(path), f'Song {i + 1}', 15, 120, 'C minor')
        self.session = Session(target_bpm=120, tracks=tracks,
                               clips=[Clip(str(i), 0, 15, blend_bars=1) for i in range(2)])

    def test_pair_context_excludes_other_neighbors(self):
        session = self.session
        session.clips = [Clip(str(i), 0, 7, blend_bars=1) for i in range(4)]
        positions = layout(session)
        start, end = pair_range(session, 2)
        self.assertEqual(start, positions[0]['end'])
        self.assertEqual(end, positions[3]['start'])
        self.assertLess(start, positions[2]['start'])
        self.assertGreater(end, positions[1]['end'])

    def test_real_render_alignment_shared_gain_cache_and_invalidation(self):
        session = self.session
        before = copy.deepcopy(session.to_dict())
        report = prepare_blend(session, 1, self.cache)
        folder = Path(report['folder'])
        outgoing, _ = sf.read(folder / 'outgoing.wav', dtype='float32', always_2d=True)
        incoming, _ = sf.read(folder / 'incoming.wav', dtype='float32', always_2d=True)
        blend, _ = sf.read(folder / 'blend.wav', dtype='float32', always_2d=True)
        self.assertEqual(outgoing.shape, incoming.shape)
        self.assertEqual(outgoing.shape, blend.shape)
        self.assertEqual(len(blend), round(report['duration'] * RATE))
        np.testing.assert_allclose(outgoing + incoming, blend, atol=4e-7)
        # No incoming audio before its cue; no outgoing after its end.
        positions = layout(session)
        first_in = positions[1]['start'] - round(report['start'] * RATE)
        last_out = positions[0]['end'] - round(report['start'] * RATE)
        self.assertEqual(np.max(abs(incoming[:first_in])), 0)
        self.assertEqual(np.max(abs(outgoing[last_out:])), 0)
        self.assertEqual(session.to_dict(), before)
        # Adding a distant song, changing key labels, or moving this pair later
        # must keep its prepared audio. Returned project positions still update.
        session.clips.append(Clip('2', 0, 15, blend_bars=1))
        session.tracks['0'].key = 'G minor'
        with patch('studio_transition_audio.render_session', side_effect=AssertionError('Must reuse unchanged pair')):
            again = prepare_blend(session, 1, self.cache)
            self.assertTrue(again['cached'])
            self.assertEqual(again['folder'], report['folder'])
            session.clips.insert(0, Clip('3', 0, 15, blend_bars=1))
            moved = prepare_blend(session, 2, self.cache)
            self.assertEqual(moved['folder'], report['folder'])
            self.assertGreater(moved['start'], report['start'])
        # A real processing change must not pass as a cache hit.
        session.clips[2].bass_swap = False
        with patch('studio_transition_audio.render_session', side_effect=RuntimeError('Render expected')) as render:
            with self.assertRaisesRegex(RuntimeError, 'Render expected'):
                prepare_blend(session, 2, self.cache)
            render.assert_called_once()

    def test_invalid_and_source_destinations_are_rejected(self):
        source = Path(self.session.tracks['0'].path)
        with self.assertRaisesRegex(djmix.MixError, 'source songs'):
            render_session(self.session, self.root / 'output.wav', stems={0: source})
        with self.assertRaisesRegex(djmix.MixError, 'different filename'):
            render_session(self.session, self.root / 'same.wav', stems={0: self.root / 'same.wav'})

    def test_silent_solo_can_be_peak_checked_without_changing_normal_loudness_validation(self):
        path = self.root / 'silence.wav'
        sf.write(path, np.zeros((RATE * 2, 2), dtype=np.float32), RATE)
        self.assertEqual(djmix.meter(path, require_loudness=False)['true_peak_db'], -np.inf)
        with self.assertRaisesRegex(djmix.MixError, 'silent'):
            djmix.meter(path)


if __name__ == '__main__':
    unittest.main()
