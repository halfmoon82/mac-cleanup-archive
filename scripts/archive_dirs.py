#!/usr/bin/env python3
"""Archive configured directories to NAS as tarballs, verify SHA-256; delete or keep local
copy per item.

Targets come from config (~/.config/mac-archive/config.json -> "archive_items":
[{"src": "~/Desktop/foo", "keep_local": true}]) or CLI --src (repeatable, keep-local by
default). No machine-specific paths are hardcoded.
Default dry-run; pass --execute to act.
"""
import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (CFG, HOME, NAS_MOUNT, dir_size, file_count, format_size,
                     require_nas, sha256_file, timestamp, write_manifest,
                     write_report)


def resolve_items(cli_srcs: List[str], keep_all_local: bool) -> List[Tuple[Path, bool]]:
    items: List[Tuple[Path, bool]] = []
    for it in CFG.get("archive_items") or []:
        try:
            src = Path(str(it["src"]).replace("~", str(HOME), 1)).expanduser()
            keep = bool(it.get("keep_local", False))
            if keep_all_local:
                keep = True
            items.append((src, keep))
        except Exception:
            continue
    for s in cli_srcs:
        items.append((Path(s).expanduser(), True))
    return items


def tar_archive(src: Path, tar_path: Path) -> bool:
    tar_path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["tar", "-cf", str(tar_path), "-C", str(src.parent), src.name],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"TAR FAILED for {src}: {r.stderr.strip()[:200]}")
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive configured directories to NAS")
    parser.add_argument("--execute", action="store_true", help="Actually archive (default: dry-run)")
    parser.add_argument("--src", action="append", default=[], help="Extra directory to archive (keep local). Repeatable.")
    parser.add_argument("--keep-all-local", action="store_true", help="Never delete local copies this run")
    args = parser.parse_args()
    dry_run = not args.execute
    sys.stdout.reconfigure(line_buffering=True)

    items = resolve_items(args.src, args.keep_all_local)
    if not items:
        print("No archive items configured. Add \"archive_items\" to ~/.config/mac-archive/config.json "
              "or pass --src <dir>.")
        report = write_report("archive-dirs",
                              f"# Directory Archive Report\n\n- Generated: {datetime.now().isoformat()}\n"
                              f"- Items: none configured\n")
        print(f"Report: {report}")
        return

    nas_ok = require_nas(execute=args.execute)
    ts = timestamp()
    manifests: List[Dict] = []
    total_files = 0
    total_freed = 0
    kept = 0

    for src, keep_local in items:
        if not src.exists():
            print(f"MISSING {src}, skipping")
            continue
        if not str(src).startswith(str(HOME)):
            print(f"SKIP (outside home): {src}")
            continue
        size = dir_size(src)
        files = file_count(src)
        rel = src.relative_to(HOME)
        tar_name = f"{str(rel).replace('/', '-')}-{ts}.tar"
        tar_path = NAS_MOUNT / "_tar_archives" / tar_name
        action = "KEEP local copy" if keep_local else "DELETE local copy"
        print(f"\nARCHIVING {src}")
        print(f"  files: {files}, size: {format_size(size)}")
        print(f"  -> {tar_path}")
        print(f"  -> {action}")
        if dry_run:
            print(f"  [DRY-RUN] would create tar, verify checksum, then {action.lower()}")
            continue
        if not nas_ok:
            print("ERROR: NAS not available; aborting without changes.")
            sys.exit(1)
        if not tar_archive(src, tar_path):
            continue
        print("  Verifying tar checksum...")
        tar_hash = sha256_file(tar_path)
        tar_size = tar_path.stat().st_size
        print(f"  Tar size: {format_size(tar_size)}, SHA-256: {tar_hash}")
        manifests.append({
            "src": str(src), "tar": str(tar_path), "files": files, "size": size,
            "tar_size": tar_size, "sha256": tar_hash, "keep_local": keep_local,
            "archived_at": datetime.now().isoformat(),
        })
        if keep_local:
            kept += 1
            total_files += files
            print(f"  KEPT local copy ({format_size(size)})")
            continue
        try:
            shutil.rmtree(src)
            total_freed += size
            total_files += files
            print(f"  DELETED local copy ({format_size(size)} freed)")
        except Exception as e:
            print(f"  FAILED to delete local copy: {e}")

    manifest_path = None
    if not dry_run:
        manifest_path = write_manifest("dirs", manifests)
    print(f"\n=== Done ===")
    print(f"Archived {total_files} files, freed {format_size(total_freed)}, kept {kept} local copies")
    if manifest_path:
        print(f"Manifest: {manifest_path}")
    report = write_report("archive-dirs",
                          f"# Directory Archive Report\n\n- Generated: {datetime.now().isoformat()}\n"
                          f"- NAS: {NAS_MOUNT} (mounted: {nas_ok})\n"
                          f"- Mode: {'DRY-RUN' if dry_run else 'EXECUTE'}\n"
                          f"- Archived dirs: {len(manifests)}, files: {total_files}\n"
                          f"- Freed locally: {format_size(total_freed)}\n"
                          f"- Kept local copies: {kept}\n"
                          + (f"- Manifest: {manifest_path}\n" if manifest_path else ""))
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
