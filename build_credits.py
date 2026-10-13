"""Collect offline credits from the runtime dependency graph and installed notices."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import subprocess
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent
CORE = ('numpy', 'filelock', 'PySide6', 'librosa', 'beat-this', 'torch', 'torchaudio')
OPTIONAL = {
    'Optional: section analysis': ('transformers', 'x-transformers', 'safetensors', 'PyYAML', 'easydict'),
    'Optional: source separation': (),
}


def read_notice(path, title=None):
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return None
    return {'title': title or Path(path).name, 'text': text}


def license_file(path):
    return bool(re.match(r'(?i)(licen[cs]e|copying|copyright|notice|ofl)([._-]|$)', path.name))


def unique_notices(notices):
    result, seen = [], set()
    for notice in notices:
        if not notice:
            continue
        digest = hashlib.sha256(notice['text'].encode()).hexdigest()
        if digest not in seen:
            seen.add(digest)
            result.append(notice)
    return result


def package_entry(dist, category):
    info = dist.metadata
    classifiers = [c.split(' :: ')[-1] for c in info.get_all('Classifier', []) if c.startswith('License ::')]
    license_name = info.get('License-Expression') or ', '.join(classifiers)
    field = info.get('License', '')
    if not license_name:
        license_name = field if len(field) < 180 else 'See bundled notices'
    if license_name in ('', 'UNKNOWN'):
        license_name = 'See project license and bundled notices'
    urls = []
    for value in info.get_all('Project-URL', []):
        _, _, url = value.partition(',')
        if url.strip().startswith('https://'):
            urls.append(url.strip())
    home = info.get('Home-page', '')
    if home.startswith(('https://', 'http://')):
        urls.insert(0, home)
    notices = []
    for path in dist.files or []:
        if license_file(Path(path)) and not str(path).endswith(('.py', '.pyc', '.so')):
            notices.append(read_notice(dist.locate_file(path), str(path)))
    if len(field) > 180:
        notices.append({'title': 'License from package metadata', 'text': field})
    supplement = ROOT / 'vendor/python-notices' / canonicalize_name(info['Name'])
    notices += [read_notice(p, str(p.relative_to(ROOT))) for p in sorted(supplement.rglob('*'))
                if p.is_file() and license_file(p)]
    if canonicalize_name(info['Name']) in ('pyside6', 'pyside6-essentials', 'pyside6-addons', 'shiboken6'):
        notices += [read_notice(p, str(p.relative_to(ROOT))) for p in sorted((ROOT/'vendor/qt-python').rglob('*'))
                    if p.is_file() and (license_file(p) or p.name == 'ABOUT.txt')]
        license_name = 'GPL-3.0 / LGPL-3.0 and upstream exceptions; see component notices'
    author = info.get('Author') or info.get('Maintainer') or f"{info['Name']} contributors"
    # Metadata sometimes puts a full license or an email list in author fields.
    if len(author) > 250:
        author = f"{info['Name']} contributors"
    return {'id': 'python:' + canonicalize_name(info['Name']), 'name': info['Name'],
            'version': dist.version, 'category': category, 'credit': author,
            'purpose': info.get('Summary', 'Python runtime dependency'),
            'license': license_name, 'urls': list(dict.fromkeys(urls)),
            'notices': unique_notices(notices)}


def python_entries(roots, category, seen):
    pending = [(name, frozenset()) for name in roots]
    visited = set()
    result = []
    while pending:
        name, extras = pending.pop(0)
        key = canonicalize_name(name)
        if (key, extras) in visited:
            continue
        visited.add((key, extras))
        dist = metadata.distribution(name)  # A missing required dependency must fail the build.
        if key not in seen:
            result.append(package_entry(dist, category))
            seen.add(key)
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker is None or any(req.marker.evaluate({'extra': extra}) for extra in ('', *extras)):
                pending.append((req.name, frozenset(req.extras)))
    return result


def add_packaged_credits(destination, executable):
    """A packager can collect optional imports absent from dependency metadata."""
    from PyInstaller.archive.readers import CArchiveReader
    destination = Path(destination)
    archive = CArchiveReader(str(executable)).open_embedded_archive('PYZ.pyz')
    roots = {name.split('.')[0] for name in archive.toc}
    mapping = metadata.packages_distributions()
    distributions = {name for root in roots for name in mapping.get(root, [])}
    catalog = json.loads((destination / 'catalog.json').read_text())
    seen = {entry['id'].removeprefix('python:') for entry in catalog['entries']
            if entry['id'].startswith('python:')}
    # These local research packages are already credited with their vendored
    # source/model notices; do not substitute incomplete wheel metadata.
    seen.update(('edm98', 'muq'))
    for name in sorted(distributions):
        if canonicalize_name(name) not in seen:
            catalog['entries'].append(package_entry(metadata.distribution(name), 'Bundled support libraries'))
            seen.add(canonicalize_name(name))
    catalog['entries'].sort(key=lambda entry: (entry['category'], entry['name'].casefold()))
    (destination / 'catalog.json').write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + '\n')
    return catalog


def native_entries():
    """Credit actual Homebrew-linked libraries, rather than every installed formula."""
    import djmix
    tools = [Path(djmix.tool_path(name)).resolve() for name in ('ffmpeg', 'ffprobe', 'rubberband')]
    if sys.platform != 'darwin':
        # Windows tool installations carry sidecar notices; Linux distributions
        # retain package notices in /usr/share/doc. Curated tool credits always exist.
        result = []
        roots = {p.parent.parent / 'licenses' for p in tools}
        if sys.platform == 'linux':
            roots.update(Path('/usr/share/doc') / name for name in ('ffmpeg', 'rubberband-cli', 'librubberband2', 'libsndfile1'))
        for folder in sorted(roots):
            if folder.is_dir():
                notices = unique_notices(read_notice(p, str(p.relative_to(folder))) for p in folder.rglob('*') if p.is_file())
                if notices:
                    result.append({'id': 'native-notices:' + folder.name, 'name': folder.name,
                                   'category': 'Native audio libraries', 'credit': 'See individual copyright notices',
                                   'purpose': 'Audio tool distribution notices', 'license': 'See bundled notices',
                                   'notices': notices, 'urls': []})
        return result
    pending = tools + [Path(sys.executable).resolve()]
    seen, formulae = set(), {}
    while pending:
        binary = pending.pop()
        if binary in seen or not binary.is_file():
            continue
        seen.add(binary)
        parts = binary.parts
        if 'Cellar' in parts:
            index = parts.index('Cellar')
            prefix = Path(*parts[:index+3])
            formulae[prefix] = parts[index+1]
        process = subprocess.run(['/usr/bin/otool', '-L', str(binary)], capture_output=True, text=True, timeout=15)
        for line in process.stdout.splitlines()[1:]:
            value = line.strip().split(' (', 1)[0]
            if value.startswith(('/opt/homebrew/', '/usr/local/')):
                pending.append(Path(value).resolve())
    entries = []
    for prefix, name in sorted(formulae.items()):
        source = prefix / '.brew' / (name + '.rb')
        formula = source.read_text() if source.is_file() else ''
        homepage = re.search(r'^\s*homepage\s+"([^"]+)"', formula, re.M)
        licenses = re.search(r'^\s*license\s+(.+)$', formula, re.M)
        notices = [read_notice(p) for p in prefix.iterdir() if p.is_file() and license_file(p)]
        docs = prefix / 'share/doc'
        if docs.is_dir():
            notices += [read_notice(p, str(p.relative_to(prefix))) for p in docs.rglob('*') if p.is_file() and license_file(p)]
        entries.append({'id': 'native:' + name, 'name': name, 'version': prefix.name,
                        'category': 'Native audio and runtime libraries', 'credit': name + ' contributors',
                        'purpose': 'Linked by the audio tools or Python runtime in this build',
                        'license': ('See bundled component notices and original build formula'
                                    if licenses and licenses.group(1).strip().endswith('[')
                                    else licenses.group(1).strip().strip('"') if licenses else 'See project license'),
                        'urls': [homepage.group(1)] if homepage else [], 'notices': unique_notices(notices)})
    return entries


def build_catalog(destination, *, with_sections=False, with_separation=False):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    curated = json.loads((ROOT / 'assets/about/projects.json').read_text())
    entries = []
    for item in curated:
        item = dict(item)
        if sys.platform not in item.pop('platforms', [sys.platform]):
            continue
        notices = []
        for pattern in item.pop('notice_files', []):
            for path in sorted(ROOT.glob(pattern)):
                if path.is_file() and path.suffix not in ('.py', '.pyc', '.sh'):
                    notices.append(read_notice(path, str(path.relative_to(ROOT))))
        item['notices'] = unique_notices(notices)
        entries.append(item)
    seen = set()
    entries += python_entries(CORE, 'Python runtime libraries', seen)
    if with_sections:
        entries += python_entries(OPTIONAL['Optional: section analysis'], 'Optional: section analysis', seen)
    if with_separation:
        entries += python_entries(OPTIONAL['Optional: source separation'], 'Optional: source separation', seen)
    entries += python_entries(('PyInstaller',), 'Build tools', seen)
    for native in native_entries():
        match = {'ffmpeg': 'ffmpeg', 'rubberband': 'rubber-band'}.get(native['name'])
        if match:
            item = next(e for e in entries if e['id'] == match)
            item['version'] = native.get('version', '')
            item['license'] = native['license']
            item['notices'] = unique_notices(item['notices'] + native['notices'])
        else:
            entries.append(native)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        next(e for e in entries if e['id'] == 'python')['notices'].append(read_notice(python_license))
    catalog = {'schema': 1, 'creator': 'Marco van Hylckama Vlieg',
               'platform': sys.platform, 'python': sys.version.split()[0],
               'scope': 'Runtime dependencies for this build; optional feature projects and build tools are labeled separately. '
                        'Bundled notices also credit upstream components inside these libraries.',
               'entries': sorted(entries, key=lambda e: (e['category'], e['name'].casefold()))}
    (destination / 'catalog.json').write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + '\n')
    for name in ('beatstitch.png', 'beatstitch-icon.png', 'beatstitch.icns', 'beatstitch.ico'):
        source = ROOT / 'assets/about' / name
        if source.is_file() and source.resolve() != (destination / name).resolve():
            (destination / name).write_bytes(source.read_bytes())
    for name in ('beatstitch.svg', 'beatstitch-icon.svg'):
        (destination / name).unlink(missing_ok=True)
    return catalog


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'assets/about')
    parser.add_argument('--with-sections', action='store_true')
    parser.add_argument('--with-separation', action='store_true')
    args = parser.parse_args()
    catalog = build_catalog(args.output, with_sections=args.with_sections, with_separation=args.with_separation)
    print(f"Collected {len(catalog['entries'])} credits in {args.output}")
