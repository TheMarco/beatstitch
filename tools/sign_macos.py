"""Sign a separate, finished Mac release with Developer ID; never touch app data."""
import argparse
from pathlib import Path
import subprocess
import sys

MACHO = {b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe',
         b'\xfe\xed\xfa\xce', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
         b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


def sign(app, identity, entitlements):
    if sys.platform != 'darwin':
        raise ValueError('Developer ID signing must run on macOS.')
    app = Path(app).resolve()
    entitlements = Path(entitlements).resolve()
    main = app / 'Contents/MacOS/BeatStitch'
    if not main.is_file() or app.suffix != '.app' or identity == '-':
        raise ValueError('Provide a finished BeatStitch.app and a Developer ID identity.')
    if not entitlements.is_file():
        raise ValueError('Missing hardened-runtime entitlements.')
    files = []
    containers = []
    for path in app.rglob('*'):
        if path.is_symlink():
            continue
        if path.is_dir() and path.suffix in ('.framework', '.app', '.xpc', '.bundle'):
            containers.append(path)
        elif path.is_file():
            with path.open('rb') as stream:
                if stream.read(4) in MACHO:
                    files.append(path)
    if main not in files:
        raise ValueError('The app executable is not a native Mach-O binary.')
    base = ['codesign', '--force', '--sign', identity, '--timestamp', '--options', 'runtime']
    # Sign code inside out. Symlinks retain their layout and are not followed
    # into external folders. No --deep signing of unexamined code is used.
    for path in sorted(files, key=lambda p: (-len(p.parts), str(p))):
        command = base + (['--entitlements', str(entitlements)] if path == main else [])
        subprocess.run(command + [str(path)], check=True)
    for path in sorted(containers, key=lambda p: (-len(p.parts), str(p))):
        subprocess.run(base + [str(path)], check=True)
    subprocess.run(base + ['--entitlements', str(entitlements), str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    print(f'Signed {len(files)} native binaries and {len(containers)} nested bundles: {app}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--identity', required=True)
    parser.add_argument('--entitlements', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'assets/macos-entitlements.plist')
    args = parser.parse_args()
    sign(args.app, args.identity, args.entitlements)
