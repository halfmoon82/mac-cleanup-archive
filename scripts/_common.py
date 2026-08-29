"""Shared helpers for mac-cleanup-archive scripts.

Portability contract:
- No absolute user paths are hardcoded; everything derives from Path.home().
- NAS mount point is discovered at runtime, never assumed.
- Config resolution order (later overrides earlier):
    1. Built-in defaults
    2. ~/.config/mac-archive/config.json  (or legacy config.sh values)
    3. Environment variables MAC_ARCHIVE_*
- Scripts that only need local reads (dry-run) must work without NAS.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HOME = Path.home()

DEFAULTS: Dict = {
    "nas_mount": str(HOME / "Volumes" / "MacArchive"),
    "nas_url": None,  # e.g. "//user@192.168.1.100/MacArchive"; never store passwords here
    "report_root": str(HOME / "Documents" / "MacArchiveReports"),
    "mail_root": str(HOME / "Library" / "Mail"),
    "age_days": 60,
    "large_file_mb": 200,
    "archive_items": [],  # [{"src": "~/Desktop/foo", "keep_local": true}, ...]
}

CONFIG_JSON = HOME / ".config" / "mac-archive" / "config.json"
LEGACY_CONFIG_SH = HOME / ".config" / "mac-archive" / "config.sh"

_ENV_MAP = {
    "MAC_ARCHIVE_NAS_MOUNT": "nas_mount",
    "MAC_ARCHIVE_NAS_URL": "nas_url",
    "MAC_ARCHIVE_REPORT_ROOT": "report_root",
    "MAC_ARCHIVE_MAIL_ROOT": "mail_root",
    "MAC_ARCHIVE_AGE_DAYS": "age_days",
    "MAC_ARCHIVE_LARGE_FILE_MB": "large_file_mb",
}


def load_config() -> Dict:
    cfg = dict(DEFAULTS)
    if LEGACY_CONFIG_SH.exists():
        try:
            text = LEGACY_CONFIG_SH.read_text(encoding="utf-8")
            for name, key in [("ARCHIVE_ROOT", "nas_mount"), ("REPORT_ROOT", "report_root"), ("ARCHIVE_AGE_DAYS", "age_days")]:
                m = re.search(rf'^{name}="?([^"\n]+)"?', text, re.M)
                if m:
                    val = m.group(1).strip()
                    if key == "age_days":
                        try:
                            cfg[key] = int(val)
                        except ValueError:
                            pass
                    else:
                        cfg[key] = val.replace("$HOME", str(HOME))
        except Exception:
            pass
    if CONFIG_JSON.exists():
        try:
            data = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k in DEFAULTS:
                    if k in data:
                        cfg[k] = data[k]
        except Exception as e:
            print(f"WARNING: bad config.json: {e}", file=sys.stderr)
    for env, key in _ENV_MAP.items():
        v = os.environ.get(env)
        if v:
            if key in ("age_days", "large_file_mb"):
                try:
                    cfg[key] = int(v)
                except ValueError:
                    pass
            else:
                cfg[key] = os.path.expanduser(v)
    cfg["nas_mount"] = Path(os.path.expanduser(str(cfg["nas_mount"])))
    cfg["report_root"] = Path(os.path.expanduser(str(cfg["report_root"])))
    cfg["mail_root"] = Path(os.path.expanduser(str(cfg["mail_root"])))
    return cfg


CFG = load_config()
NAS_MOUNT: Path = CFG["nas_mount"]
REPORT_ROOT: Path = CFG["report_root"]
MAIL_ROOT: Path = CFG["mail_root"]
AGE_DAYS: int = int(CFG["age_days"])
LARGE_FILE_THRESHOLD: int = int(CFG["large_file_mb"]) * 1024 * 1024


def nas_mounted() -> bool:
    return NAS_MOUNT.is_dir() and NAS_MOUNT.is_mount()


def try_mount_nas() -> bool:
    """Attempt to mount the NAS using the configured URL. Credentials must live
    in the macOS Keychain (mount_smbfs without a password) or be embedded in
    the URL by the user. Never hardcode passwords."""
    url = CFG.get("nas_url")
    if not url:
        return False
    try:
        NAS_MOUNT.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["mount_smbfs", url, str(NAS_MOUNT)], capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return nas_mounted()
        print(f"WARNING: mount_smbfs failed: {r.stderr.strip()}")
    except Exception as e:
        print(f"WARNING: mount attempt error: {e}")
    return False


def require_nas(execute: bool) -> bool:
    """Return True if NAS is usable. In dry-run, missing NAS is a warning, not fatal."""
    if nas_mounted():
        return True
    if try_mount_nas():
        return True
    msg = f"NAS not mounted at {NAS_MOUNT}. Set MAC_ARCHIVE_NAS_MOUNT / MAC_ARCHIVE_NAS_URL or ~/.config/mac-archive/config.json, or mount manually."
    if execute:
        print(f"ERROR: {msg}")
        return False
    print(f"WARNING: {msg} (dry-run continues without NAS)")
    return False


def format_size(n: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024.0:
            return f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} PB"


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file() and not p.is_symlink():
                try:
                    total += p.stat().st_size
                except Exception:
                    pass
    except Exception:
        pass
    return total


def file_count(path: Path) -> int:
    return sum(1 for p in path.rglob("*") if p.is_file())


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def write_report(name: str, body: str) -> Path:
    report_dir = REPORT_ROOT / "logs"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"{name}-{timestamp()}.md"
    report.write_text(body, encoding="utf-8")
    return report


def write_manifest(prefix: str, entries: List[Dict]) -> Path:
    md = NAS_MOUNT / "_manifests"
    md.mkdir(parents=True, exist_ok=True)
    path = md / f"manifest-{prefix}-{timestamp()}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def add_common_args(parser) -> None:
    parser.add_argument("--execute", action="store_true", help="Actually perform actions (default: dry-run)")
    parser.add_argument("--config", type=str, default=None, help="Override config JSON path")
