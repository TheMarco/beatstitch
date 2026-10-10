#!/usr/bin/env python3
"""Build on the target OS: macOS .app, Windows .exe folder, or Linux executable folder."""
import os
import argparse
from pathlib import Path
import subprocess
import sys

import djmix


def main():
    root=Path(__file__).resolve().parent
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist-dir',type=Path,default=root/'dist')
    destination=parser.parse_args().dist_dir.resolve()
    djmix.require_tools()
    args=[sys.executable,'-m','PyInstaller','--noconfirm','--windowed','--onedir',
          '--name','DJ Studio','--distpath',str(destination),'--workpath',str(root/'build'),
          '--specpath',str(root/'build'),'--collect-all','librosa','--collect-data','scipy',
          '--exclude-module','PySide6.QtWebEngineCore','--exclude-module','PySide6.QtWebEngineWidgets',
          '--exclude-module','matplotlib','--exclude-module','tkinter',
          '--add-data',str(root/'README.md')+':.']
    if sys.platform=='darwin':
        args+=['--osx-bundle-identifier','local.djstudio.app']
    for name in ('ffmpeg','ffprobe','rubberband'):
        args+=['--add-binary',djmix.tool_path(name)+':bin']
    args.append(str(root/'desktop_app.py'))
    environment=os.environ.copy()
    environment.setdefault('PYINSTALLER_CONFIG_DIR',str(root/'build'/'pyinstaller-cache'))
    subprocess.run(args,check=True,cwd=root,env=environment)


if __name__=='__main__':main()
