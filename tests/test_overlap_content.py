"""Voice evidence must follow source timing and keep cue suggestions as drafts."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import djmix
from studio_cache import source_signature
from studio_content import MODEL_SHA256, VERSION, current_content
from studio_engine import Clip, Session, Track, layout
from studio_beat_grid import grid_settings
from studio_overlap_content import analyze_pair_voices, overlap_voices, review_voice_overlaps, vocal_cue_choices, voice_advice


def voice_content(track, ranges):
    scores = {'voice': .8, 'drums': .1, 'pitched': .3, 'music': .8}
    return {'version': VERSION, 'model_sha256': MODEL_SHA256,
            'source': source_signature(track.path), 'duration': track.duration,
            'windows': [{'start': a, 'end': b, 'scores': scores.copy(), 'rms_db': -18., 'state': 'voice'}
                        for a, b in ranges]}


class OverlapContentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        tracks = {}
        for name in ('out', 'in', 'next'):
            path = Path(self.temporary.name) / (name + '.wav'); path.write_bytes(b'fixture')
            tracks[name] = Track(name, str(path), name, 80., 120., 'C major', cue_out=80.)
        self.session = Session(tracks=tracks, target_bpm=120.,
                               clips=[Clip(name, 0., 60., blend_bars=8) for name in tracks])
        tracks['out'].content = voice_content(tracks['out'], [(50., 60.)])
        tracks['in'].content = voice_content(tracks['in'], [(0., 10.)])
        tracks['in'].beat_grid = grid_settings(tracks['in'], 120., 0.)

    def test_voice_collision_uses_actual_overlap_tempo_and_stabilized_time(self):
        s = self.session
        result = overlap_voices(s, 1)
        self.assertEqual(result['state'], 'possible_conflict')
        self.assertEqual(result['ranges'], [{'start': 6., 'end': 10.}])
        s.target_bpm = 60.
        self.assertEqual(overlap_voices(s, 1)['seconds'], 8.)
        s.target_bpm = 120.
        from studio_timing import VERSION as TIMING_VERSION
        s.tracks['out'].timing = {'version': TIMING_VERSION, 'state': 'drift',
                                 'map': [[0., 0.], [50., 50.2], [60., 60.], [80., 80.]]}
        s.clips[0].stabilize = True
        result = overlap_voices(s, 1)
        self.assertAlmostEqual(result['ranges'][0]['start'], 6.2)
        self.assertAlmostEqual(result['seconds'], 3.8)
        s.clips[1].transpose = True; s.clips[1].semitones = 2
        self.assertEqual(overlap_voices(s, 1), result)

    def test_no_flags_and_stale_observations_never_claim_no_vocals(self):
        s = self.session; s.clips[1].cue_in = 12.
        result = overlap_voices(s, 1)
        self.assertEqual(result['state'], 'not_flagged')
        self.assertIn('does not prove a vocal-free', voice_advice(result))
        Path(s.tracks['in'].path).write_bytes(b'a replacement song')
        result = overlap_voices(s, 1)
        self.assertEqual(result['state'], 'not_checked')
        self.assertEqual(result['checked'], [True, False])
        self.assertFalse(vocal_cue_choices(s, 1))

    def test_checking_unchanged_sources_reuses_project_observations(self):
        with patch('studio_overlap_content.analyze_content', side_effect=AssertionError('Do not reprocess unchanged sources')):
            result = analyze_pair_voices(self.session, 1, Path(self.temporary.name))
        self.assertEqual(result, {identity: self.session.tracks[identity].content for identity in ('out', 'in')})

    def test_cached_mix_review_checks_each_source_once_when_clips_repeat(self):
        self.session.clips.append(Clip('out', 0., 60., blend_bars=8))
        with patch('studio_overlap_content.current_content', wraps=current_content) as check:
            results = review_voice_overlaps(self.session)
        self.assertEqual(len(results), 3)
        self.assertEqual(check.call_count, 3)

    def test_cue_choices_preserve_neighbors_protection_and_source_metadata(self):
        s = self.session; s.clips[1].intro_percussive = True
        original = copy.deepcopy(s.to_dict())
        choices = vocal_cue_choices(s, 1)
        self.assertEqual(choices[0]['time'], 4.)
        self.assertEqual(choices[0]['seconds'], 0.)
        self.assertEqual(choices[0]['source_delta'], 4.)
        self.assertEqual(choices[0]['mix_delta'], -4.)
        self.assertEqual(s.to_dict(), original)
        for choice in choices:
            draft = copy.deepcopy(s); draft.clips[1].cue_in = choice['time']; layout(draft)
        s.clips[1].protected_moments = [{'start': 20., 'end': 22., 'kind': 'favorite', 'label': 'Keep'}]
        for choice in vocal_cue_choices(s, 1):
            draft = copy.deepcopy(s); draft.clips[1].cue_in = choice['time']; layout(draft)
        s.tracks['in'].beat_grid = {}
        self.assertFalse(vocal_cue_choices(s, 1))

    def test_echo_repeats_voice_evidence_from_its_actual_send_window(self):
        s = self.session
        # Overlap is 44–60 s. Echo captures 57.5–58 and repeats through 60.
        s.tracks['out'].content = voice_content(s.tracks['out'], [(57.5, 58.)])
        s.tracks['in'].content = voice_content(s.tracks['in'], [(14.5, 16.)])
        self.assertEqual(overlap_voices(s, 1)['seconds'], 0.)
        s.clips[1].transition_effect = 'echo'
        result = overlap_voices(s, 1)
        self.assertEqual(result['ranges'], [{'start': 14.5, 'end': 16.}])
        self.assertEqual(result['seconds'], 1.5)
        s.tracks['out'].content = voice_content(s.tracks['out'], [(58.5, 60.)])
        self.assertEqual(overlap_voices(s, 1)['seconds'], 0.)


if __name__ == '__main__':
    unittest.main()
