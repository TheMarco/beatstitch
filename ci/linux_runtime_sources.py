"""Use a source-identifiable Ubuntu libquadmath in a Linux frozen package.

Scientific wheels can contain stripped libquadmath copies with no exact source
provenance. Replace their dynamic ABI with Ubuntu's library, retaining the
consumer filenames. Verify exported symbol versions before changing any file.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess


def command(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def exports(text):
    symbols = set()
    for line in text.splitlines():
        fields = line.split()
        if (len(fields) >= 8 and fields[3] in ('FUNC', 'OBJECT') and
                fields[4] in ('GLOBAL', 'WEAK') and fields[6] != 'UND'):
            name = fields[7].replace('@@', '@')
            if name not in ('_init', '_fini'): symbols.add(name)
    return symbols


def elf_machine(path):
    with path.open('rb') as handle: header = handle.read(20)
    if len(header) != 20 or header[:6] != b'\x7fELF\x02\x01':
        raise ValueError('Expected a little-endian ELF64 shared library')
    return struct.unpack('<H', header[18:20])[0]


def replace_quadmath(root):
    root = Path(root).resolve()
    targets = sorted(p for p in root.rglob('*quadmath*.so*') if p.is_file() and not p.is_symlink())
    if not targets: return None
    listing = command('ldconfig', '-p')
    candidates = re.findall(r'^\s*libquadmath\.so\.0\s+\(libc6,x86-64\)\s+=>\s+(\S+)', listing, re.M)
    if not candidates:
        raise ValueError('Install Ubuntu libquadmath0 before packaging scientific runtimes')
    provider = Path(candidates[0]).resolve()
    available = exports(command('readelf', '--wide', '--dyn-syms', str(provider)))
    if not available: raise ValueError('Replacement libquadmath exports could not be verified')
    architecture = elf_machine(provider)
    for target in targets:
        if root not in target.resolve().parents or elf_machine(target) != architecture:
            raise ValueError('Runtime target escapes the package or has a different architecture')
        needed = exports(command('readelf', '--wide', '--dyn-syms', str(target)))
        missing = needed - available
        if missing:
            raise ValueError('Replacement libquadmath lacks required exports: ' + ', '.join(sorted(missing)))
    owner = command('dpkg-query', '-S', str(provider)).split(': /', 1)[0].strip()
    source, version = command('dpkg-query', '-W', '-f=${source:Package}\t${source:Version}', owner).strip().split('\t')
    data = provider.read_bytes(); digest = hashlib.sha256(data).hexdigest()
    records = []
    for target in targets:
        records.append({'path': target.relative_to(root).as_posix(),
                        'original_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                        'sha256': digest})
        shutil.copy2(provider, target)
    receipt = {'schema': 1, 'library': 'libquadmath', 'package': owner,
               'source_package': source, 'source_version': version,
               'source_archive': 'https://launchpad.net/ubuntu/+source/' + source + '/' + version,
               'provider_sha256': digest, 'elf_machine': architecture, 'replaced': records,
               'recipe': 'Install the recorded Ubuntu libquadmath0 package; run ci/linux_runtime_sources.py '
                         'after PyInstaller. Existing consumer filenames are retained and their versioned exports '
                         'are checked against the provider before replacement.'}
    (root / 'quadmath-runtime-source.json').write_text(json.dumps(receipt, indent=2) + '\n')
    notices = root / 'licenses/runtime-libraries'; notices.mkdir(parents=True, exist_ok=True)
    copyright_file = Path('/usr/share/doc') / owner.split(':')[0] / 'copyright'
    if not copyright_file.is_file(): raise ValueError('Missing Ubuntu runtime copyright notice')
    shutil.copy2(copyright_file, notices / 'libquadmath-copyright.txt')
    catalog_path = root / 'assets/about/catalog.json'
    catalog = json.loads(catalog_path.read_text())
    catalog['entries'].append({
        'id': 'native:ubuntu-libquadmath', 'name': 'GNU libquadmath (Ubuntu runtime)',
        'version': version, 'category': 'Native mathematical runtime',
        'credit': 'Free Software Foundation and GCC contributors',
        'purpose': 'Quad-precision support used by the scientific audio-analysis libraries.',
        'license': 'LGPL-2.1-or-later; see original Ubuntu copyright notices',
        'urls': [receipt['source_archive'], 'https://github.com/TheMarco/beatstitch/releases/tag/v0.1.2'],
        'notices': [{'title': 'Ubuntu libquadmath copyright', 'text': copyright_file.read_text()}],
    })
    catalog_path.write_text(json.dumps(catalog, indent=2) + '\n')
    print('Source-identifiable libquadmath:', source, version, '; replaced', len(targets), 'verified ABI copies')
    return receipt
