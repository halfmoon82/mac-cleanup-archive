#!/usr/bin/env python3
"""Targeted safe cleanups: Chrome on-device models, unused HuggingFace models,
old VS Code Server CLI versions. Default dry-run; pass --execute to delete."""
import argparse
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import HOME, dir_size, format_size, write_report

CHROME_TARGETS = [
    HOME / "Library" / "Application Support" / "Google" / "Chrome" / "OptGuideOnDeviceModel",
    HOME / "Library" / "Application Support" / "Google" / "Chrome" / "OptGuideOnDeviceClassifierModel",
]
HF_CACHE = HOME / ".cache" / "huggingface" / "hub"
HF_REMOVE_MODELS = {
    "models--BAAI--bge-large-zh-v1.5",
    "models--BAAI--bge-base-zh-v1.5",
    "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2",
    "models--sentence-transformers--all-MiniLM-L6-v2",
    "models--shibing624--text2vec-base-chinese",
}
VSCODE_SERVERS = HOME / ".vscode-server" / "cli" / "servers"
VSCODE_KEEP_DAYS = 7


def collect_targets() -> List[Tuple[Path, str]]:
    out: List[Tuple[Path, str]] = []
    for p in CHROME_TARGETS:
        if p.exists():
            out.append((p, "chrome on-device model cache"))
    for name in HF_REMOVE_MODELS:
        p = HF_CACHE / name
        if p.exists():
            out.append((p, "unused HuggingFace model"))
    if VSCODE_SERVERS.exists():
        dirs = []
        for p in VSCODE_SERVERS.iterdir():
            if p.is_dir() and p.name.startswith("Stable-"):
                try:
                    dirs.append((p.stat().st_mtime, p))
                except Exception:
                    pass
        dirs.sort(reverse=True)
        threshold = time.time() - VSCODE_KEEP_DAYS * 86400
        for i, (mtime, p) in enumerate(dirs):
            if i == 0 or mtime > threshold:
                continue
            out.append((p, f"old VS Code Server version (> {VSCODE_KEEP_DAYS} days)"))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Targeted safe cache/model cleanups")
    parser.add_argument("--execute", action="store_true", help="Actually delete (default: dry-run)")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    targets = collect_targets()
    if not targets:
        print("No targets found.")
        write_report("cleanup-caches",
                     f"# Targeted Cleanup Report\n\n- Generated: {datetime.now().isoformat()}\n- Targets: 0\n")
        return
    freed = 0
    removed: List[Tuple[Path, int]] = []
    failed: List[Tuple[Path, str]] = []
    for p, reason in targets:
        size = dir_size(p)
        print(f"{'[DRY-RUN] ' if not args.execute else ''}Would remove {p} ({format_size(size)}; {reason})")
        if args.execute:
            try:
                shutil.rmtree(p)
                freed += size
                removed.append((p, size))
                print(f"REMOVED {p}")
            except Exception as e:
                failed.append((p, str(e)))
                print(f"FAILED {p}: {e}")
    print(f"\n{'[DRY-RUN] ' if not args.execute else ''}Total: {format_size(sum(dir_size(p) for p, _ in targets)) if not args.execute else format_size(freed)}")
    body = (f"# Targeted Cleanup Report\n\n- Generated: {datetime.now().isoformat()}\n"
            f"- Mode: {'DRY-RUN' if not args.execute else 'EXECUTE'}\n"
            f"- Targets: {len(targets)}\n- Removed: {len(removed)}, freed {format_size(freed)}\n"
            f"- Failed: {len(failed)}\n")
    if removed:
        body += "\n## Removed\n\n" + "".join(f"- `{p}` — {format_size(s)}\n" for p, s in removed)
    if failed:
        body += "\n## Failures\n\n" + "".join(f"- `{p}` — {e}\n" for p, e in failed)
    report = write_report("cleanup-caches", body)
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
