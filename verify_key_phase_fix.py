"""Compare corrected production key reads against frozen stereo stress rows."""
from pathlib import Path
import argparse
import json

from detector_experiment_common import case_audio, verify_record
from detector_pilot import atomic_json, file_hash, digest
from studio_key import analyze_key, MODEL_ID
from studio_key_model import MODEL_SHA256


def run(plan_path, baseline_path, output):
    plan=json.loads(Path(plan_path).read_text());baseline=json.loads(Path(baseline_path).read_text())
    if plan['plan_sha256'] != baseline['plan_sha256']: raise ValueError('Baseline experiment differs.')
    root=Path(__file__).resolve().parent;output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    identity={'plan_sha256':plan['plan_sha256'],'baseline_sha256':file_hash(baseline_path),
              'runner_sha256':file_hash(__file__),'model_id':MODEL_ID,'model_sha256':MODEL_SHA256,
              'source_sha256':{n:file_hash(root/n) for n in ('studio_key.py','studio_key_worker.py','studio_analysis_audio.py')}}
    path=output/'key-phase-report.json';report=json.loads(path.read_text()) if path.exists() else {
        'identity':identity,'identity_sha256':digest(identity),'rows':[],'ui_promotion_allowed':False}
    if report['identity_sha256']!=digest(identity): raise ValueError('Phase-proof implementation changed.')
    previous={(r['record_id'],r['case_id']):r for r in baseline['rows'] if r['backend']=='openkeyscan'}
    done={r['record_id'] for r in report['rows']};case=next(c for c in plan['cases'] if c['type']=='polarity')
    records=[r for r in plan['records'] if r['kind'] in ('real','synthetic_tonal','synthetic_timing')]
    for record in records:
        if record['id'] in done:continue
        verify_record(record)
        with case_audio(record,case,output/'scratch') as audio:
            observed=analyze_key(audio,output/'cache')
            if not report['rows']:
                if analyze_key(audio,output/'cache')!=observed: raise ValueError('Key cache did not reuse unchanged input.')
            old=previous[(record['id'],case['id'])];plain=previous[(record['id'],'baseline')]
            key=observed['candidates'][0]['key'] if observed.get('state')=='estimated' else None
            expected=record['expected'].get('key')
            row={'record_id':record['id'],'kind':record['kind'],'case_audio_sha256':file_hash(audio),
                 'state':observed['state'],'key':key,'expected_composition_key':expected,
                 'composition_exact':key==expected if expected else None,
                 'baseline_key':plain.get('key'),'baseline_state':plain['status'],
                 'consistent_with_baseline':key==plain.get('key') if key and plain['status']=='estimated' else None,
                 'before_state':old['status'],'before_key':old.get('key'),
                 'analysis_channel':observed.get('analysis_channel'),
                 'frames':observed['frames']}
            report['rows'].append(row);atomic_json(path,report)
        print(record['id'],row['state'],key,flush=True)
    report['status']='completed'
    known=[r for r in report['rows'] if r['expected_composition_key']]
    report['summary']={'attempted':len(report['rows']),'estimated':sum(r['state']=='estimated' for r in report['rows']),
                       'constructed_compositions':len(known),'composition_exact':sum(r['composition_exact'] for r in known),
                       'real_key_correctness':'unknown; no independent labels'}
    atomic_json(path,report);return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('plan');p.add_argument('baseline');p.add_argument('--out',required=True)
    a=p.parse_args();r=run(a.plan,a.baseline,a.out);print(r['summary'])
