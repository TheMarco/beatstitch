"""Package the small native loop trial, without any inference models."""
import os
from pathlib import Path
import subprocess
import sys

import djmix


def main():
    root = Path(__file__).resolve().parent
    example = root / 'outputs/drum-loop-trial'
    for name in ('Trial source.djmix', 'example-report.json'):
        if not (example / name).is_file(): raise SystemExit('Prepare the real-song loop example before packaging.')
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--onedir',
            '--name', 'DJ Drum Loop Trial', '--distpath', str(example / 'dist'),
            '--workpath', str(root / 'build/loop-trial'), '--specpath', str(root / 'build/loop-trial'),
            '--add-data', str(root / 'assets/fonts') + ':assets/fonts']
    # The trial uses saved grids/content labels and the existing PCM transport.
    # None of the analysis or full-project rendering entry points are invoked.
    for module in ('torch', 'torchaudio', 'transformers', 'librosa', 'numba', 'matplotlib',
                   'tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets'):
        args += ['--exclude-module', module]
    for name in ('Trial source.djmix', 'example-report.json'):
        args += ['--add-data', str(example / name) + ':example']
    for name in ('ffmpeg', 'ffprobe'):
        path = djmix.tool_path(name)
        if not path: raise SystemExit('Install ' + name + ' before packaging the trial.')
        args += ['--add-binary', path + ':bin']
    if sys.platform == 'darwin': args += ['--osx-bundle-identifier', 'local.djstudio.drumlooptrial']
    args.append(str(root / 'prototype_drum_loop.py'))
    environment = os.environ.copy()
    environment.setdefault('PYINSTALLER_CONFIG_DIR', str(root / 'build/pyinstaller-cache'))
    subprocess.run(args, cwd=root, env=environment, check=True)


if __name__ == '__main__': main()
