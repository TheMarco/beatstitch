"""Local source-bundle packager (verification/packaging only).

Packages already-present source artifacts into a zip with a receipt
manifest. It never extracts or executes archives and makes no legal
determination: byte/coverage validation does not establish legal clearance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

SCHEMA = 1
ALLOWED_KINDS = frozenset({"source_archive", "license", "build_recipe", "patch"})
REQUIRED_KINDS = frozenset({"source_archive", "license", "build_recipe"})
_ID_RE = re.compile(r"[A-Za-z0-9._-]+")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
NOTE = (
    "Byte and coverage validation does not establish legal clearance. "
    "This bundle records file hashes, sizes, and component metadata only; "
    "it is not a license classifier or legal determination."
)


def _fail(msg: str) -> ValueError:
    return ValueError(msg)


def _check_sha(value, what: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise _fail(f"{what}: sha256 must be 64 lowercase hex characters")
    return value


def _validate_artifact_path(raw, what: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise _fail(f"{what}: path must be a non-empty relative path")
    if "\\" in raw or PureWindowsPath(raw).drive:
        raise _fail(f"{what}: use a relative POSIX path")
    p = PurePosixPath(raw)
    if p.is_absolute() or os.path.isabs(raw):
        raise _fail(f"{what}: path must be relative")
    if ".." in p.parts:
        raise _fail(f"{what}: path must not contain '..'")
    if not p.parts or p.parts == (".",):
        raise _fail(f"{what}: path must name a file")
    return p.as_posix()


def _resolve_contained(source_root: Path, rel_posix: str) -> Path:
    """Return the on-disk path, rejecting symlinks and escapes."""
    rel = Path(rel_posix)
    cur = source_root
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            raise _fail(f"symlinked path is not allowed: {rel_posix}")
    try:
        st = cur.lstat()
    except FileNotFoundError:
        raise _fail(f"artifact file not found: {rel_posix}") from None
    if not stat.S_ISREG(st.st_mode):
        raise _fail(f"artifact is not a regular file: {rel_posix}")
    root_resolved = source_root.resolve()
    resolved = cur.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise _fail(f"artifact escapes source root: {rel_posix}")
    return cur


def _hash_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def build_source_bundle(manifest: dict, source_root: Path, destination: Path) -> dict:
    source_root = Path(source_root)
    destination = Path(destination)
    if not isinstance(manifest, dict):
        raise _fail("manifest must be an object")
    if manifest.get('schema') != SCHEMA:
        raise _fail('manifest schema must be 1')
    if os.path.lexists(destination):
        raise FileExistsError(f"destination already exists: {destination}")
    if source_root.is_symlink() or not source_root.is_dir():
        raise _fail(f"source root is not a directory: {source_root}")

    required = manifest.get("required_components", [])
    if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
        raise _fail("required_components must be a list of component ids")
    components = manifest.get("components")
    if not isinstance(components, list) or not components:
        raise _fail("components must be a nonempty list")
    binaries = manifest.get("binaries", [])
    if not isinstance(binaries, list):
        raise _fail("binaries must be a list")
    for b in binaries:
        if not isinstance(b, dict) or not isinstance(b.get("path"), str) or not b["path"]:
            raise _fail("each binary needs a non-empty path label")
        _check_sha(b.get("sha256"), f"binary {b.get('path')!r}")
        _validate_artifact_path(b['path'], 'binary label')

    seen_ids: set[str] = set()
    # validated: list of (component_meta, [(rel_posix, kind, sha256, disk_path)])
    validated = []
    total_artifacts = 0
    for comp in components:
        if not isinstance(comp, dict):
            raise _fail("each component must be an object")
        cid = comp.get("id")
        if not isinstance(cid, str) or not _ID_RE.fullmatch(cid) or cid in (".", ".."):
            raise _fail(f"unsafe component id: {cid!r}")
        if cid in seen_ids:
            raise _fail(f"duplicate component id: {cid}")
        seen_ids.add(cid)
        for field in ("version", "license", "source_url"):
            if not isinstance(comp.get(field), str) or not comp[field].strip():
                raise _fail(f"component {cid}: {field} must be a nonempty string")
        artifacts = comp.get("artifacts")
        if not isinstance(artifacts, list):
            raise _fail(f"component {cid}: artifacts must be a list")
        kinds: set[str] = set()
        seen_paths: set[str] = set()
        entries = []
        for art in artifacts:
            if not isinstance(art, dict):
                raise _fail(f"component {cid}: each artifact must be an object")
            kind = art.get("kind")
            if kind not in ALLOWED_KINDS:
                raise _fail(f"component {cid}: unsupported artifact kind: {kind!r}")
            rel = _validate_artifact_path(art.get("path"), f"component {cid} artifact")
            if rel in seen_paths:
                raise _fail(f"component {cid}: duplicate artifact path: {rel}")
            seen_paths.add(rel)
            expected = _check_sha(art.get("sha256"), f"component {cid} artifact {rel}")
            disk = _resolve_contained(source_root, rel)
            actual, size = _hash_file(disk)
            if actual != expected:
                raise _fail(f"component {cid} artifact {rel}: sha256 mismatch")
            kinds.add(kind)
            entries.append((rel, kind, expected, disk, size))
        missing = REQUIRED_KINDS - kinds
        if missing:
            raise _fail(f"component {cid}: missing required artifact kind(s): {sorted(missing)}")
        total_artifacts += len(entries)
        validated.append((comp, entries))

    for req in required:
        if req not in seen_ids:
            raise _fail(f"missing required component: {req}")

    receipt_components = []
    for comp, entries in validated:
        cid = comp["id"]
        receipt_components.append({
            "id": cid,
            "version": comp.get("version", ""),
            "license": comp.get("license", ""),
            "source_url": comp.get("source_url", ""),
            "artifacts": [
                {
                    "path": f"sources/{cid}/{rel}",
                    "source_path": rel,
                    "kind": kind,
                    "sha256": sha,
                    "size": size,
                }
                for rel, kind, sha, _disk, size in entries
            ],
        })
    receipt = {
        "schema": SCHEMA,
        "note": NOTE,
        "required_components": list(required),
        "components": receipt_components,
        "binaries": [{"path": b["path"], "sha256": b["sha256"]} for b in binaries],
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".tmp", dir=str(destination.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as raw:
            with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_STORED) as zf:
                for comp, entries in validated:
                    cid = comp["id"]
                    for rel, _kind, expected, disk, size in entries:
                        disk = _resolve_contained(source_root, rel)
                        digest = hashlib.sha256(); written = 0
                        member = zipfile.ZipInfo(f"sources/{cid}/{rel}")
                        with open(disk, "rb") as fh, zf.open(member, 'w', force_zip64=True) as out:
                            for data in iter(lambda: fh.read(1024 * 1024), b''):
                                out.write(data); digest.update(data); written += len(data)
                        if digest.hexdigest() != expected or written != size:
                            raise _fail(f'component {cid} artifact {rel} changed during packaging')
                zf.writestr(
                    "SOURCE_MANIFEST.json",
                    json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                )
        # Publish atomically without replacing an output created concurrently.
        os.link(tmp, destination)
        tmp.unlink()
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    digest, _ = _hash_file(destination)
    return {
        "destination": str(destination),
        "sha256": digest,
        "components": len(validated),
        "artifacts": total_artifacts,
        "component_count": len(validated),
        "artifact_count": total_artifacts,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build a local source bundle.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        with open(args.manifest, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        result = build_source_bundle(manifest, Path(args.source_root), Path(args.output))
    except Exception as exc:  # noqa: BLE001 - CLI reports any failure uniformly
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
