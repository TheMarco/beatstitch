"""Freeze controlled audio, explicit construction references and experiment scope."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import tempfile
import zipfile

import numpy as np
import soundfile as sf

import djmix
from benchmark_content import catalog, intersection, parse_labels
from detector_pilot import load_pilot, child, verify_source, atomic_json, file_hash, digest, utc_now
from detector_experiment_common import RATE, load_plan


def _write(path, audio):
    if not np.isfinite(audio).all(): raise ValueError('Invalid constructed audio.')
    peak = float(np.max(np.abs(audio)))
    if peak > .9: audio = audio * (.9 / peak)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, audio.astype(np.float32), RATE, subtype='FLOAT')


def _record(path, identity, name, kind, expected, provenance):
    info = sf.info(path)
    return {'id': identity, 'name': name, 'kind': kind, 'audio': str(path.resolve()),
            'audio_sha256': file_hash(path), 'frames': info.frames,
            'expected': expected, 'provenance': provenance}


def voice_excerpt(source, destination, start, folder):
    """Slice the decoded sample clock; codec seek/rounding is not a label."""
    decoded=Path(folder)/'decoded-reference.wav'
    djmix.ffmpeg('-i',str(source),'-ar',str(RATE),'-ac','2','-c:a','pcm_f32le',str(decoded))
    first=round(start*RATE); count=10*RATE
    with sf.SoundFile(decoded) as stream:
        if first+count>stream.frames:
            raise ValueError('Annotated reference extends beyond the decoded source audio.')
        stream.seek(first); audio=stream.read(count,dtype='float32',always_2d=True)
    if len(audio)!=count or not np.isfinite(audio).all():
        raise ValueError('Incomplete or invalid fully annotated vocal excerpt.')
    sf.write(destination,audio,RATE,subtype='FLOAT')


def synth(path, root=0, mode='major', bpm=128., phase=.173, pattern='straight', drift=False, pitched=True):
    """Original oscillator/noise construction with scheduled quarter notes.

    Composition labels and scheduled beats are construction references, not a
    claim that every human must perceive the same key or bar interpretation.
    """
    rng = np.random.default_rng(24000 + root + (mode == 'minor') * 100)
    beats = [phase]
    for i in range(64):
        speed = 116. + 16 * i / 63 if drift else bpm
        beats.append(beats[-1] + 60 / speed)
    duration = beats[-1] + .18
    count = math.ceil(duration * RATE); t = np.arange(count) / RATE
    audio = np.zeros(count)
    for i, start in enumerate(beats[:-1]):
        left = round(start * RATE); length = min(round(.23 * RATE), count - left)
        u = np.arange(length) / RATE
        amplitude = .42 if i % 4 == 0 else .26
        if pattern == 'offbeat_accent': amplitude = .42 if i % 4 == 2 else .26
        if pattern == 'noise_drums':
            # A low noise burst has no fixed musical note or harmonic stack.
            noise = rng.normal(0, 1, length)
            kernel = np.ones(41) / 41
            kick = np.convolve(noise, kernel, mode='same') * np.exp(-u / .045) * 3
        else:
            phase_wave = 2*np.pi*(48*u + 70*.024*(1-np.exp(-u/.024)))
            kick = np.sin(phase_wave) * np.exp(-u/.052)
        audio[left:left+length] += amplitude * kick
        if i % 4 in (1, 3): audio[left:left+length] += .055*rng.normal(0, 1, length)*np.exp(-u/.035)
        audio[left:left+length] += .022*rng.normal(0, 1, length)*np.exp(-u/.009)
        half = round((start + (beats[i+1]-start)/2) * RATE)
        n = min(round(.035 * RATE), count-half)
        if n > 0: audio[half:half+n] += .018*rng.normal(0, 1, n)*np.exp(-np.arange(n)/RATE/.007)
    if pitched:
        tonic = 48 + root
        thirds = (4, 4, 4, 4) if mode == 'major' else (3, 3, 4, 3)
        roots = (0, 5, 7, 0)
        for bar in range(16):
            a, b = round(beats[bar*4]*RATE), round(beats[(bar+1)*4]*RATE)
            u = np.arange(b-a)/RATE
            env = np.minimum(1, u/.018) * np.minimum(1, np.maximum(0,(b-a)/RATE-u)/.025)
            degree = roots[bar % 4]; chord = (degree, degree+thirds[bar % 4], degree+7)
            for note in chord:
                f = 440*2**((tonic+note-69)/12)
                tone = np.sin(2*np.pi*f*u) + .3*np.sin(2*np.pi*2*f*u) + .12*np.sin(2*np.pi*3*f*u)
                audio[a:b] += .045*tone*env
            bass = 440*2**((tonic+degree-12-69)/12)
            audio[a:b] += .10*np.sin(2*np.pi*bass*u)*env
        intervals = (0,2,4,7,9,7,4,2) if mode == 'major' else (0,2,3,7,8,7,3,2)
        for i, start in enumerate(beats[:-1]):
            a=round((start+.05)*RATE); n=min(round(.18*RATE),count-a)
            u=np.arange(n)/RATE; f=440*2**((tonic+12+intervals[i%8]-69)/12)
            audio[a:a+n] += .035*np.sin(2*np.pi*f*u)*np.exp(-u/.12)
    # Identical channels make channel-polarity stress an exact known cancellation
    # in a naive mono sum, while each original channel retains the composition.
    stereo = np.column_stack((audio, audio))
    edge = np.minimum(np.clip(t/.005,0,1),np.clip((duration-t)/.015,0,1))
    _write(path, stereo * edge[:,None])
    expected = {'beats': beats[:-1], 'downbeats': beats[:-1:4], 'bpm': None if drift else bpm,
                'voice': False, 'voice_scope': 'construction_has_no_voice_samples',
                'key': djmix.key_name((root,mode)) if pitched else None,
                'pitched': True if pitched else None, 'drums_only': False if pitched else None}
    if pattern == 'noise_drums' and not pitched:
        expected.update(pitched=False, drums_only=True)
    return expected


def _cases():
    all_kinds = ['real','synthetic_tonal','synthetic_timing','content_reference','silence','noise']
    tonal = ['real','synthetic_tonal']; rhythmic = ['real','synthetic_tonal','synthetic_timing']
    content = ['real','synthetic_tonal','synthetic_timing','content_reference','silence','noise']
    cases = [{'id':'baseline','type':'baseline','families':['key','timing','content'],'record_kinds':all_kinds}]
    for shift in (-2,2,5,12):
        cases.append({'id':f'pitch_{shift:+d}', 'type':'pitch','semitones':shift,
                      'families':['key','timing','content'],'record_kinds':tonal})
    for speed in (.95,1.05):
        cases.append({'id':f'tempo_{speed:.2f}','type':'tempo','speed':speed,
                      'families':['key','timing','content'],'record_kinds':rhythmic})
    cases.extend([
        {'id':'gain_minus12','type':'gain','gain_db':-12.,'families':['key','timing','content'],'record_kinds':content},
        {'id':'swap_channels','type':'swap','families':['key','timing','content'],'record_kinds':content},
        {'id':'right_polarity','type':'polarity','families':['key','timing','content'],'record_kinds':content},
        {'id':'prefix_037','type':'prefix','offset':.37,'families':['timing'],'record_kinds':rhythmic},
        {'id':'crop_125','type':'crop','offset':1.25,'families':['timing'],'record_kinds':rhythmic}])
    return cases


def prepare(pilot_path, output, jamendo=None, limit=None):
    pilot_path, output = Path(pilot_path).resolve(), Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    plan_path = output/'plan.json'
    if plan_path.exists():
        load_plan(plan_path); return plan_path
    pilot = load_pilot(pilot_path); records=[]
    selected = pilot['records'][:limit] if limit else pilot['records']
    for source in selected:
        verify_source(source, full=True)
        path = output/'baselines'/f'real-{source["id"]}.wav'
        with sf.SoundFile(child(pilot_path,source['audio'])) as stream:
            audio = stream.read(min(stream.frames,60*RATE),dtype='float32',always_2d=True)
        path.parent.mkdir(parents=True,exist_ok=True)
        sf.write(path,audio,RATE,subtype='FLOAT')
        record = _record(path,'real-'+source['id'],source['name'],'real',{},
                         {'pilot_record_id':source['id'],'source':source['source'],
                          'source_sha256':source['source_sha256'],'scope':'first 60 seconds of the decoded source; no gain normalization or limiting'})
        records.append(record)
    for mode in ('major','minor'):
        for root in range(12):
            name=f'tonal-{root}-{mode}'; path=output/'baselines'/f'{name}.wav'
            expected=synth(path,root,mode)
            records.append(_record(path,name,djmix.key_name((root,mode))+' construction','synthetic_tonal',expected,
                                   {'generator':'original oscillator/noise composition','reference':'scheduled note/chord and beat metadata','scope':'construction only'}))
    for bpm in (96.,127.5,160.):
        for pattern in ('straight','offbeat_accent','noise_drums'):
            name=f'timing-{bpm:g}-{pattern}'; path=output/'baselines'/f'{name}.wav'
            expected=synth(path,bpm=bpm,pattern=pattern,pitched=False)
            records.append(_record(path,name,name,'synthetic_timing',expected,{'reference':'scheduled quarter notes and bar starts','generator':pattern}))
    for pattern in ('straight','offbeat_accent'):
        name='timing-drift-'+pattern; path=output/'baselines'/f'{name}.wav'
        expected=synth(path,pattern=pattern,drift=True,pitched=True)
        records.append(_record(path,name,name,'synthetic_timing',expected,{'reference':'scheduled beats with 116-to-132 BPM drift','generator':pattern}))
    for kind in ('silence','noise'):
        path=output/'baselines'/f'{kind}.wav'
        audio=np.zeros((10*RATE,2),dtype=np.float32)
        if kind=='noise': audio=np.random.default_rng(1901).normal(0,.035,audio.shape).astype(np.float32)
        _write(path,audio)
        records.append(_record(path,kind,kind,kind,{'voice':False,'beats':[],'downbeats':[],
                          'key':None,'pitched':False,'drums_only':False,'voice_scope':'construction_has_no_voice_samples'},
                          {'reference':'no voice or musical-note samples in construction'}))
    if jamendo:
        jamendo=Path(jamendo).resolve(); archive_hash=file_hash(jamendo)
        choices={'positive':[],'negative':[]}; used=set()
        with zipfile.ZipFile(jamendo) as archive:
            entries=sorted(catalog(archive),key=lambda r:r['name'])
            for category in ('positive','negative'):
                for source in entries:
                    if source['name'] in used: continue
                    text=archive.read(source['labels']).decode(); labels=parse_labels(text)
                    for start in range(0, min(240,math.floor(labels[-1][1]-10))+1,5):
                        fully=intersection(start,start+10,[(a,b) for a,b,_ in labels])
                        singing=intersection(start,start+10,[(a,b) for a,b,v in labels if v])
                        if abs(fully-10)>1e-6 or not ((category=='positive' and singing>=8) or (category=='negative' and singing==0)): continue
                        choices[category].append((source,start,text)); used.add(source['name']); break
                    if len(choices[category])==8: break
            if any(len(rows)!=8 for rows in choices.values()): raise ValueError('Insufficient fully annotated training-only voice controls.')
            for category, selected_rows in choices.items():
                for index,(source,start,text) in enumerate(selected_rows):
                    name=f'jamendo-{category}-{index}'; path=output/'baselines'/f'{name}.wav'
                    with tempfile.TemporaryDirectory(dir=output,prefix='.voice-control-') as folder:
                        raw=Path(folder)/Path(source['name']).name; raw.write_bytes(archive.read(source['audio']))
                        voice_excerpt(raw,path,start,folder)
                    info=sf.info(path)
                    if info.frames!=10*RATE: raise ValueError('Voice reference is shorter than its fully annotated ten seconds.')
                    records.append(_record(path,name,source['name']+' '+category,'content_reference',
                         {'voice':category=='positive','voice_scope':'entire_window_any_singing'},
                         {'archive':str(jamendo),'archive_sha256':archive_hash,'audio_member':source['audio'],
                          'labels_member':source['labels'],'labels_sha256':source['labels_sha256'],
                          'annotation_text':text,'source_start':start,'source_end':start+10,
                          'official_partition':'train','purpose':'development controls; previously observed corpus, not fresh held-out validation'}))
    code=['detector_experiment_common.py','prepare_detector_experiments.py','studio_key.py','studio_key_worker.py',
          'studio_key_model.py','studio_structure.py','studio_structure_features.py','studio_beat_tempo.py',
          'studio_content.py','studio_content_model.py','studio_overlap_harmony.py','analyze_audio.py']
    root=Path(__file__).resolve().parent
    plan={'version':1,'project_root':str(root),'pilot':str(pilot_path),'pilot_plan_sha256':pilot['plan_sha256'],
          'records':records,'cases':_cases(),'audio_rate':RATE,'purpose':'development_controls_and_consistency',
          'ui_promotion_allowed':False,'code_sha256':{name:file_hash(root/name) for name in code},
          'policy':{'selection':'all supplied songs, all 24 constructed major/minor keys, fixed timing and voice controls before detector inference',
                    'scope':'no new calibrated confidence percentages; no independent real-song key/downbeat accuracy claim',
                    'timing_tolerances_seconds':[.03,.07],'production_thresholds_changed':False}}
    atomic_json(plan_path,{**plan,'created_utc':utc_now(),'plan_sha256':digest(plan)})
    return plan_path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pilot',type=Path); parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--jamendo',type=Path); parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    if args.limit is not None and args.limit<1: parser.error('--limit must be positive')
    print(prepare(args.pilot,args.out,args.jamendo,args.limit),flush=True)


if __name__=='__main__': main()
