"""Timing evidence, migration, real DSP and cache/preview consistency."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import djmix
from studio_engine import Session, Track, Clip, layout, render_session
from studio_timing import VERSION, summarize, fit_grid, signature, mapping, prepare, corrected_time


def observations(beats):
    windows=[]
    for start in np.arange(0, beats[-1]-16, 6):
        selected=beats[(beats>start+.12)&(beats<start+15.88)]
        if len(selected)>24:
            slope=np.polyfit(np.arange(len(selected)),selected,1)[0]
            windows.append({'quality':1.,'bpm':60/slope,'beats':selected.tolist()})
    return windows


def fixture(folder):
    rate=djmix.SR;duration=46
    n=np.arange(duration*rate);t=n/rate
    audio=.015*np.sin(2*np.pi*440*t)
    numbers=np.arange(81);beats=2+numbers*.5+.065*np.sin(numbers/13)
    pulse=np.arange(round(.05*rate))/rate
    kick=.3*np.sin(2*np.pi*80*pulse)*np.exp(-pulse*80)
    for beat in beats:
        pos=round(beat*rate);audio[pos:pos+len(kick)]+=kick
    source=folder/'source.wav';sf.write(source,np.column_stack((audio,audio)),rate,subtype='FLOAT')
    desired=2+numbers*.5
    points=np.column_stack((np.r_[0,beats,duration],np.r_[0,desired,duration])).tolist()
    timing={'version':VERSION,'source':signature(source),'bpm':120.,'state':'drift',
            'map':points,'coverage':.8,'regions':[{'start':beats[0],'end':beats[-1],
             'beats':beats.tolist(),'grid':desired.tolist(),'p95_ms':60.}]}
    track=Track('a',str(source),'Drifty test',duration,120.,'A minor',timing=timing)
    clip=Clip('a',1,44,stabilize=True)
    return Session(target_bpm=126.,tracks={'a':track},clips=[clip]),beats,desired


class TimingTests(unittest.TestCase):
    def test_whole_song_bpm_fixes_short_estimate_without_warping_stable_song(self):
        beats=1+np.arange(380)*60/125
        result=summarize(observations(beats),190)
        self.assertEqual(result['bpm'],125)
        self.assertEqual(result['state'],'steady')
        self.assertEqual(result['map'],[])
        self.assertEqual(summarize([],190)['state'],'uncertain')
        self.assertIsNone(fit_grid(np.zeros(16000),125))

    def test_drift_maps_are_monotonic_and_sparse_gaps_are_not_filled_with_beats(self):
        numbers=np.arange(240);beats=2+numbers*.5+.08*np.sin(numbers/24)
        beats=beats[(beats<45)|(beats>65)]
        result=summarize(observations(beats),125)
        self.assertEqual(result['state'],'drift')
        points=np.asarray(result['map'])
        self.assertTrue(np.all(np.diff(points,axis=0)>0))
        self.assertAlmostEqual(np.interp(55,points[:,0],points[:,1]),55)
        self.assertTrue(all(not (r['start']<55<r['end']) for r in result['regions']))

    def test_legacy_projects_default_off_and_invalid_maps_are_rejected(self):
        s=Session(tracks={'a':Track('a','missing.wav','Original',20,120)},clips=[Clip('a',0,20)])
        old=s.to_dict();old['version']=1;del old['tracks']['a']['timing'];del old['clips'][0]['stabilize']
        loaded=Session.from_dict(old)
        self.assertFalse(loaded.clips[0].stabilize)
        self.assertEqual(layout(loaded),layout(s))
        loaded.clips[0].stabilize=True
        with self.assertRaisesRegex(djmix.MixError,'Analyze timing'):layout(loaded)
        loaded.tracks['a'].timing={'version':VERSION,'state':'drift','map':[[0,0],[20,-1]]}
        with self.assertRaisesRegex(djmix.MixError,'Invalid timing'):layout(loaded)

    def test_real_warp_tempo_pitch_nonzero_cues_and_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);session,beats,desired=fixture(folder)
            track=session.tracks['a'];clip=session.clips[0];cache=folder/'cache'
            source_before=Path(track.path).read_bytes()
            clip.transpose=True;clip.semitones=2
            raw=prepare(track,clip,126,folder,'pitched',0)
            audio=np.memmap(raw,dtype='<f4',mode='r',shape=(raw.stat().st_size//8,2))
            self.assertEqual(len(audio),layout(session)[0]['length'])
            # Exclude kicks: stable pitched tone between attacks.
            segment=audio[round(10.25*djmix.SR):round(10.4*djmix.SR),0].astype(float)
            spectrum=abs(np.fft.rfft(segment*np.hanning(len(segment)),n=262144))
            freq=np.argmax(spectrum)*djmix.SR/262144
            self.assertAlmostEqual(freq,440*2**(2/12),delta=2)
            # Strong low-frequency transients should land near their mapped grid.
            from scipy.signal import butter,sosfiltfilt
            from scipy.ndimage import uniform_filter1d
            low=sosfiltfilt(butter(3,180,fs=djmix.SR,output='sos'),audio[:,0])
            envelope=uniform_filter1d(low*low,240)
            errors=[]
            for target in desired[4:-4]:
                expected=(target-corrected_time(track,clip,clip.cue_in))*120/126
                pos=round(expected*djmix.SR);width=round(.09*djmix.SR)
                found=pos-width+np.argmax(envelope[pos-width:pos+width])
                errors.append(abs(found/djmix.SR-expected))
            self.assertLess(np.percentile(errors,95),.03)
            audio._mmap.close()
            clip.transpose=False
            with patch('studio_timing.prepare',wraps=prepare) as process:
                full=render_session(session,folder/'full.wav',cache_dir=cache)
                self.assertEqual(process.call_count,1)
                region=(5*djmix.SR,12*djmix.SR)
                preview=render_session(session,folder/'preview.wav',region=region,cache_dir=cache)
                self.assertEqual(process.call_count,1)
                self.assertEqual(preview['reused_tracks'],1)
                a,_=sf.read(folder/'full.wav');b,_=sf.read(folder/'preview.wav')
                np.testing.assert_allclose(a[region[0]+480:region[1]-480],b[480:-480],atol=3e-7)
                clip.stabilize=False
                off=render_session(session,folder/'off.wav',cache_dir=cache)
                self.assertEqual(off['processed_tracks'],1)
                clip.stabilize=True
                again=render_session(session,folder/'again.wav',cache_dir=cache)
                self.assertEqual(again['reused_tracks'],1)
                self.assertEqual(process.call_count,1)
            self.assertEqual(Path(track.path).read_bytes(),source_before)
            # Continuous audition is built from the same renderer and cached too.
            from studio_audition import prepare_comparison
            report=prepare_comparison(session,clip,cache)
            paths=[Path(report['folder'])/f'{v}.wav' for v in ('fixed','straightened','fixed-click')]
            self.assertEqual(len({sf.info(p).frames for p in paths}),1)
            self.assertAlmostEqual(djmix.meter(paths[0])['lufs'],djmix.meter(paths[1])['lufs'],delta=.05)
            with patch('studio_audition.render_session') as renderer:
                self.assertEqual(prepare_comparison(session,clip,cache),report)
                renderer.assert_not_called()
            # Even a same-duration replacement cannot reuse a stale correction map.
            import os
            stat=Path(track.path).stat();os.utime(track.path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1_000_000))
            with self.assertRaisesRegex(djmix.MixError,'Analyze its timing again'):
                render_session(session,folder/'stale.wav',cache_dir=cache)
            print(f'\nWarp: pitch {freq:.2f} Hz; landmark p95 {np.percentile(errors,95)*1000:.1f} ms; preview equals export.')


if __name__=='__main__':unittest.main()
