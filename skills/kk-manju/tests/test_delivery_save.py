#!/usr/bin/env python3
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import delivery_save


class DeliveryTests(unittest.TestCase):
    def manifest(self, **item):
        return {"status": "ready", "delivery": "awaiting_host_save", "fileCount": 1,
                "files": [{"filename": "剧本.txt", "inlineText": "已审核版本", **item}]}

    def test_text_saved_and_resume_verified(self):
        with tempfile.TemporaryDirectory() as root:
            one = delivery_save.save(self.manifest(), root)
            two = delivery_save.save(self.manifest(), root)
            self.assertTrue(one["ok"] and two["ok"])
            self.assertEqual(one["files"][0]["sha256"], two["files"][0]["sha256"])

    def test_path_traversal_and_duplicate_names_rejected(self):
        for name in ("../escape.txt", "/tmp/escape", "x\\evil", ".."):
            with self.assertRaises(ValueError):
                delivery_save.checked_manifest(self.manifest(filename=name))
        m = self.manifest()
        m["files"].append(dict(m["files"][0]))
        with self.assertRaises(ValueError):
            delivery_save.checked_manifest(m)

    def test_existing_unrecorded_file_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "剧本.txt"
            path.write_text("unique old file")
            result = delivery_save.save(self.manifest(), root)
            self.assertFalse(result["ok"])
            self.assertEqual(path.read_text(), "unique old file")

    def test_hash_mismatch_does_not_publish_file(self):
        with tempfile.TemporaryDirectory() as root:
            result = delivery_save.save(self.manifest(contentHash="sha256-" + "0" * 64), root)
            self.assertFalse(result["ok"])
            self.assertFalse((Path(root) / "剧本.txt").exists())

    def test_changed_saved_bytes_detected(self):
        with tempfile.TemporaryDirectory() as root:
            delivery_save.save(self.manifest(), root)
            (Path(root) / "剧本.txt").write_text("changed")
            self.assertFalse(delivery_save.save(self.manifest(), root)["ok"])

    def test_manifest_revision_cannot_overwrite_prior(self):
        with tempfile.TemporaryDirectory() as root:
            delivery_save.save(self.manifest(), root)
            with self.assertRaises(ValueError):
                delivery_save.save(self.manifest(inlineText="new revision"), root)


if __name__ == "__main__":
    unittest.main()
