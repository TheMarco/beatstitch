"""Focused arrangement, persistence, and real three-track export checks."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import djmix
from studio_engine import Clip, Session, Track, blend_advice, layout, preview_range, render_session, suggest_order


def project():
    tracks = {str(i):Track(str(i),f"{i}.wav",f"Song {i}",20,120,key,cue_out=20)
              for i,key in enumerate(("C minor","D minor","G minor"))}
    return Session(target_bpm=120,tracks=tracks,
                   clips=[Clip(identity,0,20,blend_bars=2) for identity in tracks])


class ArrangementTests(unittest.TestCase):
    def test_layout_accounts_for_tempo_and_each_overlap_once(self):
        session=project();session.target_bpm=128
        positions=layout(session)
        self.assertEqual([p['start']/djmix.SR for p in positions],[0,15,30])
        self.assertEqual(positions[-1]['end']/djmix.SR,48.75)
        start,end=preview_range(session,2)
        self.assertEqual((start/djmix.SR,end/djmix.SR),(22.5,41.25))
        session.clips[1].cue_out=7
        with self.assertRaisesRegex(djmix.MixError,'longer than'):
            layout(session)

    def test_save_roundtrip_and_optional_harmonic_controls(self):
        session=project();incoming=session.clips[1];incoming.semitones=-2
        self.assertEqual(incoming.shift,0)
        incoming.transpose=True
        self.assertIn('same key',blend_advice(session,*session.clips[:2])['assessment'])
        incoming.transpose=False;incoming.intro_percussive=True
        self.assertEqual(blend_advice(session,*session.clips[:2])['suggested_b_shifts'],[])
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.djmix';session.save(path)
            restored=Session.from_dict(json.loads(path.read_text()))
            self.assertEqual(restored.to_dict(),session.to_dict())
        invalid=session.to_dict();invalid['tracks']['0']['key']='garbage'
        with self.assertRaises(djmix.MixError):Session.from_dict(invalid)

    def test_order_is_a_nonmutating_proposal_with_no_pitch_changes(self):
        session=project();original=copy.deepcopy(session.to_dict())
        proposed=suggest_order(session)
        self.assertEqual([c.track_id for c in proposed],['0','2','1'])
        self.assertEqual(session.to_dict(),original)
        self.assertTrue(all(c.shift==0 for c in proposed))

    def test_three_track_audio_export_and_source_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);session=project();session.target_bpm=126
            for i,track in enumerate(session.tracks.values()):
                t=np.arange(8*djmix.SR)/djmix.SR
                tone=.1*np.sin(2*np.pi*(330+i*110)*t)
                raw=folder/f'{i}.raw';np.column_stack([tone,tone]).astype('<f4').tofile(raw)
                source=folder/f'{i}.wav';djmix.export_wav(raw,source,raw=True)
                track.path=str(source);track.duration=8
                session.clips[i].cue_out=8;session.clips[i].blend_bars=1
            session.clips[1].transpose=True;session.clips[1].semitones=-2
            original=Path(session.tracks['0'].path).read_bytes()
            with self.assertRaisesRegex(djmix.MixError,'source songs'):
                render_session(session,Path(session.tracks['0'].path))
            output=folder/'mix.wav';result=render_session(session,output)
            self.assertEqual(Path(session.tracks['0'].path).read_bytes(),original)
            self.assertAlmostEqual(result['duration'],layout(session)[-1]['end']/djmix.SR,places=5)
            self.assertLessEqual(result['true_peak_db'],-.99)
            print(f"\nThree-track WAV: {result['duration']:.6f}s, {result['true_peak_db']:.2f} dBTP")

    def test_append_reuses_two_tracks_and_preview_uses_the_requested_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);session=project();cache=folder/'cache'
            for i,track in enumerate(session.tracks.values()):
                t=np.arange(12*djmix.SR)/djmix.SR
                signal=.08*np.sin(2*np.pi*(330+i*110)*t)
                raw=folder/f'{i}.raw';np.column_stack([signal,signal]).astype('<f4').tofile(raw)
                source=folder/f'{i}.wav';djmix.export_wav(raw,source,raw=True)
                track.path=str(source);track.duration=12
                session.clips[i].cue_out=12;session.clips[i].blend_bars=1
            third=session.clips.pop()
            with patch('djmix.prepare',wraps=djmix.prepare) as prepare:
                first=render_session(session,folder/'first.wav',cache_dir=cache)
                self.assertEqual((first['reused_tracks'],prepare.call_count),(0,2))
                session.clips.append(third);prepare.reset_mock()
                second=render_session(session,folder/'second.wav',cache_dir=cache)
                self.assertEqual((second['reused_tracks'],prepare.call_count),(2,1))
                prepare.reset_mock();session.clips[1].bass_swap=False
                # Context around the second transition starts at 12s, after track one.
                region=preview_range(session,2)
                preview=render_session(session,folder/'preview.wav',cache_dir=cache,region=region)
                self.assertEqual((preview['reused_tracks'],prepare.call_count),(2,0))
                self.assertEqual(preview['start'],12)
                self.assertEqual(preview['duration'],18)
                prepare.reset_mock();third.transpose=True;third.semitones=2
                shifted=render_session(session,folder/'shifted.wav',cache_dir=cache)
                self.assertEqual((shifted['reused_tracks'],prepare.call_count),(2,1))
                prepare.reset_mock();third.transpose=False
                original=render_session(session,folder/'original.wav',cache_dir=cache)
                self.assertEqual((original['reused_tracks'],prepare.call_count),(3,0))
                prepare.reset_mock();source=Path(session.tracks['0'].path);stat=source.stat()
                os.utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000000))
                replaced=render_session(session,folder/'replaced.wav',cache_dir=cache)
                self.assertEqual((replaced['reused_tracks'],prepare.call_count),(2,1))
                prepare.reset_mock();session.target_bpm=126
                changed=render_session(session,folder/'tempo.wav',cache_dir=cache)
                self.assertEqual((changed['reused_tracks'],prepare.call_count),(0,3))
            print('\nCache: append 2 reused/1 processed; preview 2→3 reused both; pitch edit processed one; tempo edit processed all.')


if __name__=='__main__':unittest.main()
