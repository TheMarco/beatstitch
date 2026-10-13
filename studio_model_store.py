"""Optional model packs in writable user storage, independent of app updates."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile

from filelock import FileLock, Timeout

from studio_model_download import download, verified
from studio_process import check_cancelled
from studio_storage import require_space

ROOT = Path(__file__).resolve().parent
MODEL_ENV = 'BEATSTITCH_MODEL_DIR'


def configure(root):
    root = Path(root).resolve()
    os.environ[MODEL_ENV] = str(root)
    return root


def model_root():
    if os.environ.get(MODEL_ENV):
        return Path(os.environ[MODEL_ENV]).expanduser().resolve()
    if sys.platform == 'darwin':
        data = Path.home() / 'Library/Application Support'
    elif sys.platform == 'win32':
        data = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    else:
        data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    return data / 'DJ Studio/DJ Studio/models'


def _file(name, size, sha256, url, **extra):
    return dict(name=name, size=size, sha256=sha256, url=url, **extra)


@lru_cache(maxsize=1)
def packs():
    # Reuse the inference engines' pinned identities: moving a model does not
    # invalidate previous analysis/audio caches or change the detector.
    from studio_structure import MODEL_SHA256 as beat_hash, MODEL_URL as beat_url
    from studio_content import MODEL_SHA256 as content_hash, MODEL_URL as content_url, MODEL_SIZE
    from studio_key_model import MODEL_FILE, MODEL_BYTES, MODEL_SHA256, SOURCE_REVISION, LICENSE_FILE, LICENSE_SHA256
    from studio_separation_model import MODEL_FILE as separation_file, MODEL_BYTES as separation_size, MODEL_SHA256 as separation_hash
    from download_separation_model import ARCHIVE_URL, ARCHIVE_BYTES, ARCHIVE_SHA256
    from download_section_models import MANIFEST, SOURCES, CHECKPOINT
    key_base = f'https://raw.githubusercontent.com/rekordcloud/openkeyscan-analyzer/{SOURCE_REVISION}/'
    sections = [_file(name, item['size'], item['sha256'], SOURCES[name],
                      incoming=CHECKPOINT if name == 'edmformer.safetensors' else item,
                      conversion='sections' if name == 'edmformer.safetensors' else None)
                for name, item in MANIFEST.items()]
    result = {
        'beats': dict(title='Beat detection', recommended=True, legacy='models',
                      description='Detect beats and bars, suggest musical cue points, and improve automatic BPM estimates. Basic tempo estimates and manual cues work without it.',
                      license='Beat This · MIT', files=[_file('beat-this-final0.ckpt', 81058141, beat_hash, beat_url)]),
        'key': dict(title='Key detection', recommended=True, legacy='key',
                    description='Estimate each song’s original key and suggest compatible tracks. You can still enter keys, transpose and mix by ear without this download.',
                    license='OpenKeyScan · MIT', files=[_file(MODEL_FILE, MODEL_BYTES, MODEL_SHA256, key_base+'checkpoints/'+MODEL_FILE),
                        _file(LICENSE_FILE, 1073, LICENSE_SHA256, key_base+'LICENSE')]),
        'content': dict(title='Voice and drum detection', recommended=True, legacy='models',
                        description='Find possible voices and drum-led passages to help choose blends and extend drums. Listening, ordinary blends and manual cues work without it.',
                        license='PANNs · CC BY 4.0 weights / MIT code', files=[_file('panns-cnn10.pth', MODEL_SIZE, content_hash, content_url)]),
        'separation': dict(title='Vocal removal and musical parts', recommended=False, legacy='separation',
                           description='Reduce competing vocals or keep mostly drums inside a blend. Ordinary mixing and automatic mastering do not need it.',
                           license='Spleeter code and weights · MIT', files=[_file(separation_file, separation_size, separation_hash, ARCHIVE_URL,
                               incoming={'size': ARCHIVE_BYTES, 'sha256': ARCHIVE_SHA256}, conversion='separation')]),
        'sections': dict(title='Advanced build/drop detection', recommended=False, legacy='sections',
                         description='Suggest named builds, drops, breakdowns, intros and outros. This large experimental detector is optional; beat-based cue suggestions remain available with Beat detection.',
                         license='Includes MuQ weights · CC BY-NC 4.0, for noncommercial use; other components retain their own licenses.', files=sections),
    }
    for pack in result.values():
        pack['download_bytes'] = sum(f.get('incoming', f)['size'] for f in pack['files'])
        pack['installed_bytes'] = sum(f['size'] for f in pack['files'])
    return result


def supported(pack):
    if pack not in packs():
        raise ValueError('Unknown model pack.')
    marker = ROOT / 'model-packs.json'
    if marker.is_file():
        try:
            return bool(json.loads(marker.read_text(encoding='utf-8')).get('features', {}).get(pack, True))
        except (OSError, ValueError, TypeError):
            return False
    return True


def _owned(root, relative):
    relative = PurePosixPath(relative)
    if relative.is_absolute() or '..' in relative.parts or '\\' in str(relative):
        raise ValueError('Invalid model asset path.')
    path = Path(root).joinpath(*relative.parts)
    if any(p.is_symlink() for p in (path, *path.parents) if p == root or root in p.parents):
        raise ValueError('Model storage cannot contain symbolic links.')
    return path


def _present(folder, spec):
    try:
        return all(not (folder/f['name']).is_symlink() and (folder/f['name']).is_file()
                   and (folder/f['name']).stat().st_size == f['size'] for f in spec['files'])
    except OSError:
        return False


def directory(pack):
    """External installed models first; legacy/source bundles remain usable."""
    spec = packs()[pack]
    external = model_root() / pack
    if _present(external, spec):
        return external
    if os.environ.get('BEATSTITCH_NO_BUNDLED_MODELS') != '1':
        legacy = ROOT / 'assets' / spec['legacy']
        if _present(legacy, spec):
            return legacy
        if pack == 'separation' and not getattr(sys, 'frozen', False):
            research = ROOT / 'outputs/spleeter-research/models'
            if _present(research, spec):
                return research
    return external


def available(pack):
    return supported(pack) and _present(directory(pack), packs()[pack])


def _identity(path):
    s = path.stat()
    return [s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino, s.st_dev]


def status(pack):
    spec = packs()[pack]
    if not supported(pack):
        return 'unsupported'
    folder = directory(pack)
    if _present(folder, spec):
        if not folder.is_relative_to(model_root()):
            return 'included'
        try:
            receipt = json.loads((folder/'installed.json').read_text(encoding='utf-8'))
            if all(receipt['files'][f['name']] == {'sha256': f['sha256'], 'identity': _identity(folder/f['name'])}
                   for f in spec['files']):
                return 'ready'
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return 'verify'
    return 'partial' if folder.is_dir() or (model_root()/'.downloads'/pack).is_dir() else 'missing'


@contextmanager
def _lock(pack):
    root = model_root(); root.mkdir(parents=True, exist_ok=True)
    locks = _owned(root, '.locks'); locks.mkdir(exist_ok=True)
    lock = FileLock(locks / (pack+'.lock'))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise ValueError('This download is already being changed by another BeatStitch window. Try again when it finishes.')
    try:
        yield root
    finally:
        lock.release()


def install(pack, progress=lambda event: None):
    if not supported(pack):
        raise ValueError('This feature is unavailable in this app build.')
    spec = packs()[pack]
    with _lock(pack) as root:
        folder = _owned(root, pack); folder.mkdir(exist_ok=True)
        downloads = _owned(root, '.downloads/'+pack); downloads.mkdir(parents=True, exist_ok=True)
        completed = 0
        for f in spec['files']:
            check_cancelled()
            target = _owned(folder, f['name']); target.parent.mkdir(parents=True, exist_ok=True)
            incoming = f.get('incoming', f)
            def report(phase, count=0, total=incoming['size'], message=''):
                progress(dict(pack=pack, phase=phase, file=f['name'], received=count, total=total,
                              pack_received=completed+count, pack_total=spec['download_bytes'], message=message))
            report('Verifying', message='Checking existing model files')
            if verified(target, f):
                report('Ready', incoming['size'])
                completed += incoming['size']
                continue
            needed = incoming['size'] + (f['size'] * 2 if f.get('conversion') else 0)
            require_space(root, needed, 'download '+spec['title'])
            staged = downloads / f['sha256']
            report('Downloading')
            download(f['url'], staged, incoming, lambda count, total: report('Downloading', count, total))
            check_cancelled()
            report('Preparing', incoming['size'], message='Verifying and preparing the downloaded model')
            if f.get('conversion') == 'separation':
                from download_separation_model import fetch_model
                fetch_model(folder, archive=staged, progress=lambda text: (check_cancelled(), report('Preparing', incoming['size'], message=text)))
            elif f.get('conversion') == 'sections':
                from download_section_models import convert_checkpoint
                with tempfile.TemporaryDirectory(prefix='.prepare-', dir=folder) as temporary:
                    converted = Path(temporary)/'model.safetensors'
                    convert_checkpoint(staged, converted)
                    check_cancelled()
                    if not verified(converted, f):
                        raise ValueError('The prepared model did not match its pinned identity.')
                    os.replace(converted, target)
            else:
                os.replace(staged, target)
            check_cancelled()
            if not verified(target, f):
                raise ValueError('The installed model could not be verified. Retry to repair it.')
            staged.unlink(missing_ok=True)
            completed += incoming['size']
        receipt = {'schema': 1, 'files': {f['name']: {'sha256': f['sha256'], 'identity': _identity(folder/f['name'])} for f in spec['files']}}
        pending = folder/'installed.pending'
        pending.write_text(json.dumps(receipt, indent=2)+'\n', encoding='utf-8')
        check_cancelled(); os.replace(pending, folder/'installed.json')
        progress(dict(pack=pack, phase='Ready', received=spec['download_bytes'], total=spec['download_bytes'],
                      pack_received=spec['download_bytes'], pack_total=spec['download_bytes'], message=spec['title']+' is ready.'))
    return folder


def remove(pack):
    if pack not in packs():
        raise ValueError('Unknown model pack.')
    with _lock(pack) as root:
        work = FileLock(root.parent / '.cache-work.lock')
        try:
            work.acquire(timeout=0)
        except Timeout:
            raise ValueError('An audio or analysis job is using this data folder. Finish it before removing models.')
        try:
            folder = _owned(root, pack)
            downloads = _owned(root, '.downloads/'+pack)
            for path in (folder, downloads):
                if path.exists():
                    shutil.rmtree(path)
        finally:
            work.release()
    # Never remove songs, prepared audio, projects, or models inside an old app.


def missing_message(pack):
    return packs()[pack]['title']+' is not downloaded or needs repair. Open File → Downloads & features to install it. Manual mixing still works.'
