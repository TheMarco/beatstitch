"""External check of previously frozen timing confidence maps; no refitting."""
from pathlib import Path
import argparse
import importlib.util
import json
import sys

from detector_pilot import atomic_json, file_hash


def run(folder):
    root=Path(__file__).resolve().parent;folder=Path(folder).resolve()
    previous=root/'outputs/gtzan-timing-confidence';maps_path=previous/'frozen-maps.json'
    frozen=json.loads(maps_path.read_text());plan=json.loads((folder/'plan.json').read_text())
    module_path=previous/'summarize_experiment.py'
    sys.path.insert(0,str(previous))
    spec=importlib.util.spec_from_file_location('previous_grouped_timing_metrics',module_path)
    metrics=importlib.util.module_from_spec(spec);spec.loader.exec_module(metrics)
    converted=[];failed=[]
    for rec in plan['records']:
        path=folder/'results'/f"{rec['id']}.json";row=json.loads(path.read_text())
        if row['status']!='scored': failed.append({'id':rec['id'],'error':row.get('error')});continue
        converted.append({'record':{'id':rec['id'],'group':row['group'],'split':row['split']},
                          'calibration':{e:{'scores':row['outcomes'][e]['scores'],
                                            'outcomes':row['outcomes'][e]['correct_70ms']}
                                         for e in ('beats','downbeats')}})
    results={}
    for split in ('fit','validation','test'):
        rows=[r for r in converted if r['record']['split']==split]
        results[split]={e:metrics.evaluate(rows,e,frozen['maps'][e]) for e in ('beats','downbeats')}
    report={'status':'completed','maps_sha256':file_hash(maps_path),'reference_plan_sha256':plan['plan_sha256'],
            'runner_sha256':file_hash(__file__),'metrics_source_sha256':file_hash(module_path),
            'results':results,'failed':failed,'ui_promotion_allowed':False,
            'scope':'External evaluation of maps frozen before this collection; no parameter fitting or method selection on fresh data.',
            'limitations':['Reference inputs are official float16 features, a proxy for the app float32 frontend.',
                           'This collection does not establish calibration on AI-generated EDM or actual blend quality.',
                           'Each artist/duplicate component stays in one partition; previously observed components are excluded.']}
    atomic_json(folder/'previous-map-evaluation.json',report)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder');a=p.parse_args();r=run(a.folder)
    print({s:{e:v['comparison_rule_passed'] for e,v in events.items()} for s,events in r['results'].items()})
