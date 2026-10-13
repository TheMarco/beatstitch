import hashlib
import json
import os
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from release_source_bundle import build_source_bundle


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SourceBundleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "src"
        self.root.mkdir()
        self.files = {
            "comp-a/src.tar.gz": b"source-bytes-a",
            "comp-a/LICENSE": b"license-a",
            "comp-a/build.sh": b"build-a",
            "comp-b/src.tar.gz": b"source-bytes-b",
            "comp-b/LICENSE": b"license-b",
            "comp-b/build.sh": b"build-b",
        }
        for rel, data in self.files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        self.dest = Path(self._tmp.name) / "bundle.zip"

    def _manifest(self):
        def comp(cid, prefix):
            return {
                "id": cid, "version": "1.0", "license": "MIT",
                "source_url": f"https://example.invalid/{cid}",
                "artifacts": [
                    {"path": f"{prefix}/src.tar.gz", "sha256": _sha(self.files[f"{prefix}/src.tar.gz"]), "kind": "source_archive"},
                    {"path": f"{prefix}/LICENSE", "sha256": _sha(self.files[f"{prefix}/LICENSE"]), "kind": "license"},
                    {"path": f"{prefix}/build.sh", "sha256": _sha(self.files[f"{prefix}/build.sh"]), "kind": "build_recipe"},
                ],
            }
        return {
            "schema": 1,
            "required_components": ["comp-a"],
            "components": [comp("comp-a", "comp-a"), comp("comp-b", "comp-b")],
            "binaries": [{"path": "app.bin", "sha256": _sha(b"binary-bytes")}],
        }

    def test_success_keeps_exact_bytes_and_metadata(self):
        result = build_source_bundle(self._manifest(), self.root, self.dest)
        self.assertEqual(result["components"], 2)
        self.assertEqual(result["artifacts"], 6)
        self.assertEqual(_sha(self.dest.read_bytes()), result["sha256"])
        with zipfile.ZipFile(self.dest) as zf:
            self.assertEqual(zf.read("sources/comp-a/comp-a/src.tar.gz"), b"source-bytes-a")
            receipt = json.loads(zf.read("SOURCE_MANIFEST.json"))
        self.assertEqual(receipt["schema"], 1)
        self.assertIn("does not establish legal clearance", receipt["note"])
        self.assertEqual(receipt["binaries"], [{"path": "app.bin", "sha256": _sha(b"binary-bytes")}])
        comp = receipt["components"][0]
        self.assertEqual((comp["license"], comp["version"]), ("MIT", "1.0"))
        art = comp["artifacts"][0]
        self.assertEqual((art["kind"], art["size"]), ("source_archive", len(b"source-bytes-a")))
        self.assertNotIn(str(self.root), json.dumps(receipt))

    def test_bad_digest_fails_without_destination(self):
        m = self._manifest()
        m["components"][0]["artifacts"][0]["sha256"] = "0" * 64
        with self.assertRaises(Exception):
            build_source_bundle(m, self.root, self.dest)
        self.assertFalse(self.dest.exists())

    def test_missing_required_component_fails(self):
        m = self._manifest()
        m["required_components"] = ["nope"]
        with self.assertRaises(Exception):
            build_source_bundle(m, self.root, self.dest)
        self.assertFalse(self.dest.exists())

    def test_missing_required_kind_fails(self):
        m = self._manifest()
        m["components"][0]["artifacts"] = [
            a for a in m["components"][0]["artifacts"] if a["kind"] != "license"
        ]
        with self.assertRaises(Exception):
            build_source_bundle(m, self.root, self.dest)
        self.assertFalse(self.dest.exists())

    def test_duplicate_ids_and_traversal_fail(self):
        m = self._manifest()
        m["components"][1]["id"] = "comp-a"
        with self.assertRaises(Exception):
            build_source_bundle(m, self.root, self.dest)
        m = self._manifest()
        m["components"][0]["artifacts"][0]["path"] = "../escape"
        with self.assertRaises(Exception):
            build_source_bundle(m, self.root, self.dest)
        self.assertFalse(self.dest.exists())

    def test_symlink_path_fails(self):
        if not hasattr(os, "symlink"):
            self.skipTest("no symlink support")
        link = self.root / "comp-a" / "linked"
        try:
            link.symlink_to(self.root / "comp-a" / "src.tar.gz")
        except OSError:
            self.skipTest("cannot create symlink")
        m = self._manifest()
        m["components"][0]["artifacts"].append(
            {"path": "comp-a/linked", "sha256": _sha(b"source-bytes-a"), "kind": "patch"}
        )
        with self.assertRaises(Exception):
            build_source_bundle(m, self.root, self.dest)
        self.assertFalse(self.dest.exists())

    def test_existing_destination_stays_intact(self):
        self.dest.write_bytes(b"keep-me")
        with self.assertRaises(Exception):
            build_source_bundle(self._manifest(), self.root, self.dest)
        self.assertEqual(self.dest.read_bytes(), b"keep-me")

    def test_source_change_after_initial_verification_is_not_published(self):
        from release_source_bundle import _hash_file
        source = self.root / 'comp-a/src.tar.gz'
        def read_and_change(path):
            result = _hash_file(path)
            if path == source:
                source.write_bytes(b'changed source')
            return result
        with patch('release_source_bundle._hash_file', side_effect=read_and_change):
            with self.assertRaisesRegex(ValueError, 'changed during packaging'):
                build_source_bundle(self._manifest(), self.root, self.dest)
        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()
