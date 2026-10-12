"""Real alternative audio: alignment, matched levels, caching and one-clock switching."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication

import djmix
from studio_blend_choices import (ChoiceTransport, alternatives, change_summary,
                                  comparison_region, draft_session, prepare_choice_comparison)
from studio_engine import layout, overlap_limit
from tests.test_transitions import project
from tests.test_grid_audio import Device, Sink


class ChoiceTests(unittest.TestCase):
    def test_options_are_nonmutating_distinct_bounded_and_respect_pitch_preference(self):
        session = project()
        session.tracks['2'].key = 'D minor'
        before = copy.deepcopy(session.to_dict())
        choices = alternatives(session, 2, max_shift=2)
        signatures = {(o['clip'].blend_bars, o['clip'].bass_swap, o['clip'].shift, o['clip'].balance_levels,
                       o['clip'].transition_effect) for o in choices}
        self.assertEqual(len(choices), len(signatures))
        self.assertGreaterEqual(len(choices), 4)
        self.assertTrue(any(o['clip'].shift == -2 for o in choices))
        for option in choices:
            self.assertLessEqual(option['clip'].blend_bars, overlap_limit(session, 2)['bars'])
            layout(draft_session(session, 2, option['clip']))
        self.assertFalse(any(o['clip'].shift for o in alternatives(session, 2, max_shift=0)))
        self.assertEqual(session.to_dict(), before)

    def test_region_aligns_outgoing_song_and_excludes_neighboring_blends(self):
        session = project()
        candidate = copy.deepcopy(session)
        candidate.clips[2].blend_bars = 4
        start, end = comparison_region(session, candidate, 2)
        for version in (session, candidate):
            positions = layout(version)
            self.assertGreaterEqual(start, positions[1]['start'] + positions[1]['incoming'])
            self.assertLessEqual(end, positions[3]['start'])
            self.assertLess(start, positions[2]['start'])
            self.assertGreater(end, positions[1]['end'])
        self.assertEqual(layout(session)[1]['end'], layout(candidate)[1]['end'])
        candidate.clips[2].transpose, candidate.clips[2].semitones = True, -2
        summary = change_summary(session, 2, candidate.clips[2])
        self.assertIn('earlier', summary)
        self.assertIn('whole incoming clip', summary)
        self.assertIn('3 → 4', summary)


class AudioChoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_real_comparison_is_matched_aligned_cached_and_switches_without_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            session = project()
            for i, track in enumerate(session.tracks.values()):
                phase = np.arange(12 * djmix.SR) / djmix.SR
                audio = (.1 + i * .035) * np.sin(2 * np.pi * (220 + i * 110) * phase)
                path = root / f'{i}.wav'
                sf.write(path, np.column_stack([audio, audio]), djmix.SR, subtype='PCM_24')
                track.path, track.duration = str(path), 12
                session.clips[i].cue_out, session.clips[i].blend_bars = 12, 1
            draft = copy.deepcopy(session.clips[2]); draft.blend_bars = 2
            draft.transition_effect = 'echo'
            draft.cue_in = 1.237
            self.assertEqual(draft_session(session, 2, draft).clips[2].cue_in, 1.237)
            report = prepare_choice_comparison(session, 2, draft, root / 'audio-cache')
            directory = Path(report['folder'])
            current, rate = sf.read(directory / 'current.wav')
            candidate, _ = sf.read(directory / 'draft.wav')
            self.assertEqual(current.shape, candidate.shape)
            self.assertEqual(len(current), round(report['duration'] * rate))
            measures = [djmix.meter(directory / f'{name}.wav') for name in ('current', 'draft')]
            self.assertLess(abs(measures[0]['lufs'] - measures[1]['lufs']), .2)
            self.assertTrue(all(m['true_peak_db'] <= -1 for m in measures))
            # Before either incoming entrance, outgoing phase must be identical.
            np.testing.assert_allclose(np.corrcoef(current[4800:48000, 0], candidate[4800:48000, 0])[0, 1], 1, atol=1e-7)
            self.assertGreater(np.max(np.abs(current - candidate)), .001)
            stamp = (directory / 'current.wav').stat().st_mtime_ns
            with patch('studio_blend_choices.prepare_listening', side_effect=AssertionError('Should reuse comparison')):
                cached = prepare_choice_comparison(session, 2, draft, root / 'audio-cache')
            self.assertTrue(cached['cached'])
            self.assertEqual(stamp, (directory / 'current.wav').stat().st_mtime_ns)
            transport = ChoiceTransport(sink_factory=Sink)
            transport.load(directory); transport.set_device(Device()); transport.set_loop(True); transport.play()
            transport.sink.consume(4800); transport._pump()
            position, starts, resets = transport.position_frame(), transport.start_count, transport.reset_count
            transport.select('draft')
            transport.sink.consume(4800); transport._pump()
            self.assertGreater(transport.position_frame(), position)
            self.assertEqual((transport.start_count, transport.reset_count), (starts, resets))
            self.assertEqual(transport.mixer.target, (0., 1.))
            transport.close()
            # Source changes cannot be mistaken for a reusable comparison.
            source = Path(session.tracks['2'].path); stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            with patch('studio_blend_choices.prepare_listening', side_effect=RuntimeError('Fresh render required')):
                with self.assertRaisesRegex(RuntimeError, 'Fresh render required'):
                    prepare_choice_comparison(session, 2, draft, root / 'audio-cache')


if __name__ == '__main__':
    unittest.main()
