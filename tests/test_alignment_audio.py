"""Alignment handles use real transformed audio, cached bands and cue time maps."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import djmix
from studio_alignment_audio import RATE, cue_for_offset, offset_for_cue, prepare_alignment
from studio_beat_alignment import estimate_alignment, kick_markers
from studio_engine import Clip, Session, Track, layout, render_session
from studio_timing import VERSION
from tests.test_beat_alignment import make_kicks


class AlignmentAudioTests(unittest.TestCase):
    def test_real_audio_cache_and_final_renderer_preserve_the_correction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); tracks = {}
            for identity, delay in (('a', 0.), ('b', .06)):
                audio = make_kicks(24, 120, RATE, shift=delay) * .12
                path = root / f'{identity}.wav'
                sf.write(path, np.column_stack([audio, -audio]), RATE, subtype='FLOAT')
                tracks[identity] = Track(identity, str(path), identity, 24, 120, 'A minor', cue_out=24)
            session = Session(target_bpm=120, tracks=tracks,
                              clips=[Clip('a', 0, 20), Clip('b', 4, 20, blend_bars=2, bass_swap=False)])
            before = copy.deepcopy(session.to_dict())
            report = prepare_alignment(session, 1, root / 'cache')
            with patch('studio_alignment_audio.djmix.prepare', side_effect=AssertionError('Unchanged audio must be reused')):
                cached = prepare_alignment(session, 1, root / 'cache')
            for side, other in zip(report['sides'], cached['sides']):
                np.testing.assert_array_equal(side['full'], other['full'])
            core = slice(report['margin'], report['margin'] + report['frames'])
            result = estimate_alignment(*[s['full'][core] for s in report['sides']], RATE, 120)
            self.assertEqual(result['state'], 'suggested', result)
            self.assertAlmostEqual(result['offset_seconds'], -.06, delta=.015)
            self.assertEqual(session.to_dict(), before)
            session.clips[1].cue_in = cue_for_offset(session, 1, 4, result['offset_seconds'])
            pos = layout(session); a = pos[1]['start']; b = pos[0]['end']
            render_session(session, root / 'mix.wav', cache_dir=root / 'cache', region=(a, b),
                           stems={0: root / 'a-solo.wav', 1: root / 'b-solo.wav'})
            sides = [sf.read(root / name, dtype='float32', always_2d=True)[0]
                     for name in ('a-solo.wav', 'b-solo.wav')]
            # Fades change amplitude, so compare measured attacks away from the fade endpoints.
            markers = [np.asarray(kick_markers(s, RATE, 120)) for s in sides]
            usable = markers[0][(markers[0] > .6) & (markers[0] < 3.4)]
            gaps = [float(np.min(abs(markers[1] - t))) for t in usable]
            self.assertGreater(len(gaps), 3)
            self.assertLess(max(gaps), .02)
            # The check uses the reference tempo; the accepted offset must still
            # align the same source events through a shared changing set tempo.
            session.tempo_enabled = True
            session.tempo_points = [{'beat': 0., 'bpm': 120.}, {'beat': 64., 'bpm': 132.}]
            reference_session = Session.from_dict(before)
            reference_session.tempo_enabled = True; reference_session.tempo_points = session.tempo_points
            with patch('studio_alignment_audio.djmix.prepare', side_effect=AssertionError('Reference check should reuse its audio')):
                reference = prepare_alignment(reference_session, 1, root / 'cache')
            render_session(session, root / 'curve-mix.wav', cache_dir=root / 'cache', region=(a, b),
                           stems={0: root / 'curve-a.wav', 1: root / 'curve-b.wav'})
            curved = [sf.read(root / name, dtype='float32', always_2d=True)[0] for name in ('curve-a.wav', 'curve-b.wav')]
            markers = [np.asarray(kick_markers(s, RATE, 120)) for s in curved]
            duration = len(curved[0]) / RATE
            usable = markers[0][(markers[0] > .6) & (markers[0] < duration - .6)]
            self.assertGreater(len(usable), 3)
            self.assertLess(max(float(np.min(abs(markers[1] - t))) for t in usable), .025)

    def test_stabilized_overlap_handles_can_be_prepared_and_reused(self):
        from studio_cache import source_signature
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); tracks = {}
            for identity in ('a', 'b'):
                audio = make_kicks(24, 120, RATE) * .1
                path = root / (identity + '.wav'); sf.write(path, np.column_stack([audio, audio]), RATE)
                track = Track(identity, str(path), identity, 24, 120, 'A minor', cue_out=24)
                track.timing = {'version': VERSION, 'state': 'drift', 'source': source_signature(path),
                                'map': [[0, 0], [12, 12.1], [24, 24]]}
                tracks[identity] = track
            session = Session(target_bpm=128, tracks=tracks,
                              clips=[Clip('a', 0, 20, stabilize=True), Clip('b', 4, 20, blend_bars=2, stabilize=True)])
            first = prepare_alignment(session, 1, root / 'cache')
            with patch('studio_alignment_audio.prepare_timing', side_effect=AssertionError('Reuse accepted warped audio')):
                second = prepare_alignment(session, 1, root / 'cache')
            for a, b in zip(first['sides'], second['sides']):
                np.testing.assert_array_equal(a['full'], b['full'])
            self.assertEqual(first['frames'], round(2 * 240 / 128 * RATE))

    def test_source_tempo_ratio_and_stabilization_map_round_trip(self):
        track = Track('a', 'fixture', 'Song', 40, 125, 'A minor', cue_out=40)
        clip = Clip('a', 4.123456789, 35)
        session = Session(target_bpm=128, tracks={'a': track}, clips=[Clip('a', 0, 20), clip])
        for stabilize in (False, True):
            clip.stabilize = stabilize
            track.timing = {'version': VERSION, 'state': 'drift', 'map': [[0, 0], [20, 20.2], [40, 40]]}
            original = clip.cue_in
            for offset in (-.125, .08):
                clip.cue_in = cue_for_offset(session, 1, original, offset)
                self.assertAlmostEqual(offset_for_cue(session, 1, original), offset, places=10)
                clip.cue_in = cue_for_offset(session, 1, original, 0)
                self.assertEqual(clip.cue_in, original)
            with self.assertRaises(djmix.MixError): cue_for_offset(session, 1, original, 100)


if __name__ == '__main__': unittest.main()
