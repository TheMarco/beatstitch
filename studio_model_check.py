"""Exercise a frozen app's real user-store download and key worker in isolation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    report = {'status':'running', 'frozen':bool(getattr(sys,'frozen',False)), 'checks':[]}
    original = {k:os.environ.get(k) for k in ('BEATSTITCH_MODEL_DIR','BEATSTITCH_NO_BUNDLED_MODELS','NUMBA_CACHE_DIR')}
    def save():
        (output/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    try:
        from studio_model_store import configure, install, available, directory, status
        configure(output/'models'); os.environ['BEATSTITCH_NO_BUNDLED_MODELS']='1'
        os.environ['NUMBA_CACHE_DIR']=str(output/'numba-cache')
        if available('key'): raise ValueError('The supposedly fresh model store is not empty.')
        last=[None]
        def progress(event):
            key=(event['phase'], round(100*event['pack_received']/max(1,event['pack_total'])))
            if key != last[0]:
                last[0]=key; print(f"Key download: {key[0]} {key[1]}%", flush=True)
        folder=install('key', progress)
        if status('key') != 'ready' or directory('key') != folder or not folder.is_relative_to(output):
            raise ValueError('Downloaded key files are not verified in the isolated user store.')
        report['checks'].append({'name':'real HTTPS download, verification and user-store resolution','status':'passed'})
        from studio_model_download import verified
        from studio_model_store import packs
        for f in packs()['key']['files']:
            if not verified(folder/f['name'],f): raise ValueError('Downloaded key model changed.')
        # Reuse after a completed install cannot need a second HTTP request.
        from unittest.mock import patch
        with patch('studio_model_download.urllib.request.urlopen',side_effect=AssertionError('Repeated download')):
            install('key')
        report['checks'].append({'name':'installed model reused without network','status':'passed'})
        from studio_demo import bundled_project
        from studio_projects import read_project
        from studio_key import analyze_key, valid_result
        session,_=read_project(bundled_project()); source=Path(session.tracks[session.clips[0].track_id].path)
        with source.open('rb') as stream: before=hashlib.file_digest(stream,'sha256').hexdigest()
        result=analyze_key(source,output/'key-cache')
        if not valid_result(result) or result['state']!='estimated': raise ValueError('Downloaded-model key inference returned no usable observation.')
        with source.open('rb') as stream: after=hashlib.file_digest(stream,'sha256').hexdigest()
        if before!=after: raise ValueError('Key inference changed the practice audio.')
        report['checks'].append({'name':'frozen key worker uses downloaded models; source unchanged','status':'passed','key':result['candidates'][0]['key']})
        report['status']='passed'; save(); return 0
    except Exception as error:
        report['status']='failed'; report['error']=str(error); save(); return 1
    finally:
        for key,value in original.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value


if __name__=='__main__': raise SystemExit(main())
