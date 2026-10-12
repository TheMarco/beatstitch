"""Package the native listener with prepared excerpts, without inference models."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent
    report_path = root / 'outputs/stem-separation-trial/example-report.json'
    report = json.loads(report_path.read_text())
    folder = Path(report['folder'])
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--onedir',
            '--name', 'DJ Separation Trial', '--distpath', str(root / 'dist'),
            '--workpath', str(root / 'build/separation-trial'), '--specpath', str(root / 'build/separation-trial'),
            '--add-data', str(root / 'assets/fonts') + ':assets/fonts',
            '--add-data', str(report_path) + ':example']
    # Bundle a portable report filename without changing the saved trial report.
    portable = root / 'build/separation-trial/report.json'
    portable.parent.mkdir(parents=True, exist_ok=True)
    portable.write_text(json.dumps(report, allow_nan=False))
    args += ['--add-data', str(portable) + ':example']
    for name in ('original', 'instrumental', 'drums', 'vocals', 'recombined'):
        args += ['--add-data', str(folder / f'{name}.wav') + ':example']
    for module in ('torch', 'torchaudio', 'demucs', 'transformers', 'librosa', 'numba',
                   'matplotlib', 'tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets'):
        args += ['--exclude-module', module]
    if sys.platform == 'darwin':
        args += ['--osx-bundle-identifier', 'local.djstudio.separationtrial']
    args.append(str(root / 'prototype_separation.py'))
    environment = os.environ.copy()
    environment.setdefault('PYINSTALLER_CONFIG_DIR', str(root / 'build/pyinstaller-cache'))
    subprocess.run(args, cwd=root, env=environment, check=True)


if __name__ == '__main__': main()
