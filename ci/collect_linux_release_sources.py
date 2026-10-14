"""Collect source packages for the Ubuntu libraries in an existing release.

This does not rebuild or change the application. ELF build IDs must match an
installed Ubuntu library before its dpkg source identity is used. Unmatched
libraries remain explicit gaps; collecting archives is not legal clearance.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import urllib.parse
import urllib.request


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def build_id(path):
    result = command('readelf', '-n', str(path))
    match = re.search(r'Build ID: ([0-9a-f]+)', result)
    return match[1] if match else None


def get_json(url):
    with urllib.request.urlopen(url, timeout=90) as response:
        return json.load(response)


def source_files(name, version):
    query = urllib.parse.urlencode({
        'ws.op': 'getPublishedSources', 'source_name': name,
        'version': version, 'exact_match': 'true',
        'distro_series': 'https://api.launchpad.net/1.0/ubuntu/noble',
    })
    result = get_json('https://api.launchpad.net/1.0/ubuntu/+archive/primary?' + query)
    entries = result['entries']
    if not entries:
        raise ValueError(f'No exact Ubuntu source publication: {name} {version}')
    entry = entries[0]
    if entry['source_package_name'] != name or entry['source_package_version'] != version:
        raise ValueError('Source publication identity differs from requested version')
    return entry['self_link'], get_json(entry['self_link'] + '?ws.op=sourceFileUrls')


def collect_source(identity, destination):
    name, version = identity
    publication, urls = source_files(name, version)
    folder = destination / (name + '-' + version.replace(':', '_'))
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for url in urls:
        filename = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit('/', 1)[-1])
        if not filename or Path(filename).name != filename:
            raise ValueError('Unsafe source archive name')
        target = folder / filename
        digest, count = hashlib.sha256(), 0
        with urllib.request.urlopen(url, timeout=180) as response, target.open('xb') as output:
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > 1024**3:
                    raise ValueError('Source file exceeds 1 GiB limit')
                output.write(chunk); digest.update(chunk)
        files.append({'file': target.name, 'url': url, 'bytes': count, 'sha256': digest.hexdigest()})
    dsc = next((folder / f['file'] for f in files if f['file'].endswith('.dsc')), None)
    if dsc is None:
        raise ValueError('Source publication has no .dsc manifest')
    checksums = re.search(r'(?m)^Checksums-Sha256:\n((?: [^\n]+\n)+)', dsc.read_text())
    if not checksums:
        raise ValueError('Source manifest has no SHA256 checksums')
    by_name = {f['file']: f for f in files}
    for line in checksums[1].splitlines():
        sha, size, filename = line.split()
        actual = by_name.get(filename)
        if actual is None or actual['sha256'] != sha or actual['bytes'] != int(size):
            raise ValueError(f'Source checksum mismatch: {filename}')
    receipt = {'name': name, 'version': version, 'publication': publication, 'files': files}
    (folder / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print('Collected', name, version, flush=True)
    return receipt


def collect(archive, output, expected_sha):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected_sha:
        raise ValueError('Release archive SHA256 differs from the verified release')
    output.mkdir(parents=True, exist_ok=True)
    binaries, matched, unmatched = [], [], []
    native = output / 'inventory-input'; native.mkdir(exist_ok=True)
    with tarfile.open(archive) as tar:
        for member in tar:
            path = Path(member.name)
            if not member.isfile() or (path.parent.as_posix() != 'BeatStitch/_internal' and 'quadmath' not in path.name):
                continue
            if not path.name.startswith('lib') or '.so' not in path.name:
                continue
            if member.size > 512 * 1024**2:
                raise ValueError('Unexpected native-library size')
            target = native / path.name
            data = tar.extractfile(member).read(); target.write_bytes(data)
            ident = build_id(target)
            record = {'path': member.name, 'sha256': hashlib.sha256(data).hexdigest(), 'build_id': ident}
            binaries.append(record)
            if path.name == 'libpython3.13.so.1.0' and b'3.13.5' in data:
                record.update({'common_component': 'CPython 3.13.5',
                               'source_url': 'https://www.python.org/ftp/python/3.13.5/Python-3.13.5.tgz'})
                continue  # Matching source is in the common/Mac source companion.
            candidate_name = 'libquadmath.so.0' if 'quadmath' in path.name else path.name
            candidates = list(Path('/usr/lib/x86_64-linux-gnu').rglob(candidate_name))
            found = False
            for candidate in candidates:
                if not candidate.is_file() or not ident or build_id(candidate) != ident:
                    continue
                real = candidate.resolve()
                owner = command('dpkg-query', '-S', str(real)).split(': /', 1)[0].splitlines()[0]
                source, version = command('dpkg-query', '-W', '-f=${source:Package}\t${source:Version}', owner).strip().split('\t')
                record.update({'package': owner, 'source': source, 'source_version': version})
                matched.append(record); found = True; break
            if not found:
                unmatched.append(record)
    inventory = {'release_sha256': expected_sha, 'matched': matched, 'unmatched': unmatched,
                 'common_sources': [x for x in binaries if 'common_component' in x]}
    (output / 'native-inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
    identities = sorted({(x['source'], x['source_version']) for x in matched})
    print(len(matched), 'matched libraries;', len(unmatched), 'explicit gaps;', len(identities), 'source packages', flush=True)
    failures, receipts = [], []
    def run(identity):
        try:
            return collect_source(identity, output / 'ubuntu-sources'), None
        except Exception as error:
            return None, {'source': identity, 'error': str(error)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for receipt, error in pool.map(run, identities):
            if receipt: receipts.append(receipt)
            if error: failures.append(error)
    (output / 'source-collection.json').write_text(json.dumps({'sources': receipts, 'failures': failures}, indent=2) + '\n')
    # These copied binaries are solely for inventory; the source artifact excludes
    # them so that it never duplicates a binary release or implies source content.
    for path in native.iterdir(): path.unlink()
    native.rmdir()
    if unmatched or failures:
        raise ValueError(f'Coverage needs review: {len(unmatched)} unmatched libraries, {len(failures)} failed source collections')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    collect(args.archive, args.output, args.sha256)


if __name__ == '__main__': main()
