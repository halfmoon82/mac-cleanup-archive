#!/usr/bin/env python3
"""Archive Mail attachments older than N days to NAS, verify SHA-256, then delete locally.

Portable: mail root and NAS mount are resolved via _common (env/config), no hardcoded paths.
Default dry-run; pass --execute to act.
"""
import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (AGE_DAYS, HOME, MAIL_ROOT, NAS_MOUNT, format_size,
                     require_nas, sha256_file, timestamp, write_manifest,
                     write_report)


def find_old_attachments() -> List[Path]:
    if not MAIL_ROOT.exists():
        return []
    cmd = ["find", str(MAIL_ROOT), "-type", "f",
           "-path", "*Attachments*", "-mtime", f"+{AGE_DAYS}", "-print"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print(f"find failed: {r.stderr.strip()[:200]}")
        return []
    return [Path(line) for line in r.stdout.splitlines() if line.strip()]


def tar_files(paths: List[Path], tar_path: Path) -> bool:
    tar_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as f:
        for p in paths:
            f.write(str(p) + "\n")
        list_file = f.name
    try:
        r = subprocess.run(["tar", "-czf", str(tar_path), "-T", list_file],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            print(f"TAR FAILED: {r.stderr.strip()[:200]}")
            return False
        return True
    finally:
        os.unlink(list_file)


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive Mail attachments older than N days to NAS")
    parser.add_argument("--execute", action="store_true", help="Actually archive and delete (default: dry-run)")
    args = parser.parse_args()
    dry_run = not args.execute
    sys.stdout.reconfigure(line_buffering=True)

    nas_ok = require_nas(execute=args.execute)
    paths = find_old_attachments()
    count = len(paths)
    size = sum(p.stat().st_size for p in paths if p.exists()) if paths else 0
    print(f"Found {count} Mail attachments older than {AGE_DAYS} days ({format_size(size)})")

    if not paths:
        write_report("mail-attachments",
                     f"# Mail Attachment Report\n\n- Generated: {datetime.now().isoformat()}\n"
                     f"- Mail root: {MAIL_ROOT}\n- Mode: {'DRY-RUN' if dry_run else 'EXECUTE'}\n"
                     f"- Older than {AGE_DAYS} days: 0\n")
        print("Nothing to archive.")
        return

    ts = timestamp()
    tar_name = f"mail-attachments-mtime+{AGE_DAYS}-{ts}.tar.gz"
    local_tar = Path(tempfile.gettempdir()) / tar_name
    nas_tar = NAS_MOUNT / "_mail_archives" / tar_name

    if dry_run:
        print(f"[DRY-RUN] Would tar -> {nas_tar}, verify checksum, delete {count} local attachments.")
        report = write_report("mail-attachments",
                              f"# Mail Attachment Report (DRY-RUN)\n\n- Generated: {datetime.now().isoformat()}\n"
                              f"- Mail root: {MAIL_ROOT}\n- NAS: {NAS_MOUNT} (mounted: {nas_ok})\n"
                              f"- Attachments: {count}, {format_size(size)}\n")
        print(f"Report: {report}")
        return

    if not nas_ok:
        print("ERROR: NAS required but not available; aborting without changes.")
        sys.exit(1)

    print(f"Creating local tar: {local_tar}")
    if not tar_files(paths, local_tar):
        sys.exit(1)
    print(f"Tar size: {format_size(local_tar.stat().st_size)}")

    nas_tar.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cp", str(local_tar), str(nas_tar)], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(f"CP FAILED: {r.stderr.strip()[:200]}")
        sys.exit(1)

    print("Verifying tar checksum...")
    local_hash = sha256_file(local_tar)
    nas_hash = sha256_file(nas_tar)
    print(f"Local: {local_hash}")
    print(f"NAS:   {nas_hash}")
    if local_hash != nas_hash:
        print("CHECKSUM MISMATCH; not deleting local attachments")
        sys.exit(1)
    print("Checksums match.")

    deleted, failed = 0, 0
    for p in paths:
        try:
            p.unlink()
            deleted += 1
        except Exception as e:
            failed += 1
            print(f"FAILED to delete {p}: {e}")
    local_tar.unlink()
    print(f"Deleted {deleted} local attachments ({failed} failed)")

    manifest = write_manifest("mail", [{
        "tar": str(nas_tar), "tar_size": nas_tar.stat().st_size, "sha256": nas_hash,
        "attachments": count, "attachments_size": size, "deleted": deleted, "failed": failed,
        "archived_at": datetime.now().isoformat(),
    }])
    report = write_report("mail-attachments",
                          f"# Mail Attachment Report\n\n- Generated: {datetime.now().isoformat()}\n"
                          f"- Mail root: {MAIL_ROOT}\n- NAS: {NAS_MOUNT}\n"
                          f"- Tar: {nas_tar} ({format_size(nas_tar.stat().st_size)})\n"
                          f"- SHA-256: `{nas_hash}`\n"
                          f"- Attachments: {count} ({format_size(size)}), deleted {deleted}, failed {failed}\n"
                          f"- Manifest: {manifest}\n")
    print(f"Manifest: {manifest}")
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
