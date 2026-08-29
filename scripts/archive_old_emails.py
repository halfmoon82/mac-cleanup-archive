#!/usr/bin/env python3
"""Archive Apple Mail emails before a cutoff date to NAS, verify SHA-256, delete locally,
and rebuild the Mail envelope index.

Portable: mail root, cutoff date, and NAS mount resolved via _common (env/config).
Cutoff default: 2025-01-01 local time; override with MAC_ARCHIVE_EMAIL_CUTOFF=YYYY-MM-DD.
Default dry-run; pass --execute to act. Execute mode quits Mail first.
"""
import argparse
import email.utils
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (CFG, HOME, MAIL_ROOT, NAS_MOUNT, format_size, require_nas,
                     sha256_file, timestamp, write_manifest, write_report)

MAX_HEADER_READ = 65536


def resolve_cutoff() -> datetime:
    raw = os.environ.get("MAC_ARCHIVE_EMAIL_CUTOFF") or CFG.get("email_cutoff") or "2025-01-01"
    d = datetime.strptime(str(raw)[:10], "%Y-%m-%d")
    return d.replace(tzinfo=datetime.now().astimezone().tzinfo)


CUTOFF_DATE = resolve_cutoff()


def quit_mail(timeout: int = 15) -> bool:
    r = subprocess.run(["pgrep", "-x", "Mail"], capture_output=True, text=True)
    if r.returncode != 0:
        return True
    print("Mail is running. Sending quit...")
    subprocess.run(["osascript", "-e", 'tell application "Mail" to quit'],
                   capture_output=True, text=True)
    for _ in range(timeout * 2):
        r = subprocess.run(["pgrep", "-x", "Mail"], capture_output=True, text=True)
        if r.returncode != 0:
            return True
        time.sleep(0.5)
    print("ERROR: Mail did not quit in time. Close it manually and rerun.")
    return False


def parse_emlx_date(path: Path) -> Optional[datetime]:
    try:
        with open(path, "rb") as f:
            f.readline()
            header_bytes = f.read(MAX_HEADER_READ)
    except Exception:
        return None
    text = header_bytes.decode("latin-1", errors="ignore")
    blank = text.find("\n\n")
    if blank == -1:
        blank = text.find("\r\n\r\n")
        blank = len(text) if blank == -1 else blank + 2
    headers = text[:blank]
    headers = re.sub(r"\r?\n[ \t]+", " ", headers)
    m = re.search(r"^Date:\s*(.+)$", headers, re.MULTILINE | re.IGNORECASE)
    if not m:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(m.group(1).strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def find_old_emails() -> Tuple[List[Path], int, int]:
    old: List[Path] = []
    undated = 0
    total = 0
    if not MAIL_ROOT.exists():
        return old, total, undated
    for p in MAIL_ROOT.rglob("*.emlx"):
        total += 1
        dt = parse_emlx_date(p)
        if dt is None:
            undated += 1
            continue
        if dt < CUTOFF_DATE:
            old.append(p)
    return old, total, undated


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


def rebuild_mail_index() -> int:
    removed = 0
    for p in MAIL_ROOT.rglob("Envelope Index*"):
        if p.is_file():
            try:
                p.unlink()
                removed += 1
                print(f"  Rebuild index: removed {p}")
            except Exception as e:
                print(f"  WARNING: could not remove {p}: {e}")
    return removed


def remove_empty_dirs(root: Path) -> None:
    for dirpath, _dirnames, _filenames in os.walk(str(root), topdown=False):
        d = Path(dirpath)
        try:
            if not any(d.iterdir()):
                d.rmdir()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive Apple Mail emails older than the cutoff to NAS")
    parser.add_argument("--execute", action="store_true", help="Actually archive and delete (default: dry-run)")
    parser.add_argument("--no-rebuild-index", action="store_true", help="Do not remove Mail Envelope Index files")
    args = parser.parse_args()
    dry_run = not args.execute
    sys.stdout.reconfigure(line_buffering=True)

    nas_ok = require_nas(execute=args.execute)
    old, total, undated = find_old_emails()
    old_size = sum(p.stat().st_size for p in old if p.exists()) if old else 0
    print(f"Scanned {total} .emlx files under {MAIL_ROOT}")
    print(f"Older than {CUTOFF_DATE.date()}: {len(old)} files ({format_size(old_size)})")
    print(f"Undated / unparseable: {undated}")

    if not old:
        write_report("old-emails",
                     f"# Old Email Archive Report\n\n- Generated: {datetime.now().isoformat()}\n"
                     f"- Cutoff: {CUTOFF_DATE.date()}\n- Mail root: {MAIL_ROOT}\n"
                     f"- Mode: {'DRY-RUN' if dry_run else 'EXECUTE'}\n- Older than cutoff: 0\n")
        print("No old emails to archive.")
        return

    ts = timestamp()
    cutoff_str = CUTOFF_DATE.strftime("%Y%m%d")
    tar_name = f"old-emails-before-{cutoff_str}-{ts}.tar.gz"
    local_tar = Path(tempfile.gettempdir()) / tar_name
    nas_tar = NAS_MOUNT / "_mail_archives" / tar_name

    if dry_run:
        print(f"[DRY-RUN] Would tar -> {nas_tar}, verify checksum, delete {len(old)} local emails, rebuild index.")
        report = write_report("old-emails",
                              f"# Old Email Archive Report (DRY-RUN)\n\n- Generated: {datetime.now().isoformat()}\n"
                              f"- Cutoff: {CUTOFF_DATE.date()}\n- Mail root: {MAIL_ROOT}\n"
                              f"- NAS: {NAS_MOUNT} (mounted: {nas_ok})\n"
                              f"- Scanned: {total}, older than cutoff: {len(old)} ({format_size(old_size)})\n"
                              f"- Undated: {undated}\n")
        print(f"Report: {report}")
        return

    if not nas_ok:
        print("ERROR: NAS required but not available; aborting without changes.")
        sys.exit(1)
    if not quit_mail():
        sys.exit(1)

    print(f"Creating local tar: {local_tar}")
    if not tar_files(old, local_tar):
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
        print("CHECKSUM MISMATCH; not deleting local emails")
        sys.exit(1)
    print("Checksums match.")

    deleted, failed = 0, 0
    print(f"Deleting {len(old)} local email files...")
    for p in old:
        try:
            p.unlink()
            deleted += 1
        except Exception as e:
            failed += 1
            print(f"FAILED to delete {p}: {e}")
    local_tar.unlink()

    index_removed = 0
    if not args.no_rebuild_index:
        print("Removing Mail Envelope Index files so Mail.app rebuilds on launch...")
        index_removed = rebuild_mail_index()

    print("Removing empty directories under Mail...")
    remove_empty_dirs(MAIL_ROOT)

    manifest = write_manifest("old-emails", [{
        "tar": str(nas_tar), "tar_size": nas_tar.stat().st_size, "sha256": nas_hash,
        "total": total, "old_count": len(old), "old_size": old_size, "undated": undated,
        "deleted": deleted, "failed": failed, "index_removed": index_removed,
        "archived_at": datetime.now().isoformat(),
    }])
    report = write_report("old-emails",
                          f"# Old Email Archive Report\n\n- Generated: {datetime.now().isoformat()}\n"
                          f"- Cutoff: {CUTOFF_DATE.date()}\n- Mail root: {MAIL_ROOT}\n- NAS: {NAS_MOUNT}\n"
                          f"- Tar: {nas_tar} ({format_size(nas_tar.stat().st_size)})\n"
                          f"- SHA-256: `{nas_hash}`\n"
                          f"- Deleted: {deleted}, failed: {failed}, index files removed: {index_removed}\n"
                          f"- Manifest: {manifest}\n\n"
                          f"**Please restart Mail.app to rebuild the mailbox index.**\n")
    print(f"Manifest: {manifest}")
    print(f"Report: {report}")
    print("Done. Please restart Mail.app to rebuild the index.")


if __name__ == "__main__":
    main()
