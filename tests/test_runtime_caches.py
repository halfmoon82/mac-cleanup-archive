#!/usr/bin/env python3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import mac_cleanup


class RuntimeCacheScanTests(unittest.TestCase):
    def test_collects_files_and_symlink_without_following_target(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cache = base / "cache"
            cache.mkdir()
            payload = cache / "payload.bin"
            payload.write_bytes(b"cache")
            outside = base / "outside.txt"
            outside.write_text("keep me", encoding="utf-8")
            link = cache / "linked"
            link.symlink_to(outside)

            found = mac_cleanup._iter_runtime_cache_files(cache)

            self.assertIn(payload, found)
            self.assertIn(link, found)
            self.assertNotIn(outside, found)

    def test_runtime_scan_reports_symlink_itself(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cache = base / "cache"
            cache.mkdir()
            outside = base / "outside.txt"
            outside.write_text("keep me", encoding="utf-8")
            link = cache / "linked"
            link.symlink_to(outside)

            target = mac_cleanup.RuntimeCacheTarget(cache, "test cache")
            candidates = mac_cleanup.scan_runtime_cache_files([target])

            self.assertEqual([(link, "test cache", link.lstat().st_size)], candidates)
            self.assertTrue(outside.exists())

    def test_delete_file_removes_directory_symlink_not_target(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cache = base / "cache"
            cache.mkdir()
            outside = base / "outside"
            outside.mkdir()
            kept = outside / "keep.txt"
            kept.write_text("keep me", encoding="utf-8")
            link = cache / "linked-dir"
            link.symlink_to(outside, target_is_directory=True)

            self.assertTrue(mac_cleanup.delete_file(link, dry_run=False, log_file=None))

            self.assertFalse(link.exists())
            self.assertTrue(kept.exists())


if __name__ == "__main__":
    unittest.main()
