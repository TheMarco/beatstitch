"""Independent clock markers in the constructed drift-render controls.

The controls include broadband noise attacks on known beats. Their high-band
attacks can check clock placement without relying on the loudest oscillation
of a pitched kick, which changes when the kick is transposed. This is a
construction-specific measurement, not a general music beat detector.
"""
from pathlib import Path
import argparse
import json

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt
from scipy.ndimage import uniform_filter1d, gaussian_filter1d

from detector_pilot import atomic_json, file_hash


def markers(path, expected):
    audio,rate=sf.read(path,dtype='float64',always_2d=True)
    if rate!=48000 or not np.isfinite(audio).all(): raise ValueError('Invalid clock-control audio.')
    filtered=sosfiltfilt(butter(4,[2500,10000],btype='bandpass',fs=rate,output='sos'),audio,axis=0)
    rms=np.sqrt(np.maximum(0,uniform_filter1d(np.mean(filtered**2,axis=1),48)))
    envelope=gaussian_filter1d(np.maximum(0,rms-np.r_[np.zeros(144),rms[:-144]]),48)
    times=[];prominence=[]
    for event in expected:
        a,b=max(0,round((event-.08)*rate)),min(len(envelope),round((event+.08)*rate)+1)
        if b<=a or np.max(envelope[a:b])<1e-7: raise ValueError('No measurable constructed transient.')
        i=a+int(np.argmax(envelope[a:b]));times.append(i/rate)
        background=envelope[max(0,a-round(.06*rate)):a]
        level=float(np.percentile(background,95)) if len(background) else 0.
        prominence.append(float(envelope[i]/max(level,1e-9)))
    return {'times':times,'prominence_vs_preceding_background':prominence}


def run(folder):
    folder=Path(folder).resolve();comparison=json.loads((folder/'comparison.json').read_text());rows=[]
    for row in comparison['rows']:
        sub=folder/f"{row['engine']}-pitch{row['pitch_semitones']}"
        before=row['original_attacks']['expected_seconds'];after=row['corrected_attacks']['expected_seconds']
        original=sub/row['excerpts']['original']['path'];corrected=sub/row['excerpts']['corrected']['path']
        observed_before=markers(original,before);observed_after=markers(corrected,after)
        ratio=np.median(np.diff(after))/np.median(np.diff(before))
        errors=(np.array(observed_after['times'])-after-(np.array(observed_before['times'])-before)*ratio)*1000
        rows.append({'engine':row['engine'],'pitch_semitones':row['pitch_semitones'],
                     'source_sha256':file_hash(original),'corrected_sha256':file_hash(corrected),
                     'errors_ms':errors.tolist(),'p95_abs_ms':float(np.percentile(abs(errors),95)),
                     'max_abs_ms':float(np.max(abs(errors))),
                     'within_30ms':bool(np.max(abs(errors))<=30),
                     'source_markers':observed_before,'corrected_markers':observed_after})
    report={'rows':rows,'runner_sha256':file_hash(__file__),'comparison_sha256':file_hash(folder/'comparison.json'),
            'scope':'Constructed broadband beat markers only; musical kick shape and perceived quality remain separate.',
            'ui_promotion_allowed':False}
    atomic_json(folder/'transient-marker-report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('folder',type=Path)
    args=parser.parse_args();report=run(args.folder)
    print([(r['engine'],r['pitch_semitones'],r['p95_abs_ms'],r['within_30ms']) for r in report['rows']])
