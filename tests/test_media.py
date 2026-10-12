"""Source relocation verifies content and preserves the arrangement."""
import copy
from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf

import djmix
from studio_engine import Clip, Session, layout, load_track, render_session
from studio_media import prepare_relink
from studio_source_identity import fingerprint


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.cache = self.root / 'cache'; self.cache.mkdir()
        self.source = self.root / 'Song.wav'
        t = np.arange(12 * 8000) / 8000
        sf.write(self.source, .1 * np.sin(2 * np.pi * 440 * t), 8000)
        track = load_track(self.source, self.cache); track.bpm, track.key = 120, 'C minor'
        self.identity = track.id
        old = track.fingerprint['source']
        track.timing = {'version': 1, 'source': old, 'state': 'drift', 'map': [[0, 0], [6, 6.05], [12, 12]]}
        track.structure = {'version': 1, 'source': old, 'source_bpm': 120}
        track.beat_grid = {'version': 1, 'source': old, 'bpm': 120, 'anchor': 0}
        clip = Clip(track.id, 1, 11, stabilize=True, transpose=True, semitones=2, order_locked=True,
                    protected_moments=[{'start': 3, 'end': 8, 'label': 'Favorite', 'kind': 'favorite'}])
        self.session = Session(tracks={track.id: track}, clips=[clip])
        self.moved = self.root / 'moved/Song.wav'; self.moved.parent.mkdir()
        self.source.rename(self.moved)

    def test_moved_identical_audio_preserves_edits_and_rebinds_valid_analysis(self):
        before = copy.deepcopy(self.session.to_dict())
        result = prepare_relink(self.session, self.identity, self.moved, self.cache)
        track = result['draft'].tracks[self.identity]
        self.assertTrue(result['verified']); self.assertFalse(result['reset_correction'])
        self.assertEqual(self.session.to_dict(), before)
        self.assertEqual(result['draft'].clips, self.session.clips)
        self.assertEqual(track.path, str(self.moved))
        for value in (track.timing, track.structure, track.beat_grid): self.assertEqual(value['source'], track.fingerprint['source'])
        self.assertEqual(track.bpm, 120); self.assertEqual(track.key, 'C minor')

    def test_different_audio_with_same_name_and_duration_is_rejected(self):
        wrong = self.root / 'Song.wav'; sf.write(wrong, np.zeros(12 * 8000), 8000)
        with self.assertRaisesRegex(djmix.MixError, 'does not match'):
            prepare_relink(self.session, self.identity, wrong, self.cache)

    def test_legacy_relink_is_explicitly_unverified_and_clears_old_analysis(self):
        self.session.tracks[self.identity].fingerprint = {}
        result = prepare_relink(self.session, self.identity, self.moved, self.cache)
        self.assertFalse(result['verified']); self.assertEqual(result['reset_correction'], [self.session.clips[0].id])
        track = result['draft'].tracks[self.identity]
        self.assertEqual((track.timing, track.structure, track.beat_grid), ({}, {}, {}))
        expected = copy.deepcopy(self.session.clips); expected[0].stabilize = False
        self.assertEqual(result['draft'].clips, expected)

    def test_stale_annotations_are_not_revalidated_by_relink(self):
        self.session.tracks[self.identity].structure['source'] = ['old version', 0, 0]
        result = prepare_relink(self.session, self.identity, self.moved, self.cache)
        self.assertEqual(result['draft'].tracks[self.identity].structure['source'], ['old version', 0, 0])

    def test_wrong_duration_and_interrupted_hash_leave_current_project_unchanged(self):
        self.session.tracks[self.identity].duration = 12.2
        before = copy.deepcopy(self.session.to_dict())
        with self.assertRaisesRegex(djmix.MixError, 'same recording'):
            prepare_relink(self.session, self.identity, self.moved, self.cache)
        def cancel(message): raise djmix.MixError('Operation cancelled.')
        with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
            fingerprint(self.moved, cancel)
        self.assertEqual(self.session.to_dict(), before)

    def test_relinked_pitch_and_stabilization_render_with_protected_passage(self):
        draft = prepare_relink(self.session, self.identity, self.moved, self.cache)['draft']
        output = self.root / 'relinked.wav'
        result = render_session(draft, output)
        self.assertAlmostEqual(result['duration'], layout(draft)[-1]['end'] / djmix.SR, places=5)
        data, rate = sf.read(output); self.assertTrue(np.isfinite(data).all()); self.assertEqual(rate, djmix.SR)
        self.assertLessEqual(result['true_peak_db'], -.99)


if __name__ == '__main__': unittest.main()
