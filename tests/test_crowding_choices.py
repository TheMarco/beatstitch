import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import djmix
from studio_beat_grid import grid_settings
from studio_crowding_choices import (SEARCH_LIMIT, audible_entry, choice_fingerprint,
                                     cue_candidates, draft_for_cue, search_cues)
from studio_engine import Clip, Session, Track, layout
from tests.test_overlap_content import voice_content


class CrowdingChoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        tracks = {}
        for identity in ('a', 'b', 'c'):
            path = self.root / (identity + '.wav'); path.write_bytes(b'source fixture')
            tracks[identity] = Track(identity, str(path), identity, 200, 120, 'C major', cue_out=200)
        self.session = Session(tracks=tracks, target_bpm=120,
                               clips=[Clip('a', 0, 180, blend_bars=4),
                                      Clip('b', 80, 180, blend_bars=4, intro_percussive=True),
                                      Clip('c', 0, 180, blend_bars=4)])
        tracks['b'].beat_grid = grid_settings(tracks['b'], 120, 0)

    def test_candidates_are_bounded_supported_and_keep_neighbors_and_protection(self):
        s = self.session; before = copy.deepcopy(s.to_dict())
        choices = cue_candidates(s, 1)
        self.assertTrue(choices); self.assertLessEqual(len(choices), SEARCH_LIMIT)
        for time in choices:
            self.assertEqual(time % 2, 0)
            self.assertLessEqual(abs(time - 80), 32 * 2)
            d = draft_for_cue(s, 1, time); layout(d)
            self.assertEqual(d.clips[0], s.clips[0]); self.assertEqual(d.clips[2], s.clips[2])
            self.assertEqual(d.clips[1].cue_out, 180); self.assertEqual(d.clips[1].blend_bars, 4)
            self.assertFalse(d.clips[1].intro_percussive)
        self.assertEqual(s.to_dict(), before)
        s.clips[1].protected_moments = [{'start': 95., 'end': 120., 'label': 'Keep', 'kind': 'drop'}]
        self.assertTrue(all(t + 8 <= 95 for t in cue_candidates(s, 1)))
        s.clips[1].protected_moments = []; s.clips[1].cue_out = 110
        self.assertTrue(all(110 - t >= 24 for t in cue_candidates(s, 1)))

    def test_missing_or_stale_grid_does_not_invent_entrances(self):
        self.session.tracks['b'].beat_grid = {}
        self.assertEqual(cue_candidates(self.session, 1), [])
        self.session.tracks['b'].beat_grid = grid_settings(self.session.tracks['b'], 120, 0)
        Path(self.session.tracks['b'].path).write_bytes(b'replaced source')
        self.assertEqual(cue_candidates(self.session, 1), [])

    def run_search(self, measurement=None, audible=True, progress=lambda _: None, same_activity=False):
        def measure(s, index, report, cache_dir, progress):
            progress('checking')
            seconds = measurement(s.clips[index].cue_in) if measurement else (8. if s.clips[index].cue_in == 80 else 4.)
            side = {'active': True, 'events': 6, 'notes': 4, 'coverage': .9}
            parts = [{'start': float(a), 'end': float(a + 4), 'flagged': a < seconds,
                      'different_fraction': .9 if seconds == 8 or same_activity else .25,
                      'audible_fraction': 1., 'outgoing': side.copy(), 'incoming': side.copy()} for a in (0, 4)]
            return {'state': 'possible_crowding' if seconds else 'not_flagged', 'seconds': seconds,
                    'overlap_seconds': 8., 'parts': parts}
        with patch('studio_crowding_choices.prepare_blend', return_value={'folder': 'prepared'}) as render, \
                patch('studio_crowding_choices.analyze_crowding', side_effect=measure), \
                patch('studio_crowding_choices.audible_entry', return_value=audible):
            result = search_cues(self.session, 1, self.root / 'audio-cache', progress)
        return result, render

    def test_ranked_results_measure_every_same_length_draft_without_mutation(self):
        before = copy.deepcopy(self.session.to_dict())
        result, render = self.run_search()
        self.assertEqual([c['time'] for c in result['choices']], [72., 88.])
        self.assertTrue(all(c['before_seconds'] == 8 and c['seconds'] == 4 for c in result['choices']))
        self.assertEqual(result['choices'][0]['source_delta'], -8)
        self.assertEqual(result['choices'][0]['mix_delta'], 8)
        self.assertEqual(result['fingerprint'], choice_fingerprint(self.session, 1))
        self.assertEqual(render.call_count, len(cue_candidates(self.session, 1)) + 1)
        self.assertTrue(all(call.args[0].clips[1].blend_bars == 4 for call in render.call_args_list))
        self.assertEqual(self.session.to_dict(), before)

    def test_no_improvement_or_silent_entrance_is_not_offered(self):
        result, _ = self.run_search(measurement=lambda _: 8.)
        self.assertEqual(result['choices'], [])
        result, _ = self.run_search(audible=False)
        self.assertEqual(result['choices'], [])
        self.assertIn('limited search', result['reason'])
        result, _ = self.run_search(same_activity=True)
        self.assertEqual(result['choices'], [])  # A hard-threshold flip alone is insufficient.

    def test_worse_known_vocal_overlap_is_rejected_before_rendering(self):
        s = self.session
        s.tracks['a'].content = voice_content(s.tracks['a'], [(172., 180.)])
        s.tracks['b'].content = voice_content(s.tracks['b'], [(96., 104.)])
        _, render = self.run_search()
        starts = [call.args[0].clips[1].cue_in for call in render.call_args_list]
        self.assertNotIn(96., starts)

    def test_cancellation_and_source_replacement_do_not_return_choices(self):
        def cancel(message):
            if message.startswith('Entrance 2 of'): raise djmix.MixError('Operation cancelled.')
        with self.assertRaisesRegex(djmix.MixError, 'cancelled'): self.run_search(progress=cancel)
        def replace(message):
            if message == 'Entrance search complete':
                Path(self.session.tracks['a'].path).write_bytes(b'changed while searching')
        with self.assertRaisesRegex(djmix.MixError, 'source changed'): self.run_search(progress=replace)

    def test_entry_audibility_uses_overlap_only_and_stereo_power(self):
        s = self.session; positions = layout(s); start = positions[1]['start'] / djmix.SR - 2
        y = np.zeros((12 * djmix.SR, 2), dtype=np.float32); y[:2 * djmix.SR] = .1
        sf.write(self.root / 'incoming.wav', y, djmix.SR, subtype='FLOAT')
        report = {'start': start, 'folder': str(self.root)}
        self.assertFalse(audible_entry(s, 1, report))
        y[2 * djmix.SR:6 * djmix.SR] = [.05, -.05]
        sf.write(self.root / 'incoming.wav', y, djmix.SR, subtype='FLOAT')
        self.assertTrue(audible_entry(s, 1, report))


if __name__ == '__main__': unittest.main()
