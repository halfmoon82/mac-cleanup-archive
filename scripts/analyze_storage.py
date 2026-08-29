#!/usr/bin/env python3
"""Read-only storage analysis: top directories and large files under $HOME and common roots.
Portable: only derives from Path.home(); never writes anything."""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import HOME, format_size, write_report

EXCLUDE_NAMES = {".git", ".venv", "venv", "node_modules", "__pycache__", ".Trash"}


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDE_NAMES for part in path.parts)


def dir_size_scandir(path: Path, depth: int = 0) -> int:
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                    elif entry.is_dir(follow_symlinks=False):
                        child = Path(entry.path)
                        if not is_excluded(child):
                            total += dir_size_scandir(child)
                except Exception:
                    pass
    except Exception:
        pass
    return total


def du_top(root: Path, top_n: int = 15) -> List[Tuple[Path, int]]:
    results: List[Tuple[Path, int]] = []
    if not root.exists():
        return results
    try:
        for child in root.iterdir():
            try:
                if child.is_symlink() or is_excluded(child):
                    continue
                size = child.stat().st_size if child.is_file() else dir_size_scandir(child)
                results.append((child, size))
            except Exception:
                pass
    except Exception:
        pass
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_n]


def find_large_files(root: Path, min_size: int = 100 * 1024 * 1024, max_n: int = 30) -> List[Tuple[int, Path]]:
    results: List[Tuple[int, Path]] = []
    if not root.exists():
        return results
    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dp = Path(dirpath)
            if is_excluded(dp):
                dirnames[:] = []
                continue
            for name in filenames:
                p = dp / name
                if p.is_symlink():
                    continue
                try:
                    s = p.stat().st_size
                    if s >= min_size:
                        results.append((s, p))
                except Exception:
                    pass
            if len(results) > max_n * 2:
                break
    except Exception:
        pass
    results.sort(key=lambda x: x[0], reverse=True)
    return results[:max_n]


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)
    roots = [HOME, HOME / "Library" / "Caches", HOME / "Library" / "Application Support",
             HOME / "Library" / "Containers", HOME / "Library" / "Mail",
             HOME / "Library" / "Developer", HOME / "Desktop", HOME / "Documents",
             HOME / "Downloads", HOME / "Movies", HOME / "Pictures"]
    lines = ["# Storage Analysis Report", f"- Generated: {datetime.now().isoformat()}", f"- Home: {HOME}"]
    for root in roots:
        lines.append(f"\n## Top entries under {root}\n")
        for path, size in du_top(root):
            lines.append(f"{format_size(size):>10}  {path}")
    lines.append("\n## Large files (>100 MB) under HOME\n")
    for size, p in find_large_files(HOME):
        lines.append(f"{format_size(size):>10}  {p}")
    lines.append("\n## Time Machine local snapshots\n")
    try:
        r = subprocess.run(["tmutil", "listlocalsnapshots", "/"], capture_output=True, text=True, timeout=10)
        lines.append(r.stdout.strip() or "None")
    except Exception as e:
        lines.append(f"Error: {e}")
    text = "\n".join(lines)
    print(text)
    report = write_report("storage-analysis", text)
    print(f"\nReport: {report}")


if __name__ == "__main__":
    main()
