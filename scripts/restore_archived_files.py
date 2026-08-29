#!/usr/bin/env python3
"""Restore project/config files that were archived to NAS but should not have been.
Reads an archive list produced by mac_cleanup.py (archive-*.lst) and copies back files
whose local path sits inside a project or configuration root.
Default: latest archive list under the report root; override with --list <path>."""
import argparse
import shutil
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import HOME, NAS_MOUNT, REPORT_ROOT, require_nas

PROJECT_MARKERS = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    ".git", ".gitignore", ".gitmodules",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "pom.xml", "build.gradle",
    "pyproject.toml", "Pipfile", "requirements.txt", "setup.py", "setup.cfg",
    "Gemfile", "composer.json", "Podfile",
    "CMakeLists.txt", "Makefile", "Dockerfile",
    "tsconfig.json", "jsconfig.json", "vite.config.ts", "vite.config.js",
    "webpack.config.js", "webpack.config.ts", "project.config.json",
    ".xcodeproj", ".sln", ".idea", ".vscode", ".obsidian",
}


def is_project_or_config_path(path: Path) -> bool:
    if path.name in PROJECT_MARKERS or path.suffix in {".xcodeproj", ".sln"}:
        return True
    for parent in path.parents:
        if parent == HOME or parent.parent == HOME:
            break
        for marker in PROJECT_MARKERS:
            try:
                if (parent / marker).exists():
                    return True
            except Exception:
                pass
    return False


def latest_archive_list() -> Path | None:
    logs = REPORT_ROOT / "logs"
    if not logs.exists():
        return None
    lists = sorted(logs.glob("archive-*.lst"))
    return lists[-1] if lists else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore project/config files from NAS")
    parser.add_argument("--list", type=str, default=None, help="Archive list file (default: latest archive-*.lst)")
    parser.add_argument("--execute", action="store_true", help="Actually copy back (default: dry-run)")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    list_path = Path(args.list) if args.list else latest_archive_list()
    if not list_path or not list_path.exists():
        print(f"Archive list not found: {list_path}")
        sys.exit(1)
    if not require_nas(execute=args.execute):
        print("NAS not available; cannot restore.")
        sys.exit(1)

    restored = failed = skipped = 0
    candidates: List[Path] = []
    for line in list_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        local_path = Path(parts[-1])
        if not local_path.is_absolute():
            local_path = HOME / local_path
        if local_path.exists():
            skipped += 1
            continue
        nas_path = NAS_MOUNT / local_path.relative_to(HOME) if str(local_path).startswith(str(HOME)) else None
        if nas_path is None or not nas_path.exists():
            skipped += 1
            continue
        if not is_project_or_config_path(local_path):
            skipped += 1
            continue
        candidates.append((local_path, nas_path))
        if args.execute:
            try:
                local_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(nas_path, local_path)
                restored += 1
                print(f"RESTORED {local_path}")
            except Exception as e:
                failed += 1
                print(f"FAILED {local_path}: {e}")
        else:
            print(f"[DRY-RUN] would restore {local_path}")

    print(f"\nCandidates: {len(candidates)}, restored: {restored}, failed: {failed}, skipped: {skipped}")


if __name__ == "__main__":
    main()
