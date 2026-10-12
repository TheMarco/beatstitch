import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import djmix
from studio_engine import Clip, Session, Track, layout, overlap_limit
from studio_order import OrderOptions, draft_for_order, propose_order
from studio_protection import fit_protected_overlaps, protection_limits
from studio_render_cache import audio_recipe


def project():
    tracks = {str(i): Track(str(i), f'{i}.wav', f'Song {i}', 120, 120, 'C minor', cue_out=120) for i in range(3)}
    return Session(target_bpm=120, tracks=tracks, clips=[Clip(k, 0, 120, blend_bars=4) for k in tracks])

def protect(clip, start, end, name='Vocal'):
    clip.protected_moments.append({'start': start, 'end': end, 'label': name, 'kind': 'vocal'})


class ProtectionTests(unittest.TestCase):
    def test_kept_range_and_both_overlaps_are_enforced_at_sample_boundaries(self):
        session = project(); clip = session.clips[1]
        protect(clip, 8, 112)
        layout(session)  # A protected passage may meet a blend exactly at the edge.
        self.assertEqual(overlap_limit(session, 1)['bars'], 4)
        self.assertEqual(overlap_limit(session, 2)['bars'], 4)
        clip.cue_in = 9
        with self.assertRaisesRegex(djmix.MixError, 'whole protected passage'):
            layout(session)
        clip.cue_in = 0; clip.blend_bars = 5
        with self.assertRaisesRegex(djmix.MixError, 'Blend 1 → 2 covers protected'):
            layout(session)
        clip.blend_bars = 4; session.clips[2].blend_bars = 5
        with self.assertRaisesRegex(djmix.MixError, 'Blend 2 → 3 covers protected'):
            layout(session)

    def test_fit_only_shortens_adjacent_blends_and_does_not_mutate(self):
        session = project(); protect(session.clips[1], 5, 115)
        before = copy.deepcopy(session.to_dict())
        draft, changes = fit_protected_overlaps(session, 1)
        self.assertEqual([c.blend_bars for c in draft.clips], [4, 2, 2])
        self.assertEqual(len(changes), 2); self.assertEqual(session.to_dict(), before)
        layout(draft)
        session.clips[1].protected_moments[0]['start'] = .1
        with self.assertRaisesRegex(djmix.MixError, 'no room for even one bar'):
            fit_protected_overlaps(session, 1)

    def test_tempo_and_stabilization_use_processed_frame_positions(self):
        session = project(); session.target_bpm = 127.3; clip = session.clips[1]
        protect(clip, 10, 110)
        # A nonlinear source-time map must affect the protection, just as it affects audio.
        with patch('studio_protection.corrected_time', side_effect=lambda track, c, t: t * .8 if t < 60 else t - 12):
            limits = protection_limits(session.tracks['1'], clip, session.target_bpm)
        self.assertEqual(limits['incoming'][0], round(8 * 120 / 127.3 * djmix.SR))
        self.assertEqual(limits['outgoing'][0], round(108 * 120 / 127.3 * djmix.SR) - round(98 * 120 / 127.3 * djmix.SR))

    def test_touching_bar_boundaries_survive_fractional_sample_rounding(self):
        session = project(); session.target_bpm = 126
        for clip in session.clips: clip.cue_out = 8; clip.blend_bars = 1
        protect(session.clips[1], 2, 6)
        layout(session)
        self.assertEqual(overlap_limit(session, 2)['bars'], 1)
        session.clips[1].protected_moments[0]['end'] = 6.001
        with self.assertRaisesRegex(djmix.MixError, 'covers protected'): layout(session)

    def test_order_planner_keeps_protected_songs_and_avoids_new_overlaps(self):
        session = project(); protect(session.clips[0], 0, 4, 'Opening vocal')
        protect(session.clips[2], 116, 120, 'Closing drop')
        options = OrderOptions(opener='', target_seconds=120, allow_omissions=True, auto_energy=False)
        draft = propose_order(session, options)['draft']
        self.assertEqual([c.id for c in draft.clips], [session.clips[0].id, session.clips[2].id])
        layout(draft)
        with self.assertRaisesRegex(djmix.MixError, 'protected passages must stay'):
            draft_for_order(session, [session.clips[0].id, session.clips[1].id], options)
        options.opener = session.clips[2].id
        with self.assertRaisesRegex(djmix.MixError, 'No valid order'):
            propose_order(session, options)

    def test_protection_saves_and_loads_without_changing_audio_recipe(self):
        session = project()
        with tempfile.TemporaryDirectory() as directory:
            for track in session.tracks.values():
                path = Path(directory) / track.path; path.write_bytes(b'fixture'); track.path = str(path)
            with patch('studio_render_cache.engine_signature', return_value='fixture'):
                before = audio_recipe(session)
                protect(session.clips[1], 20, 100)
                self.assertEqual(audio_recipe(session), before)
            restored = Session.from_dict(session.to_dict())
            self.assertEqual(restored.to_dict(), session.to_dict())
            old = session.to_dict(); old['version'] = 3
            for clip in old['clips']: clip.pop('protected_moments')
            self.assertTrue(all(not c.protected_moments for c in Session.from_dict(old).clips))

    def test_invalid_protection_metadata_is_rejected(self):
        for start, end in ((True, 10), (0, float('nan')), (10, 5), (-1, 10), (100, 130)):
            session = project(); protect(session.clips[1], start, end)
            with self.subTest(start=start, end=end), self.assertRaises(djmix.MixError): layout(session)


if __name__ == '__main__': unittest.main()
