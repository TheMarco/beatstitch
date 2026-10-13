"""Assemble an auditable source candidate from current code and pinned archives."""
import argparse,hashlib,json,re,shutil,subprocess,tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-root',type=Path,required=True)
parser.add_argument('--app',type=Path,required=True)
args=parser.parse_args()
out=args.source_root.resolve();inputs=out/'inputs';app=args.app.resolve()
if not (app/'Contents/MacOS/BeatStitch').is_file():
 raise SystemExit('Expected the finished BeatStitch Mac bundle.')
if not (out/'source-receipts.json').is_file():
 raise SystemExit('Collect the reviewed source receipts before staging.')

def sha(path):
 with Path(path).open('rb') as h:return hashlib.file_digest(h,'sha256').hexdigest()

def artifact(path,kind):
 return {'path':str(Path(path).relative_to(out)),'kind':kind,'sha256':sha(path)}

# Capture the reviewed working tree, including new/uncommitted implementation.
# Audio previews, user's songs, caches, weights and private configuration are excluded.
files=subprocess.run(['git','ls-files','-co','--exclude-standard','-z'],capture_output=True,check=True,cwd=ROOT).stdout.decode().split('\0')
files=sorted(set(n for n in files if n and not n.startswith(('assets/branding/','.codex/','.agents/'))))
files=[n for n in files if (ROOT/n).is_file() and not (ROOT/n).is_symlink()]
files=[n for n in files if not (n.startswith(('assets/demo/','assets/lessons/')) and n.endswith(('.wav','.mp3','.flac')))]
snapshot=inputs/'beatstitch-source.tar.gz'
with tarfile.open(snapshot,'w:gz') as archive:
 for name in files:archive.add(ROOT/name,arcname='Beatstitch/'+name,recursive=False)
(input_manifest:=inputs/'app-source-files.json').write_text(json.dumps({'files':{n:sha(ROOT/n) for n in files},'excluded':'Private outputs/cache, model weights, branding experiments and generated practice audio; model/demo/lesson build recipes are included.'},indent=2)+'\n')
for name in ('LICENSE','LICENSE_NOTICE.txt','README.md','requirements.txt','requirements-desktop.txt','requirements-key.txt','requirements-sections.txt','requirements-separation.txt','requirements-release-macos.txt','RELEASE_MAC.md','build_desktop.py','download_separation_model.py','studio_spleeter_checkpoint.py','studio_spleeter_model.py'):
 shutil.copyfile(ROOT/name,inputs/name)
shutil.copyfile(out/'source-instructions.txt',inputs/'REBUILD.txt')
rows=json.loads((out/'source-receipts.json').read_text())+json.loads((out/'python-source-receipts.json').read_text())+json.loads((out/'runtime-source-receipts.json').read_text())
components=[]
for row in rows:
 if sha(out/row['archive'])!=row['sha256']:
  raise SystemExit(f"Changed source archive: {row['id']}")
 cid=re.sub('[^A-Za-z0-9._-]','-',row['id'])
 arts=[artifact(out/row['archive'],'source_archive')]
 arts.extend(artifact(out/p,'license') for p in row['notices'] if Path(p).suffix not in ('.py','.pyc','.sh'))
 arts.append(artifact(inputs/'REBUILD.txt','build_recipe'))
 if row.get('recipe'):arts.append(artifact(out/row['recipe'],'build_recipe'))
 if row.get('patch'):arts.append(artifact(out/row['patch'],'patch'))
 if row.get('provenance'):arts.append(artifact(out/row['provenance'],'build_recipe'))
 if row['id']=='gcc-runtime':arts.append(artifact(out/'inputs/gcc13/BUILD.txt','build_recipe'))
 if row['id']=='ffmpeg':arts.append(artifact(inputs/'ffmpeg-build-info.txt','build_recipe'))
 if row['id']=='qt-ffmpeg':arts.append(artifact(inputs/'qt-ffmpeg-configure.txt','build_recipe'))
 if row['id']=='qt-python':arts.append(artifact(inputs/'pyside-wheel-config.py','build_recipe'))
 components.append({'id':cid,'version':row['version'],'license':row['license'],'source_url':row['url'][0],'artifacts':arts})
# Published MIT model source, conversion recipe and original model license.
shutil.copyfile(ROOT/'outputs/spleeter-research/spleeter-2.4.2.tar.gz',inputs/'spleeter-2.4.2.tar.gz')
shutil.copyfile(ROOT/'vendor/spleeter/LICENSE',inputs/'SPLEETER-LICENSE')
shutil.copyfile(ROOT/'vendor/spleeter/ABOUT.txt',inputs/'SPLEETER-MODEL-PROVENANCE.txt')
components.append(dict(id='spleeter',version='2.4.2 / model v1.4.0',license='MIT code and pretrained weights',source_url='https://github.com/deezer/spleeter',artifacts=[artifact(inputs/'spleeter-2.4.2.tar.gz','source_archive'),artifact(inputs/'SPLEETER-LICENSE','license'),artifact(inputs/'SPLEETER-MODEL-PROVENANCE.txt','build_recipe'),artifact(inputs/'download_separation_model.py','build_recipe'),artifact(inputs/'studio_spleeter_checkpoint.py','build_recipe')]))
components.append(dict(id='beatstitch',version='release snapshot 2026-10-13',license='GPL-3.0-or-later; third-party notices apply separately',source_url='https://github.com/TheMarco/dj-studio',artifacts=[artifact(snapshot,'source_archive'),artifact(inputs/'LICENSE','license'),artifact(inputs/'LICENSE_NOTICE.txt','license'),artifact(inputs/'README.md','build_recipe'),artifact(inputs/'REBUILD.txt','build_recipe'),artifact(inputs/'build_desktop.py','build_recipe'),artifact(input_manifest,'build_recipe')]))
# Exact source, patch and scripts for the locally modified Qt Cocoa binary.
patch_folder=inputs/'qt-cocoa';patch_folder.mkdir(exist_ok=True)
for name in ('README.md','manifest.json','upstream.patch','CMakeLists.txt'):
 shutil.copyfile(ROOT/'vendor/qt-cocoa'/name,patch_folder/name)
shutil.copyfile(ROOT/'rebuild_qt_cocoa.py',patch_folder/'rebuild_qt_cocoa.py')
shutil.copyfile(ROOT/'assets/qt/qtbase-v6.11.2.tar.gz',patch_folder/'qtbase-v6.11.2.tar.gz')
components.append(dict(id='qt-cocoa-fix',version='6.11.2 / c7fd3f34b997bb363be15650647665b3b6b8a5f4',license='Qt GPL/LGPL; upstream source notices',source_url='https://codereview.qt-project.org/c/qt/qtbase/+/765434',artifacts=[artifact(patch_folder/'qtbase-v6.11.2.tar.gz','source_archive'),artifact(inputs/'LICENSE','license'),artifact(patch_folder/'upstream.patch','patch'),*[artifact(patch_folder/n,'build_recipe') for n in ('README.md','manifest.json','CMakeLists.txt','rebuild_qt_cocoa.py')]]))

# Record every distinct packaged native binary, including symlink target identities.
binaries=[]
for p in app.rglob('*'):
 if not p.is_file() or p.is_symlink():continue
 with p.open('rb') as f:magic=f.read(4)
 if magic in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca'):
  binaries.append({'path':str(p.relative_to(app)),'sha256':sha(p)})
if not binaries:
 raise SystemExit('No native binaries were inventoried; refusing an empty release identity.')
# Include pinned release receipts so the source collection can be reproduced.
for name in ('source-receipts.json','python-source-receipts.json','runtime-source-receipts.json'):
 shutil.copyfile(out/name,inputs/name)
 components[-1]['artifacts'].append(artifact(inputs/name,'build_recipe'))
manifest={'schema':1,'required_components':[c['id'] for c in components],'components':components,'binaries':binaries}
(out/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print({'source_components':len(components),'app_source_files':len(files),'packaged_native_binaries':len(binaries)})
