import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import djmix
from studio_beat_grid import grid_settings
from studio_cache import source_signature
from studio_drum_loops import JOIN_SECONDS, repeat_audio
from studio_engine import Clip, Session, Track, layout
from studio_loop_extension import choices_for_clip, draft_with_copy, extension_plan, prepare_extension, publish_extension, _write_extended
from studio_loop_comparison import comparison_regions, prepare_comparison
from studio_media import create_portable_copy
from studio_process import cancel_on
from studio_projects import read_project
from studio_storage import StorageManager


class LoopExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'drums.wav'
        t = np.arange(48 * djmix.SR) / djmix.SR; phase = t % .5
        x = (.3 * np.sin(2 * np.pi * 90 * phase) * np.exp(-phase * 30)).astype(np.float32)
        self.audio = np.column_stack([x, -x]); sf.write(self.source, self.audio, djmix.SR, subtype='FLOAT')
        self.track = Track('drums', str(self.source), 'Test drums', 48., bpm=120., key='A minor', cue_out=48.)
        self.track.beat_grid = grid_settings(self.track, 120., 0.)
        self.session = Session(target_bpm=120., tracks={'drums': self.track},
                               clips=[Clip('drums', 0, 48, blend_bars=2) for _ in range(3)], collections={'Favorites': ['drums']})
        self.cache = self.root / 'audio-cache'

    def choice(self, side='intro'): return choices_for_clip(self.session, 1, 2, side)[0]

    def test_streaming_copy_matches_audition_and_preserves_stereo_and_source(self):
        a, b, repeats = 2 * djmix.SR, 6 * djmix.SR, 2
        raw, smooth, _ = repeat_audio(self.audio, a, b, repeats, round(JOIN_SECONDS * djmix.SR))
        for enabled, expected in ((False, raw), (True, smooth)):
            dest = self.root / f'{enabled}.wav'
            _write_extended(self.source, dest, a, b, repeats, enabled, lambda _: None)
            actual, _ = sf.read(dest, dtype='float32', always_2d=True)
            np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(sf.read(self.source, dtype='float32', always_2d=True)[0], self.audio)

    def test_protected_passages_cues_neighbors_and_baked_timing(self):
        clip = self.session.clips[1]; choice = self.choice()
        clip.protected_moments = [{'start': 20., 'end': 24., 'label': 'Keep drop', 'kind': 'drop'}]
        clip.stabilize = True
        self.track.timing = {'version': 1, 'state': 'drift', 'source': source_signature(self.source),
                             'map': [[0, 0], [12, 12.1], [36, 36.1], [48, 48]]}
        before = copy.deepcopy(self.session.to_dict())
        plan = extension_plan(self.session, 1, choice, 2, overlap=4)
        extra = plan['added_frames'] / djmix.SR
        replacement = Track('copy', str(self.source), 'Copy', 48 + extra, bpm=120., cue_out=48 + extra)
        draft = draft_with_copy(self.session, 1, plan, replacement)
        self.assertTrue(plan['baked_timing']); self.assertFalse(draft.clips[1].stabilize)
        self.assertEqual(draft.clips[1].id, clip.id)
        self.assertEqual(draft.clips[1].cue_out, 48 + extra)
        self.assertAlmostEqual(draft.clips[1].protected_moments[0]['start'], 20.1 + extra)
        self.assertEqual(draft.clips[0], self.session.clips[0]); self.assertEqual(draft.clips[2], self.session.clips[2])
        self.assertEqual(draft.collections['Favorites'], ['drums', 'copy'])
        self.assertEqual(self.session.to_dict(), before)
        choice = choices_for_clip(self.session, 1, 2, 'intro')[-1]
        clip.protected_moments = [{'start': choice['start'], 'end': choice['end'], 'label': 'Do not repeat', 'kind': 'passage'}]
        self.assertNotIn(choice, choices_for_clip(self.session, 1, 2, 'intro'))
        with self.assertRaisesRegex(djmix.MixError, 'protected'): extension_plan(self.session, 1, choice, 1)

    @unittest.skipUnless(djmix.tool_path('ffmpeg'), 'FFmpeg required')
    def test_cache_copy_roundtrip_portability_and_collision_safety(self):
        choice = self.choice(); before = copy.deepcopy(self.session.to_dict())
        result = prepare_extension(self.session, 1, choice, 2, self.cache)
        self.assertTrue(max(result['copy'].waveform) > .9)
        self.assertEqual(result['copy'].duration, 56.)
        self.assertFalse(result['copy'].timing or result['copy'].beat_grid or result['copy'].content)
        with patch('studio_loop_extension.djmix.ffmpeg', side_effect=AssertionError('Unnecessary decoding')):
            second = prepare_extension(self.session, 1, choice, 2, self.cache, overlap=4)
        self.assertEqual(second['copy'].path, result['copy'].path)
        track = publish_extension(result, self.root / 'media' / 'loops')
        draft = draft_with_copy(self.session, 1, result['plan'], track)
        reopened = Session.from_dict(json.loads(json.dumps(draft.to_dict())))
        self.assertEqual(reopened.to_dict(), draft.to_dict())
        portable = create_portable_copy(draft, self.root / 'portable')
        moved = self.root / 'moved'; (self.root / 'portable').rename(moved)
        loaded, _ = read_project(moved / Path(portable['project']).name)
        self.assertTrue(Path(loaded.tracks[track.id].path).is_file())
        manager = StorageManager(self.root)
        with patch.object(manager, 'limit', return_value=0): manager.prune()
        self.assertTrue(Path(track.path).exists()); self.assertTrue(self.source.exists())
        result = prepare_extension(self.session, 1, choice, 2, self.cache)
        Path(track.path).write_bytes(b'externally changed')
        with self.assertRaisesRegex(djmix.MixError, 'will not be overwritten'): publish_extension(result, self.root / 'media' / 'loops')
        self.assertEqual(Path(track.path).read_bytes(), b'externally changed')
        self.assertEqual(self.session.to_dict(), before)

    @unittest.skipUnless(djmix.tool_path('ffmpeg'), 'FFmpeg required')
    def test_current_draft_actual_render_both_contexts_and_reuse(self):
        result = prepare_extension(self.session, 1, self.choice('outro'), 1, self.cache)
        bounds = comparison_regions(self.session, result, 'blend')
        self.assertNotEqual(bounds['regions'][0], bounds['regions'][1])
        for context in ('passage', 'blend'):
            report = prepare_comparison(self.session, result, self.cache, context=context)
            for name in ('current', 'draft'):
                info = sf.info(Path(report['folder']) / f'{name}.wav')
                self.assertEqual(info.frames, report['frames']); self.assertEqual(info.channels, 2)
                self.assertGreater(max(report[name + '_wave']), 0)
            with patch('studio_loop_comparison.prepare_listening', side_effect=AssertionError('Unnecessary render')):
                self.assertEqual(prepare_comparison(self.session, result, self.cache, context=context)['folder'], report['folder'])

    @unittest.skipUnless(djmix.tool_path('ffmpeg'), 'FFmpeg required')
    def test_cancellation_and_source_change_publish_no_copy(self):
        stop = threading.Event(); stop.set()
        with cancel_on(stop), self.assertRaisesRegex(djmix.MixError, 'cancelled'):
            prepare_extension(self.session, 1, self.choice(), 1, self.cache)
        def mutate(message):
            if message.startswith('Adding loop'):
                os.utime(self.source, ns=(self.source.stat().st_atime_ns, self.source.stat().st_mtime_ns + 1000))
        with self.assertRaisesRegex(djmix.MixError, 'source changed'):
            prepare_extension(self.session, 1, self.choice(), 1, self.cache, mutate)
        self.assertFalse(list(self.cache.rglob('extended.wav')))

    def test_opening_or_ending_comparison_keeps_new_audio_after_current_end(self):
        self.session.clips = [self.session.clips[1]]
        choice = choices_for_clip(self.session, 0, 2, 'outro')[0]
        plan = extension_plan(self.session, 0, choice, 3)
        track = Track('copy', str(self.source), 'Copy', 60., bpm=120., cue_out=60.)
        draft = draft_with_copy(self.session, 0, plan, track)
        bounds = comparison_regions(self.session, {'draft': draft, 'index': 0, 'plan': plan})
        self.assertEqual(bounds['regions'][0][1], layout(self.session)[-1]['end'])
        self.assertGreater(bounds['frames'], bounds['regions'][0][1] - bounds['regions'][0][0])
        self.assertEqual(len(bounds['join_times']), 3)

    @unittest.skipUnless(djmix.tool_path('rubberband'), 'Rubber Band required')
    def test_accepted_timing_is_baked_into_audio_once_without_baking_pitch(self):
        from studio_timing import prepare
        self.track.timing = {'version': 1, 'state': 'drift', 'source': source_signature(self.source),
                             'map': [[0, 0], [12, 12.1], [36, 36.1], [48, 48]]}
        clip = self.session.clips[1]; clip.stabilize = True; clip.transpose = True; clip.semitones = 2
        reference = self.root / 'reference'; reference.mkdir()
        raw = prepare(self.track, Clip('drums', 0, 48, stabilize=True), 120, reference, 'timing', 0.)
        corrected = np.fromfile(raw, dtype='<f4').reshape(-1, 2)
        result = prepare_extension(self.session, 1, self.choice(), 1, self.cache)
        plan = result['plan']
        _, expected, _ = repeat_audio(corrected, plan['start_frame'], plan['end_frame'], 1, round(JOIN_SECONDS * djmix.SR))
        actual, _ = sf.read(result['copy'].path, dtype='float32', always_2d=True)
        np.testing.assert_array_equal(actual, expected)
        self.assertFalse(result['draft'].clips[1].stabilize)
        self.assertEqual(result['draft'].clips[1].shift, 2)


if __name__ == '__main__': unittest.main()
