"""Exercise optional workers in an existing desktop package, without rebuilding it."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time

import numpy as np
import soundfile as sf

RATE, SECONDS = 48000, 12
STEMS = ('drums', 'bass', 'other', 'vocals')
LABELS = {'intro', 'buildup', 'drop', 'breakdown', 'outro', 'silence'}


def sha256(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def isolated_environment(work, model_dir=None):
    environment = os.environ.copy()
    for name in ('PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH',
                 'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'):
        environment.pop(name, None)
    environment.update(PATH='', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                       HF_HOME=str(work/'hf'), TORCH_HOME=str(work/'torch'),
                       XDG_CACHE_HOME=str(work/'cache'), QT_QPA_PLATFORM='offscreen')
    environment['BEATSTITCH_MODEL_DIR'] = str(model_dir or work/'models')
    if model_dir is not None:
        environment['BEATSTITCH_NO_BUNDLED_MODELS'] = '1'
    return environment


def stop_worker(process):
    if process.poll() is not None:
        return
    if sys.platform == 'win32':
        taskkill = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/taskkill.exe'
        try:
            subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if sys.platform != 'win32':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.kill()
        process.wait()


def run_worker(executable, arguments, work, log, timeout=600, model_dir=None):
    options = ({'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32'
               else {'start_new_session': True})
    with log.open('wb') as diagnostics, subprocess.Popen(
            [str(executable), *map(str, arguments)], cwd=work, env=isolated_environment(work, model_dir),
            stdout=diagnostics, stderr=subprocess.STDOUT, **options) as process:
        try:
            code = process.wait(timeout=timeout)
        finally:
            stop_worker(process)
    result = work/'result.json'
    if not result.is_file() or result.stat().st_size > 1024 * 1024:
        raise ValueError(f'Worker exited {code} without a bounded result; see {log.name}.')
    data = json.loads(result.read_text(encoding='utf-8'))
    if code or not isinstance(data, dict) or 'error' in data:
        reason = data.get('error', f'exit {code}') if isinstance(data, dict) else 'invalid result'
        raise ValueError(str(reason)[:1200])
    return data


def validate_sections(result):
    regions = result.get('sections')
    if not isinstance(regions, list) or not 1 <= len(regions) <= 2048:
        raise ValueError('No bounded section result.')
    previous = 0.
    for region in regions:
        if not isinstance(region, dict) or region.get('label') not in LABELS:
            raise ValueError('Unknown section label.')
        a, b = region.get('start'), region.get('end')
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (a, b))
                or not previous <= a < b <= SECONDS + .15):
            raise ValueError('Invalid section boundaries.')
        previous = b
    return {'regions': regions, 'device': result.get('device')}


def validate_stems(result, work, frames):
    if result != {'frames': frames, 'rate': RATE, 'channels': 2}:
        raise ValueError('Worker returned the wrong excerpt dimensions.')
    peaks = {}
    for name in STEMS:
        samples, rate = sf.read(work/(name+'.wav'), dtype='float32', always_2d=True)
        if rate != RATE or samples.shape != (frames, 2) or not np.isfinite(samples).all():
            raise ValueError('Invalid or misaligned stem: ' + name)
        peaks[name] = float(np.max(np.abs(samples)))
    if max(peaks.values()) < 1e-6:
        raise ValueError('All separated stems are silent.')
    return {**result, 'peak_amplitudes': peaks}


def make_source(path):
    t = np.arange(RATE * SECONDS, dtype=np.float64) / RATE
    pulse = np.exp(-35 * (t % .5)) * np.sin(2 * np.pi * 70 * t)
    chord = sum(np.sin(2 * np.pi * hz * t) for hz in (220, 261.6256, 329.6276)) / 3
    gate = .4 + .6 * np.sin(np.pi * t / 4) ** 2
    left = .17 * pulse + .08 * chord * gate
    right = .16 * pulse + .07 * chord * gate + .01 * np.sin(2 * np.pi * 440 * t)
    sf.write(path, np.column_stack((left, right)), RATE, subtype='FLOAT')


def check_package(executable, resources, output, *, sections=False, separation=False, model_dir=None):
    if not (sections or separation):
        raise ValueError('Select at least one optional worker to check.')
    executable, resources, output = (Path(p).resolve() for p in (executable, resources, output))
    model_dir = Path(model_dir).resolve() if model_dir is not None else None
    if not executable.is_file() or not resources.is_dir():
        raise ValueError('Choose an existing packaged executable and its resource directory.')
    expected_resources = (executable.parent.parent/'Frameworks'
                          if executable.parent.name == 'MacOS' and executable.parent.parent.name == 'Contents'
                          else executable.parent/'_internal')
    if resources != expected_resources.resolve():
        raise ValueError('Resources must belong to this executable, not a development folder or another app.')
    output.mkdir(parents=True, exist_ok=False)
    report = {'version': 1, 'checker_sha256': sha256(__file__),
              'status': 'running', 'platform': sys.platform, 'os': platform.platform(),
              'architecture': platform.machine(), 'executable': str(executable),
              'executable_sha256': sha256(executable), 'checks': [],
              'not_tested': ['Musical accuracy or separation quality', 'Native UI and physical audio output',
                             'Long recordings, cancellation through the UI, and other operating systems']}

    def save():
        pending = output/'report.pending'
        pending.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        pending.replace(output/'report.json')

    save()
    try:
        # All generated audio and model caches are discarded after this check.
        with tempfile.TemporaryDirectory(prefix='dj-optional-workers-') as temporary:
            root = Path(temporary)
            source = root/'Original generated practice.wav'
            make_source(source)
            before = sha256(source)
            for name, enabled in (('sections', sections), ('separation', separation)):
                if not enabled:
                    continue
                item = {'name': name, 'status': 'running'}
                report['checks'].append(item); save()
                began = time.monotonic()
                try:
                    work = root/name; work.mkdir()
                    if name == 'sections':
                        manifest = json.loads((resources/'section-model-manifest.json').read_text())
                        for relative, expected in manifest.items():
                            path = (model_dir/'sections' if model_dir else resources/'assets/sections')/relative
                            if path.stat().st_size != expected['size'] or sha256(path) != expected['sha256']:
                                raise ValueError('Invalid bundled section model: ' + relative)
                        result = run_worker(executable, ['--section-worker', source, work], work, output/(name+'.log'),
                                            **({'model_dir':model_dir} if model_dir else {}))
                        item['details'] = validate_sections(result)
                    else:
                        import shutil
                        shutil.copyfile(source, work/'decoded.wav')
                        start, end = 4 * RATE, 8 * RATE
                        (work/'request.json').write_text(json.dumps({'start': start, 'end': end,
                            'models': str(model_dir/'separation' if model_dir else resources/'assets/separation')}), encoding='utf-8')
                        result = run_worker(executable, ['--separation-worker', work], work, output/(name+'.log'),
                                            **({'model_dir':model_dir} if model_dir else {}))
                        item['details'] = validate_stems(result, work, end - start)
                    if sha256(source) != before:
                        raise ValueError('The original generated source was modified.')
                    item['status'] = 'passed'
                except Exception as error:
                    item.update(status='failed', error=f'{type(error).__name__}: {error}')
                finally:
                    item['seconds'] = round(time.monotonic() - began, 3); save()
        report['status'] = 'passed' if all(c['status'] == 'passed' for c in report['checks']) else 'failed'
    except Exception as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
    finally:
        save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('executable', 'resources', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--sections', action='store_true')
    parser.add_argument('--separation', action='store_true')
    parser.add_argument('--model-dir', type=Path, help='External model root containing sections/ and separation/; tests a model-free app after downloads.')
    args = parser.parse_args()
    report = check_package(**vars(args))
    print(f'{report["status"]}: {args.output / "report.json"}')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
