"""Split independent records without repeating completed key inferences."""
from pathlib import Path
import argparse
import importlib.util
import json

from detector_experiment_common import load_plan
from detector_pilot import atomic_json, digest, file_hash


def key_module(plan):
    path = Path(plan['runtime_root']) / 'detector_experiment_key.py'
    spec = importlib.util.spec_from_file_location('frozen_key_experiment', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def split(plan_path, report_path, output, count=3):
    if type(count) is not int or count < 1: raise ValueError('Need a positive shard count.')
    plan = load_plan(plan_path); source = json.loads(Path(report_path).read_text())
    module = key_module(plan)
    if (source.get('plan_sha256') != plan['plan_sha256']
            or source.get('runner_sha256') != file_hash(module.__file__)):
        raise ValueError('Completed rows do not belong to this frozen experiment.')
    module._validate_saved_rows(source, plan)
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    receipt = {'parent_plan': str(Path(plan_path).resolve()), 'parent_plan_sha256': plan['plan_sha256'],
               'inherited_report': str(Path(report_path).resolve()), 'inherited_report_sha256': file_hash(report_path),
               'coordinator_sha256': file_hash(__file__), 'shards': []}
    for index in range(count):
        records = plan['records'][index::count]; ids = {r['id'] for r in records}
        child = {k:v for k,v in plan.items() if k not in ('plan_sha256', 'created_utc')}
        child.update(records=records, parent_plan_sha256=plan['plan_sha256'], shard_index=index,
                     inherited_report_sha256=receipt['inherited_report_sha256'])
        child['plan_sha256'] = digest(child)
        folder = output / str(index); folder.mkdir()
        child_path = folder / 'plan.json'; atomic_json(child_path, child)
        rows = [r for r in source['rows'] if r['record_id'] in ids]
        saved = {**source, 'plan_sha256': child['plan_sha256'], 'rows': rows, 'status': 'running',
                 'inherited_from': {k:receipt[k] for k in ('inherited_report','inherited_report_sha256','parent_plan_sha256')}}
        saved['summary'] = module._summarize(rows, records, child['cases'])
        atomic_json(folder/'key-report.json', saved)
        receipt['shards'].append({'plan':str(child_path), 'out':str(folder),
                                 'plan_sha256':child['plan_sha256'], 'records':len(records), 'inherited_rows':len(rows)})
    receipt['receipt_sha256'] = digest(receipt)
    atomic_json(output/'manifest.json', receipt)
    return receipt


def merge(manifest_path, output):
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get('receipt_sha256') != digest({k:v for k,v in manifest.items() if k!='receipt_sha256'}):
        raise ValueError('Shard receipt changed.')
    if file_hash(manifest['inherited_report']) != manifest['inherited_report_sha256']:
        raise ValueError('Inherited evidence changed.')
    plan = load_plan(manifest['parent_plan']); module = key_module(plan)
    combined = None; rows = []; identities = set(); receipts = []
    for shard in manifest['shards']:
        child = load_plan(shard['plan']); path = Path(shard['out'])/'key-report.json'
        report = json.loads(path.read_text())
        if (report['status'] != 'finished' or report['plan_sha256'] != child['plan_sha256']
                or child['plan_sha256'] != shard['plan_sha256']
                or report['runner_sha256'] != file_hash(module.__file__)):
            raise ValueError('Shard is incomplete or changed: ' + str(path))
        module._validate_saved_rows(report, child)
        expected = {(r['id'],c['id'],b) for r in child['records']
                    for c in module.applicable_cases(child,r,'key') for b in module.BACKENDS}
        actual = {(r['record_id'],r['case_id'],r['backend']) for r in report['rows']}
        if expected != actual or identities & actual: raise ValueError('Missing or duplicated shard rows.')
        identities |= actual; rows.extend(report['rows']); combined = report
        receipts.append({'path':str(path),'sha256':file_hash(path),'rows':len(actual)})
    expected = {(r['id'],c['id'],b) for r in plan['records']
                for c in module.applicable_cases(plan,r,'key') for b in module.BACKENDS}
    if identities != expected: raise ValueError('Merged experiment is incomplete.')
    combined.update(plan_sha256=plan['plan_sha256'], rows=rows, shards=receipts,
                    shard_manifest_sha256=file_hash(manifest_path), status='finished')
    combined['summary'] = module._summarize(rows,plan['records'],plan['cases'])
    atomic_json(Path(output)/'key-report.json', combined)
    return combined


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('split');s.add_argument('plan');s.add_argument('report');s.add_argument('--out',required=True);s.add_argument('--count',type=int,default=3)
    m=sub.add_parser('merge');m.add_argument('manifest');m.add_argument('--out',required=True)
    a=p.parse_args()
    result=split(a.plan,a.report,a.out,a.count) if a.action=='split' else merge(a.manifest,a.out)
    print(json.dumps({'status':'prepared' if a.action=='split' else 'finished',
                      'rows':len(result.get('rows',[])), 'shards':len(result.get('shards',[]))}))
