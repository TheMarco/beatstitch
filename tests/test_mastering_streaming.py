"""Whole-mix A/B stays bounded in memory and switches on the same PCM clock."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from studio_mastering_audio import PCMView, MasteringTransport, prepare_playback
from studio_mastering_player import MasteringLab
from tests.test_mastering_audio import Device, Sink, write_folder


class MasteringStreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_preparation_uses_bounded_reads_and_live_switching_does_not_scan_peaks(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            write_folder(folder, n=240000)
            sizes = []
            original = PCMView.read_range
            def tracked(view, start, count):
                sizes.append(count); return original(view, start, count)
            with patch.object(PCMView, 'read_range', tracked), patch('studio_mastering_audio.sf.read', side_effect=AssertionError('Do not decode the entire mix')):
                prepared = prepare_playback(folder)
                self.assertLessEqual(max(sizes), 65536)
                player = MasteringTransport(sink_factory=Sink)
                window = MasteringLab(folder, player=player, prepared=prepared, smoke_test=True)
                handles = [prepared[0].array_for(v).file for v in ('current', 'mastered', 'matched')]
                try:
                    self.assertTrue(window.ready)
                    self.assertIsInstance(window.data.current, PCMView)
                    player.set_device(Device()); player.play()
                    starts, resets, stream = player.start_count, player.reset_count, player.stream
                    with patch.object(PCMView, 'peaks', side_effect=AssertionError('A live toggle must use cached waveforms')):
                        for i in range(70):
                            window.choose('B' if i % 2 else 'A')
                            window.match.setChecked(i % 3 == 0)
                            player.sink.consume(48); player._pump()
                    self.assertTrue(player.playing)
                    self.assertEqual((player.start_count, player.reset_count), (starts, resets))
                    self.assertIs(player.stream, stream)
                    self.assertGreater(player.position(), 0)
                    self.assertLessEqual(max(sizes), 65536)
                finally:
                    window.close()
                self.assertTrue(all(file.closed for file in handles))

    def test_file_passages_wrap_and_finish_the_current_lap_when_loop_is_disabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); write_folder(folder, n=48000)
            data, _ = prepare_playback(folder)
            player = MasteringTransport()
            try:
                player.load_data(data); player.set_passage(1000, 1100); player.set_loop(True)
                first = np.frombuffer(player.mixer.read(250), dtype='<f4')
                self.assertEqual(len(first), 500)
                player.set_loop(False)
                self.assertEqual(len(player.mixer.read(200)), 50 * 8)
                self.assertEqual(player.mixer.read(1), b'')
            finally: player.close()


if __name__ == '__main__': unittest.main()
