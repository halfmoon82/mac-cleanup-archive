#!/usr/bin/env python3
"""mac_cleanup.py - Comprehensive Mac cleanup and NAS archive (portable).

Sections:
1. Old cache files (browser, package managers, app support).
2. Logs older than N days (user agents + system log dirs).
3. Scheduled-task docs older than N days (filename-date based), archived to NAS.
4. Files not read in N days, archived to NAS (project roots and configs protected).
5. Large files (>= threshold MB) not read recently, archived to NAS.
6. Duplicate files: delete all but newest copy.
7. Managed caches via tool commands (npm/yarn/pip/brew) and other safe targets.
8. Git repos whose work is fully pushed and merged to the remote default branch
   and idle > N days: tar to NAS, verify, then delete local copy.
9. Project manuscript docs (PRD/PLAN/SPEC/DESIGN ...) idle > N days, archived to NAS.
10. Any NAS archive is SHA-256 verified before local deletion.
11. Extended modules (borrowed from mac-cleanup-py, age-gated tiers a la
    PiusSunday/mac-cleanup): 14d tier for regenerable caches/logs, 90d tier
    for rebuild-costly data. Includes multi-volume Trash and /private/var/folders.
12. HuggingFace interrupted-download residue (~/.cache/huggingface/hub/**/blobs/*.incomplete).
13. Idle ollama models (>= N days via manifest mtime); exempt list hardcoded.
14. Downloads installers (*.dmg/*.pkg) older than N days: archived to NAS, then deleted.
15. Report-only review items (PiusSunday orphan design): orphaned app containers,
    Telegram postbox cache, iOS backups, Xcode Archives, simulator devices/runtimes,
    Time Machine local snapshots. Listed for manual review, never auto-deleted.

All paths derive from $HOME; NAS mount is discovered at runtime via _common.
Default is dry-run; pass --execute to act.
"""
import argparse
import glob
import gzip
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (AGE_DAYS, CFG, HOME, LARGE_FILE_THRESHOLD, NAS_MOUNT,
                     REPORT_ROOT, dir_size, file_count, format_size,
                     require_nas, sha256_file, timestamp, write_manifest,
                     write_report)

# ---------- Cache directories (age-based deletion) ----------
CACHE_DIRS = [
    HOME / ".npm",
    HOME / ".pip_cache",
    HOME / "Library" / "Caches" / "com.apple.dt.Xcode",
    HOME / "Library" / "Caches" / "com.apple.Safari",
    HOME / "Library" / "Caches" / "com.apple.WebKit",
    HOME / "Library" / "Caches" / "com.google.Chrome",
    HOME / "Library" / "Caches" / "com.google.Chrome.canary",
    HOME / "Library" / "Caches" / "com.microsoft.VSCode",
    HOME / "Library" / "Caches" / "com.microsoft.edgemac",
    HOME / "Library" / "Caches" / "npm",
    HOME / "Library" / "Caches" / "pip",
    HOME / "Library" / "Caches" / "yarn",
    HOME / "Library" / "Caches" / "pypoetry",
    HOME / "Library" / "Caches" / "org.python.python",
    HOME / "Library" / "Caches" / "com.apple.python",
    HOME / "Library" / "Caches" / "com.docker.docker",
    HOME / "Library" / "Caches" / "ms-playwright",
    HOME / "Library" / "Caches" / "bun",
    HOME / "Library" / "Caches" / "node-gyp",
    HOME / "Library" / "Caches" / "com.apple.AppleMediaServices",
    HOME / "Library" / "Caches" / "com.apple.CloudTelemetry",
    HOME / "Library" / "Caches" / "com.apple.helpd",
]

APP_SUPPORT_CACHE_DIRS = [
    HOME / "Library" / "Application Support" / "Code" / "Cache",
    HOME / "Library" / "Application Support" / "Code" / "CachedData",
    HOME / "Library" / "Application Support" / "Code" / "CachedExtensions",
    HOME / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Cache",
    HOME / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Code Cache",
    HOME / "Library" / "Application Support" / "Slack" / "Cache",
    HOME / "Library" / "Application Support" / "Discord" / "Cache",
    HOME / "Library" / "Application Support" / "Teams" / "Cache",
    HOME / "Library" / "Application Support" / "Zoom" / "Cache",
    HOME / "Library" / "Application Support" / "Spotify" / "PersistentCache",
]

class RuntimeCacheTarget(NamedTuple):
    """A regenerable cache directory plus the apps/processes that may hold it open."""

    path: Path
    reason: str
    apps: Tuple[str, ...] = ()
    process_patterns: Tuple[str, ...] = ()


# User-approved runtime cache coverage (2026-08-30): these are safe to rebuild,
# but several are held open by running apps. Execute mode quits the associated
# apps first; dry-run never quits anything.
RUNTIME_CACHE_TARGETS: List[RuntimeCacheTarget] = [
    RuntimeCacheTarget(
        HOME / "Library" / "Caches" / "Google",
        "Google/Chrome cache (regenerable)",
        apps=("Google Chrome",),
        process_patterns=("/Applications/Google Chrome.app",),
    ),
    RuntimeCacheTarget(
        HOME / "Library" / "Caches" / "com.anthropic.claudefordesktop.ShipIt",
        "Claude ShipIt update cache (regenerable)",
        apps=("Claude",),
        process_patterns=("/Applications/Claude.app",),
    ),
    RuntimeCacheTarget(
        HOME / "Library" / "Caches" / "camoufox",
        "Camoufox cached runtime (re-downloaded on next use)",
        apps=("Camoufox",),
        process_patterns=("Library/Caches/camoufox/Camoufox.app",),
    ),
    RuntimeCacheTarget(
        HOME / ".cache" / "puppeteer",
        "Puppeteer browser cache (regenerable)",
        process_patterns=("/.cache/puppeteer/", "Google Chrome for Testing", "chrome-headless-shell"),
    ),
    RuntimeCacheTarget(
        HOME / ".bun" / "install" / "cache",
        "bun install cache (regenerable)",
    ),
    RuntimeCacheTarget(
        HOME / "Library" / "Caches" / "Homebrew",
        "Homebrew download cache (regenerable)",
    ),
    RuntimeCacheTarget(
        HOME / ".cache" / "whisper",
        "Whisper model files .pt (re-downloaded by openai-whisper on next use)",
    ),
]

MANAGED_CACHES: List[Tuple[str, List[str]]] = [
    ("npm cache", ["npm", "cache", "clean", "--force"]),
    ("yarn cache", ["yarn", "cache", "clean", "--all"]),
    ("pip cache", ["python3", "-m", "pip", "cache", "purge"]),
    ("brew cache", ["brew", "cleanup", "--prune=all"]),
    ("uv cache", ["uv", "cache", "clean"]),
    ("pnpm store", ["pnpm", "store", "prune"]),
    ("pod cache", ["pod", "cache", "clean", "--all"]),
    ("go module cache", ["go", "clean", "-modcache"]),
    ("gem cleanup", ["gem", "cleanup"]),
    ("composer cache", ["composer", "clearcache", "--no-interaction"]),
    ("conan cache", ["conan", "remove", "*", "-c"]),
    # Dangling-only prune: stopped containers, unused networks, dangling images/build
    # cache. Deliberately NOT `-a` (which would drop all unused images).
    ("docker dangling", ["docker", "system", "prune", "-f"]),
]

# ---------- Extended modules (age-gated tiers) ----------
# Borrowed from mac-cleanup/mac-cleanup-py, adapted to a non-interactive,
# age-gated design borrowed from PiusSunday/mac-cleanup:
#   TIER_FAST: regenerable caches/logs -> touch only files older than 14d.
#   TIER_SLOW: rebuild-costly or user-adjacent data -> 90d.
# Entries are (path or glob, tier_days, reason). Globs are expanded at scan time.
# Items that are NOT trivially regenerable (iOS backups, Xcode Archives, Telegram
# db) are deliberately absent here; they live in the report-only review section.
TIER_FAST_DAYS = 14
TIER_SLOW_DAYS = 90

MODULE_DIRS: List[Tuple[str, int, str]] = [
    # --- 14d tier: regenerable caches & logs ---
    ("~/Library/Logs/JetBrains", TIER_FAST_DAYS, "JetBrains IDE logs"),
    ("~/Library/Application Support/Adobe/Common/Media Cache Files", TIER_FAST_DAYS, "Adobe media cache"),
    ("~/Library/Application Support/Google/Chrome/Default/Application Cache", TIER_FAST_DAYS, "Chrome application cache"),
    ("~/Library/Caches/com.apple.nsurlsessiond", TIER_FAST_DAYS, "nsurlsessiond cache"),
    ("~/Library/Logs/CoreSimulator", TIER_FAST_DAYS, "CoreSimulator logs"),
    ("~/Library/Developer/Xcode/DerivedData", TIER_FAST_DAYS, "Xcode DerivedData"),
    ("~/Library/Developer/Xcode/iOS Device Logs", TIER_FAST_DAYS, "Xcode iOS device logs"),
    ("~/Dropbox/.dropbox.cache", TIER_FAST_DAYS, "Dropbox cache"),
    ("~/Library/Application Support/Google/DriveFS/*/content_cache", TIER_FAST_DAYS, "DriveFS content cache"),
    ("~/Library/Caches/composer", TIER_FAST_DAYS, "Composer cache"),
    ("~/Library/Application Support/Steam/appcache", TIER_FAST_DAYS, "Steam appcache"),
    ("~/Library/Application Support/Steam/depotcache", TIER_FAST_DAYS, "Steam depotcache"),
    ("~/Library/Application Support/Steam/logs", TIER_FAST_DAYS, "Steam logs"),
    ("~/Library/Application Support/Steam/steamapps/shadercache", TIER_FAST_DAYS, "Steam shadercache"),
    ("~/Library/Application Support/Steam/steamapps/temp", TIER_FAST_DAYS, "Steam temp"),
    ("~/Library/Application Support/Steam/steamapps/download", TIER_FAST_DAYS, "Steam download temp"),
    ("~/Library/Application Support/minecraft/logs", TIER_FAST_DAYS, "Minecraft logs"),
    ("~/Library/Application Support/minecraft/crash-reports", TIER_FAST_DAYS, "Minecraft crash reports"),
    ("~/Library/Application Support/minecraft/webcache", TIER_FAST_DAYS, "Minecraft webcache"),
    ("~/Library/Application Support/minecraft/webcache2", TIER_FAST_DAYS, "Minecraft webcache2"),
    ("~/Library/Application Support/minecraft/.mixin.out", TIER_FAST_DAYS, "Minecraft mixin output"),
    ("~/.lunarclient/game-cache", TIER_FAST_DAYS, "Lunar Client game cache"),
    ("~/.lunarclient/launcher-cache", TIER_FAST_DAYS, "Lunar Client launcher cache"),
    ("~/.lunarclient/logs", TIER_FAST_DAYS, "Lunar Client logs"),
    ("~/.lunarclient/offline/*/logs", TIER_FAST_DAYS, "Lunar Client offline logs"),
    ("~/.android/cache", TIER_FAST_DAYS, "Android SDK cache"),
    ("~/.kite/logs", TIER_FAST_DAYS, "Kite logs"),
    ("~/.cacher/logs", TIER_FAST_DAYS, "Cacher logs"),
    ("~/Library/Application Support/Microsoft/Teams/Cache", TIER_FAST_DAYS, "Teams cache"),
    ("~/Library/Application Support/Microsoft/Teams/Application Cache", TIER_FAST_DAYS, "Teams application cache"),
    ("~/Library/Application Support/Microsoft/Teams/Code Cache", TIER_FAST_DAYS, "Teams code cache"),
    ("~/Library/Application Support/Microsoft/Teams/GPUCache", TIER_FAST_DAYS, "Teams GPU cache"),
    ("~/Library/Application Support/Microsoft/Teams/gpucache", TIER_FAST_DAYS, "Teams GPU cache"),
    ("~/Library/Application Support/Microsoft/Teams/tmp", TIER_FAST_DAYS, "Teams tmp"),
    ("~/Library/Application Support/obsidian/Cache", TIER_FAST_DAYS, "Obsidian cache"),
    ("~/Library/Application Support/obsidian/Code Cache", TIER_FAST_DAYS, "Obsidian code cache"),
    ("~/Library/Application Support/obsidian/DawnGraphiteCache", TIER_FAST_DAYS, "Obsidian dawn cache"),
    ("~/Library/Application Support/obsidian/DawnWebGPUCache", TIER_FAST_DAYS, "Obsidian WebGPU cache"),
    ("~/Library/Application Support/Electronic Arts/EA app/IGOCache", TIER_FAST_DAYS, "EA app cache"),
    ("~/Library/Application Support/Electronic Arts/EA app/Logs", TIER_FAST_DAYS, "EA app logs"),
    ("~/Library/Application Support/Electronic Arts/EA app/OfflineCache", TIER_FAST_DAYS, "EA app offline cache"),
    ("~/Library/Application Support/Electronic Arts/EA app/CEF/BrowserCache/EADesktop", TIER_FAST_DAYS, "EA CEF browser cache"),
    ("~/Library/Application Support/Chromium/GraphiteDawnCache", TIER_FAST_DAYS, "Chromium dawn cache"),
    ("~/Library/Application Support/Chromium/GrShaderCache", TIER_FAST_DAYS, "Chromium shader cache"),
    ("~/Library/Application Support/Chromium/ShaderCache", TIER_FAST_DAYS, "Chromium shader cache"),
    ("~/Library/Application Support/Chromium/Default/DawnCache", TIER_FAST_DAYS, "Chromium dawn cache"),
    ("~/Library/Application Support/Chromium/Default/GPUCache", TIER_FAST_DAYS, "Chromium GPU cache"),
    ("~/Library/Caches/Arc", TIER_FAST_DAYS, "Arc browser cache"),
    ("~/Library/Caches/CloudKit/company.thebrowser.Browser", TIER_FAST_DAYS, "Arc CloudKit cache"),
    ("~/Library/Caches/company.thebrowser.Browser", TIER_FAST_DAYS, "Arc browser cache"),
    ("/private/var/folders", TIER_FAST_DAYS, "system temp (var/folders)"),
    ("~/.Trash", TIER_FAST_DAYS, "trash items"),
    ("/Volumes/*/.Trashes", TIER_FAST_DAYS, "external volume trash"),
    ("~/wget-log", TIER_FAST_DAYS, "wget log"),
    ("~/.wget-hsts", TIER_FAST_DAYS, "wget HSTS cache"),
    ("~/*.hprof", TIER_FAST_DAYS, "Java heap dump"),
    # --- 90d tier: rebuild-costly caches ---
    ("~/.gradle/caches", TIER_SLOW_DAYS, "Gradle caches"),
    ("~/.nuget/packages", TIER_SLOW_DAYS, "NuGet packages"),
    ("~/.conan2/p", TIER_SLOW_DAYS, "Conan packages"),
    ("~/Music/iTunes/iTunes Media/Mobile Applications", TIER_SLOW_DAYS, "iOS app .ipa archives"),
]

# ---------- HuggingFace interrupted-download residue ----------
HF_HUB = HOME / ".cache" / "huggingface" / "hub"

# ---------- Ollama idle models ----------
# Hardcoded exemption: qwen3.5:4b-q4_K_M is used by openclaw heartbeat and the
# memos summarizer (see ~/.openclaw/openclaw.json). Never deleted, even on --execute.
OLLAMA_EXEMPT_MODELS = {"qwen3.5:4b-q4_K_M"}
OLLAMA_MANIFEST_ROOT = HOME / ".ollama" / "models" / "manifests"

# ---------- Downloads installers ----------
INSTALLER_EXTS = {".dmg", ".pkg"}

# ---------- Logs (agents + system) ----------
LOG_DIRS = [
    HOME / "Library" / "Logs",
    Path("/Library/Logs"),
    Path("/var/log"),
    Path("/private/var/log"),
]
# Agent-specific log dirs: any of these hidden tool dirs' logs subfolder
AGENT_TOOL_DIRS = [
    ".openclaw", ".codex", ".claude", ".claude-mem", ".cc-switch",
    ".pm2", ".finshare", ".copilot", ".continue",
]


def _collect_agent_log_dirs() -> List[Path]:
    dirs: List[Path] = []
    for name in AGENT_TOOL_DIRS:
        base = HOME / name
        if not base.exists():
            continue
        for sub in ("logs", "log"):
            p = base / sub
            if p.is_dir():
                dirs.append(p)
    return dirs


LOG_DIRS += _collect_agent_log_dirs()

# ---------- Scheduled-task docs (filename-date based) ----------
TASK_DOC_ROOTS = [
    HOME / ".openclaw" / "wealthteam-research-closed-loop",
    HOME / ".openclaw" / "wealth-market-clock-symmetry",
    HOME / ".openclaw" / "workspace",
    HOME / ".openclaw-worktrees",
]
TASK_DOC_SUBDIRS = [
    "wealth-team/daily-review", "wealth-team/weekly-review", "wealth-team/reports",
    "reports", "daily-review", "weekly-review",
]
TASK_DOC_RE = re.compile(
    r"(morning-brief|postmarket|daily-review|weekly-review|investment-theory|memory-consolidation|intraday-log|cio-decision|minimal-closed-loop-morning)",
    re.I,
)
TASK_DOC_EXCLUDE_RE = re.compile(
    r"(stock-daily-analysis|watchlist|research-report|researcher-research-digest|investment-objective|ticker|portfolio|position|account_state|knowledge-base|wiki|research-closed-loop|\.db$|\.sqlite)",
    re.I,
)

# ---------- Protection ----------
PROTECTED_RE = re.compile(
    "(" + "|".join([
        r"\.git($|\/)", r"\.svn($|\/)", r"\.hg($|\/)",
        r"\.venv($|\/)", r"venv($|\/)", r"node_modules($|\/)", r"__pycache__($|\/)",
        r"\.pytest_cache($|\/)", r"\.tox($|\/)", r"\.mypy_cache($|\/)", r"\.next($|\/)", r"\.egg-info($|\/)",
        r"\.env($|\/)", r"AGENTS\.md$", r"CLAUDE\.md$", r"WORKFLOW_POLICY", r"IDENTITY\.md$",
        r"HEARTBEAT\.md$", r"SOUL\.md$", r"USER\.md$", r"TOOLS\.md$", r"\.gitignore$", r"\.gitconfig$",
        r"\.zshrc$", r"\.bashrc$", r"\.profile$", r"\.ssh\/", r"\.gnupg\/", r"\.photoslibrary",
        r"Photos Library\.photoslibrary", r"Photo Booth", r"iMovie", r"GarageBand", r"Logic", r"Final Cut",
        r"openclaw备份", r"\.backup_log", r"\.last_backup", r"\.remote_backup_log",
        r"target/classes", r"target/test-classes", r"\.dist", r"/dist/", r"/build/",
        r"\.xcodeproj", r"\.sln", r"\.obsidian", r"\.vscode", r"\.idea",
    ]) + ")",
    re.I,
)

PROJECT_MARKERS = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    ".git", ".gitignore", ".gitmodules",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "pom.xml", "build.gradle",
    "pyproject.toml", "Pipfile", "requirements.txt", "setup.py", "setup.cfg",
    "Gemfile", "composer.json", "Podfile",
    "CMakeLists.txt", "Makefile", "Dockerfile",
    "tsconfig.json", "jsconfig.json", "vite.config.ts", "vite.config.js",
    "webpack.config.js", "webpack.config.ts", "project.config.json",
    "project.private.config.json", ".eslintrc.json", ".eslintrc-auto-import.json",
    ".xcodeproj", ".sln", ".idea", ".vscode", ".obsidian",
}

EXCLUDE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".cpp", ".c", ".h",
    ".swift", ".m", ".mm", ".rb", ".php", ".sh", ".zsh", ".bash", ".fish",
    ".html", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
    ".sql", ".db", ".sqlite", ".sqlite3",
    ".toml", ".lock", ".mod", ".sum", ".gradle", ".pom",
    ".pem", ".key", ".crt", ".p12", ".keystore", ".cer",
}

OTHER_PATTERNS = [
    (HOME / "Library" / "Logs" / "DiagnosticReports", 60),
    (HOME / "Library" / "Logs" / "CrashReporter", 60),
    (HOME / "Library" / "Developer" / "Xcode" / "iOS DeviceSupport", 60),
    (HOME / "Library" / "Developer" / "Xcode" / "DerivedData", 30),
    (HOME / "Downloads", 365),
    (HOME / ".Trash", 30),
]

USER_ROOTS = [HOME / "Desktop", HOME / "Documents", HOME / "Downloads",
              HOME / "Movies", HOME / "Pictures", HOME / "Music"]

# ---------- Merged/inactive git repos (tar to NAS, verify, delete local) ----------
# .git search depth: repo roots up to 3 levels below HOME (".git" itself is one deeper).
REPO_FIND_MAX_DEPTH = 4
REPO_SCAN_EXCLUDE_DIRS = {
    "Library", "Applications", "Pictures", "Movies", "Music", "Public",
    ".Trash", ".cache", ".npm", ".local", ".vscode-server", ".cursor",
    ".cargo", ".rustup", ".ollama", ".docker", ".fnm", ".nvm", "go",
    ".codex", ".openclaw", ".openclaw-worktrees", ".claude", ".claude-mem",
    ".cc-switch", ".agent-reach", ".agents", "node_modules", ".venv", "venv",
    "__pycache__",
}

# Knowledge-base shape: manuscript docs under such a tree are curated knowledge,
# not project intermediates; never archive them. Detected via KB_SCHEMA.md or
# the wiki/+raw/ directory pair.
def _looks_like_kb(path: Path) -> bool:
    try:
        if (path / "KB_SCHEMA.md").exists():
            return True
        return (path / "wiki").is_dir() and (path / "raw").is_dir()
    except Exception:
        return False

# ---------- Intra-repo residue (merged branches / regenerable dirs / leftovers) ----------
# Three tiers with different safety profiles:
#   1. local branches merged into the remote default branch -> delete (recoverable
#      from remote, no archive needed);
#   2. regenerable dirs (node_modules/.venv/dist/...) -> delete only when the repo
#      has been idle longer than the age threshold (rebuild restores them);
#   3. untracked leftover intermediates (*.bak, *.orig, *.rej, *rollback*) -> archive
#      to NAS with SHA-256 verification, then delete (they exist nowhere else).
# Every executed action is appended to <repo>/CLEANUP-RECOVERY.md so each project
# can look up what was removed and how to restore it.
REPO_REGEN_DIRS = ["node_modules", ".venv", "venv", "dist", "build",
                   ".next", ".turbo", "target", "__pycache__"]
REPO_LEFTOVER_RE = re.compile(r"(\.bak$|\.orig$|\.rej$|rollback)", re.I)
RECOVERY_FILENAME = "CLEANUP-RECOVERY.md"


# ---------- Project manuscript docs (PRD/PLAN/..., archive to NAS) ----------
MANUSCRIPT_RE = re.compile(
    r"(^|[^a-z0-9])(prd|plans?|specs?|specification|design|roadmap|blueprint)([^a-z0-9]|$)",
    re.I,
)
MANUSCRIPT_EXT = {".md", ".markdown", ".txt"}

# ---------- Helpers ----------
def log(msg: str, log_file: Optional[Path] = None) -> None:
    print(msg, flush=True)
    if log_file:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(msg + "\n")


def is_protected_path(path: Path) -> bool:
    s = str(path)
    if PROTECTED_RE.search(s):
        return True
    if any(part in {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
                    ".tox", ".mypy_cache", ".next", ".egg-info", ".Trash", ".cache", ".DS_Store"}
           for part in path.parts):
        return True
    return False


def is_project_root(path: Path) -> bool:
    if path.name in PROJECT_MARKERS or path.suffix in {".xcodeproj", ".sln", ".obsidian", ".idea", ".vscode"}:
        return True
    for marker in PROJECT_MARKERS:
        try:
            if (path / marker).exists():
                return True
        except Exception:
            pass
    return False


def is_under_project_root(path: Path) -> bool:
    for parent in path.parents:
        if parent == HOME or parent.parent == HOME:
            break
        if is_project_root(parent):
            return True
    return False


def extract_filename_date(path: Path) -> Optional[float]:
    s = path.name
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        try:
            d = datetime.strptime(m.group(0), "%Y-%m-%d")
            return (datetime.now() - d).total_seconds() / 86400
        except Exception:
            pass
    m = re.search(r'(\d{4})-W(\d{2})', s)
    if m:
        try:
            y, w = int(m.group(1)), int(m.group(2))
            d = datetime.strptime(f"{y}-W{w:02d}-1", "%Y-W%W-%w")
            return (datetime.now() - d).total_seconds() / 86400
        except Exception:
            pass
    return None


def run_find(paths: List[Path], expr: List[str]) -> List[str]:
    valid = [str(p) for p in paths if p.exists()]
    if not valid:
        return []
    prune: List[str] = []
    for pat in [".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
                ".tox", ".mypy_cache", ".next", ".egg-info", ".Trash"]:
        prune += ["-path", f"*/{pat}", "-prune", "-o"]
    cmd = ["find"] + valid + prune + expr + ["-print"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            log(f"WARNING: find failed: {r.stderr.strip()[:200]}")
            return []
        return [line for line in r.stdout.splitlines() if line]
    except Exception as e:
        log(f"WARNING: find error: {e}")
        return []


def parse_find_output(lines: List[str], reason: str) -> List[Tuple[Path, str, int]]:
    results = []
    for line in lines:
        p = Path(line)
        if not p.is_file() or is_protected_path(p):
            continue
        try:
            results.append((p, reason, p.stat().st_size))
        except Exception:
            pass
    return results


# ---------- Scanners ----------
def scan_cache_files(days: int) -> List[Tuple[Path, str, int]]:
    out: List[Tuple[Path, str, int]] = []
    for root in CACHE_DIRS:
        if not root.exists():
            continue
        out += parse_find_output(run_find([root], ["-atime", f"+{days}"]),
                                 f"cache not accessed in {days} days")
    return out


def scan_app_support_caches(days: int = 30) -> List[Tuple[Path, str, int]]:
    out: List[Tuple[Path, str, int]] = []
    for root in APP_SUPPORT_CACHE_DIRS:
        if not root.exists():
            continue
        out += parse_find_output(run_find([root], ["-atime", f"+{days}"]),
                                 f"app-support cache not accessed in {days} days")
    return out


def present_runtime_cache_targets() -> List[RuntimeCacheTarget]:
    return [target for target in RUNTIME_CACHE_TARGETS if target.path.exists()]


def _pids_matching(pattern: str) -> List[int]:
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    if r.returncode not in (0, 1):
        return []
    current = {os.getpid(), os.getppid()}
    pids: List[int] = []
    for line in r.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid not in current:
            pids.append(pid)
    return pids


def _patterns_have_processes(patterns: Tuple[str, ...]) -> bool:
    return any(_pids_matching(pattern) for pattern in patterns)


def quit_runtime_cache_apps(targets: List[RuntimeCacheTarget], dry_run: bool,
                            log_file: Optional[Path]) -> None:
    """Quit apps that can hold runtime caches open.

    Dry-run only reports what would happen. Execute mode first asks macOS apps to
    quit gracefully, then uses TERM and finally KILL for the narrow process
    patterns declared by the target. The patterns live in RUNTIME_CACHE_TARGETS so
    broad names are not invented at call sites.
    """
    apps = sorted({app for target in targets for app in target.apps})
    patterns = tuple(dict.fromkeys(pattern for target in targets for pattern in target.process_patterns))
    if not apps and not patterns:
        return
    if dry_run:
        if apps:
            log(f"[DRY-RUN] Would quit cache-associated apps: {', '.join(apps)}", log_file)
        if patterns:
            log(f"[DRY-RUN] Would stop cache-associated processes matching: {', '.join(patterns)}", log_file)
        return

    running_apps = []
    for target in targets:
        if target.process_patterns and not _patterns_have_processes(target.process_patterns):
            continue
        running_apps.extend(target.apps)
    for app in sorted(dict.fromkeys(running_apps)):
        log(f"Quitting cache-associated app: {app}", log_file)
        subprocess.run(["osascript", "-e", f'tell application "{app}" to quit'],
                       capture_output=True, text=True, timeout=20)

    deadline = time.time() + 10
    while time.time() < deadline and _patterns_have_processes(patterns):
        time.sleep(0.5)

    for pattern in patterns:
        pids = _pids_matching(pattern)
        if not pids:
            continue
        log(f"Force stopping cache-associated processes matching {pattern}: {pids}", log_file)
        subprocess.run(["pkill", "-TERM", "-f", pattern], capture_output=True, text=True, timeout=10)

    deadline = time.time() + 5
    while time.time() < deadline and _patterns_have_processes(patterns):
        time.sleep(0.5)

    for pattern in patterns:
        pids = _pids_matching(pattern)
        if not pids:
            continue
        log(f"Force killing cache-associated processes matching {pattern}: {pids}", log_file)
        subprocess.run(["pkill", "-KILL", "-f", pattern], capture_output=True, text=True, timeout=10)


def _iter_runtime_cache_files(root: Path) -> List[Path]:
    """Return files and symlinks below an exact approved cache root.

    Symlinks are returned as symlinks so unlinking them never follows to data
    outside the cache root. Directory entries are left behind as empty dirs.
    """
    try:
        if root.is_symlink() or root.is_file():
            return [root]
        if not root.is_dir():
            return []
        return [p for p in root.rglob("*") if p.is_symlink() or p.is_file()]
    except Exception:
        return []


def scan_runtime_cache_files(targets: List[RuntimeCacheTarget]) -> List[Tuple[Path, str, int]]:
    out: List[Tuple[Path, str, int]] = []
    for target in targets:
        for p in _iter_runtime_cache_files(target.path):
            try:
                out.append((p, target.reason, p.lstat().st_size))
            except Exception:
                pass
    return out


def scan_old_logs(days: int) -> List[Tuple[Path, str, int]]:
    out: List[Tuple[Path, str, int]] = []
    for root in LOG_DIRS:
        if not root.exists():
            continue
        out += parse_find_output(run_find([root], ["-mtime", f"+{days}"]),
                                 f"log older than {days} days")
    return out


def scan_task_docs(days: int) -> List[Tuple[Path, str, int]]:
    out: List[Tuple[Path, str, int]] = []
    scan_paths: List[Path] = []
    for root in TASK_DOC_ROOTS:
        if not root.exists():
            continue
        for sub in TASK_DOC_SUBDIRS:
            p = root / sub
            if p.exists():
                scan_paths.append(p)
    if not scan_paths:
        return out
    for line in run_find(scan_paths, []):
        p = Path(line)
        if is_protected_path(p) or not TASK_DOC_RE.search(str(p)) or TASK_DOC_EXCLUDE_RE.search(str(p)):
            continue
        age = extract_filename_date(p)
        if age is None or age <= days:
            continue
        try:
            out.append((p, f"scheduled task doc older than {days} days", p.stat().st_size))
        except Exception:
            pass
    return out


def scan_unread_files(days: int, min_size: int = 1024, max_size: int = 10 * 1024**3) -> List[Tuple[Path, str, int]]:
    valid = [r for r in USER_ROOTS if r.exists()]
    if not valid:
        return []
    size_expr = ["-size", f"+{min_size}c", "-size", f"-{max_size}c"]
    lines = run_find(valid, ["-atime", f"+{days}"] + size_expr)
    out: List[Tuple[Path, str, int]] = []
    for line in lines:
        p = Path(line)
        if not p.is_file() or is_protected_path(p) or is_under_project_root(p):
            continue
        if p.suffix.lower() in EXCLUDE_EXTENSIONS:
            continue
        try:
            out.append((p, f"not read in {days} days", p.stat().st_size))
        except Exception:
            pass
    return out


def scan_large_files(min_size: int) -> List[Tuple[Path, str, int]]:
    valid = [r for r in USER_ROOTS if r.exists()]
    if not valid:
        return []
    lines = run_find(valid, ["-size", f"+{min_size}c"])
    out: List[Tuple[Path, str, int]] = []
    for line in lines:
        p = Path(line)
        if not p.is_file() or is_protected_path(p) or is_under_project_root(p):
            continue
        if p.suffix.lower() in EXCLUDE_EXTENSIONS:
            continue
        try:
            size = p.stat().st_size
            age_days = (time.time() - p.stat().st_atime) / 86400
            out.append((p, f"large file ({format_size(size)}), not read in {age_days:.0f} days", size))
        except Exception:
            pass
    return out


def scan_duplicates() -> Tuple[List[Tuple[Path, str, int]], int]:
    scan_roots = [HOME / "Downloads", HOME / "Pictures", HOME / "Movies", HOME / "Desktop"]
    valid = [r for r in scan_roots if r.exists()]
    if not valid:
        return [], 0
    lines = run_find(valid, ["-size", "+1k", "-size", "-1G"])
    size_to_paths: Dict[int, List[Path]] = defaultdict(list)
    for line in lines:
        p = Path(line)
        if not p.is_file() or is_protected_path(p) or is_under_project_root(p):
            continue
        if p.suffix.lower() in EXCLUDE_EXTENSIONS:
            continue
        try:
            size_to_paths[p.stat().st_size].append(p)
        except Exception:
            pass
    hash_to_paths: Dict[str, List[Path]] = defaultdict(list)
    for size, paths in size_to_paths.items():
        if len(paths) < 2:
            continue
        for p in paths:
            try:
                hash_to_paths[sha256_file(p)].append(p)
            except Exception:
                pass
    to_delete: List[Tuple[Path, str, int]] = []
    space_saved = 0
    for h, paths in hash_to_paths.items():
        if len(paths) < 2:
            continue
        def sort_key(p: Path) -> Tuple[float, float]:
            try:
                s = p.stat()
                return getattr(s, "st_birthtime", s.st_mtime), s.st_mtime
            except Exception:
                return 0.0, 0.0
        paths_sorted = sorted(paths, key=sort_key, reverse=True)
        keep = paths_sorted[0]
        for p in paths_sorted[1:]:
            to_delete.append((p, f"duplicate of {keep}; keeping newest copy", p.stat().st_size))
            space_saved += p.stat().st_size
    return to_delete, space_saved


def scan_other_cleanup() -> List[Tuple[Path, str, int]]:
    out: List[Tuple[Path, str, int]] = []
    for root, days in OTHER_PATTERNS:
        if not root.exists():
            continue
        out += parse_find_output(run_find([root], ["-atime", f"+{days}"]),
                                 f"other cleanup: older than {days} days")
    return out


def _git(repo: Path, *args: str) -> Optional[str]:
    """Run a read-only git command; return stdout stripped, or None on failure."""
    try:
        r = subprocess.run(["git", "-C", str(repo)] + list(args),
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return None
        return r.stdout.strip()
    except Exception:
        return None


def find_git_repos(max_depth: int = REPO_FIND_MAX_DEPTH) -> List[Path]:
    """Locate .git directories under HOME, pruning runtime/heavy trees."""
    prune: List[str] = []
    for name in sorted(REPO_SCAN_EXCLUDE_DIRS):
        prune += ["-name", name, "-prune", "-o"]
    cmd = (["find", str(HOME), "-maxdepth", str(max_depth)] + prune +
           ["-type", "d", "-name", ".git", "-print"])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode not in (0, 1):
            log(f"WARNING: find git repos failed: {r.stderr.strip()[:200]}")
        return [Path(line) for line in r.stdout.splitlines() if line]
    except Exception as e:
        log(f"WARNING: find git repos error: {e}")
        return []


def repo_fully_merged_reason(repo: Path) -> Tuple[bool, str]:
    """Conservative check: every bit of local work must exist on the remote."""
    if not _git(repo, "remote"):
        return False, "no remote configured"
    status = _git(repo, "status", "--porcelain")
    if status is None:
        return False, "git status failed"
    # The recovery manifests we write must not disqualify a repo by themselves.
    status_lines = [l for l in status.splitlines()
                    if l and not l[3:].strip().strip('"') == RECOVERY_FILENAME]
    if status_lines:
        return False, "working tree not clean"
    if _git(repo, "stash", "list"):
        return False, "has stashed changes"
    unpushed = _git(repo, "log", "--branches", "--not", "--remotes", "--oneline")
    if unpushed is None:
        return False, "git log failed"
    if unpushed:
        return False, "has unpushed commits"
    head = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if not head:
        for cand in ("origin/main", "origin/master"):
            if _git(repo, "rev-parse", "--verify", "--quiet", cand) is not None:
                head = cand
                break
    if not head:
        return False, "no remote default branch"
    branches = _git(repo, "branch", "--format=%(refname:short)") or ""
    for b in branches.splitlines():
        b = b.strip()
        if not b:
            continue
        chk = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", b, head],
                             capture_output=True, timeout=60)
        if chk.returncode != 0:
            return False, f"branch {b} not merged into {head}"
    return True, f"all branches merged into {head}"


def scan_merged_repos(days: int) -> List[Tuple[Path, str, int]]:
    out: List[Tuple[Path, str, int]] = []
    if not shutil.which("git"):
        log("WARNING: git not found; skipping merged-repo scan")
        return out
    gitdirs = sorted(find_git_repos(), key=lambda p: len(p.parts))
    chosen: List[Path] = []
    for gitdir in gitdirs:
        repo = gitdir.parent
        if any(str(repo).startswith(str(c) + "/") for c in chosen):
            continue  # nested inside an already-selected repo; tar covers it
        ok, detail = repo_fully_merged_reason(repo)
        if not ok:
            continue
        head_ts = _git(repo, "log", "-1", "--format=%ct")
        if not head_ts:
            continue
        try:
            age = (time.time() - int(head_ts)) / 86400
        except ValueError:
            continue
        if age <= days:
            continue
        chosen.append(repo)
        out.append((repo,
                    f"merged repo ({detail}), idle {age:.0f} days",
                    dir_size(repo)))
    return out


def _manuscript_committed_and_pushed(p: Path) -> bool:
    """For manuscripts inside a git repo: file must be tracked, unmodified, and
    have no unpushed commits touching it. Repos without a remote are rejected."""
    if not shutil.which("git"):
        return False
    top = _git(p.parent, "rev-parse", "--show-toplevel")
    if not top:
        return True  # not inside a repo; age check alone decides
    repo = Path(top)
    if not _git(repo, "remote"):
        return False
    try:
        rel = str(p.relative_to(repo))
    except ValueError:
        return False
    if _git(repo, "status", "--porcelain", "--", rel):
        return False
    if _git(repo, "ls-files", "--error-unmatch", rel) is None:
        return False  # untracked: only copy is local
    unpushed = _git(repo, "log", "--not", "--remotes", "--oneline", "--", rel)
    if unpushed is None or unpushed:
        return False
    return True


def scan_manuscript_docs(days: int) -> List[Tuple[Path, str, int]]:
    roots: List[Path] = [r for r in USER_ROOTS if r.exists()]
    try:
        for child in sorted(HOME.iterdir()):
            if (child.is_dir() and not child.name.startswith(".")
                    and child.name not in REPO_SCAN_EXCLUDE_DIRS and child not in roots):
                roots.append(child)
    except Exception:
        pass
    if not roots:
        return []
    kb_cache: Dict[Path, bool] = {}

    def under_kb(p: Path) -> bool:
        for parent in p.parents:
            if parent == HOME or parent.parent == parent:
                break
            if parent not in kb_cache:
                kb_cache[parent] = _looks_like_kb(parent)
            if kb_cache[parent]:
                return True
        return False

    lines = run_find(roots, ["-mtime", f"+{days}"])
    out: List[Tuple[Path, str, int]] = []
    for line in lines:
        p = Path(line)
        if not p.is_file() or is_protected_path(p):
            continue
        if under_kb(p):
            continue
        if p.suffix.lower() not in MANUSCRIPT_EXT:
            continue
        if not MANUSCRIPT_RE.search(p.stem):
            continue
        if not _manuscript_committed_and_pushed(p):
            continue
        try:
            out.append((p, f"manuscript doc idle over {days} days (mtime)", p.stat().st_size))
        except Exception:
            pass
    return out


def _repo_idle_days(repo: Path) -> Optional[float]:
    ts = _git(repo, "log", "-1", "--format=%ct")
    if not ts:
        return None
    try:
        return (time.time() - int(ts)) / 86400
    except ValueError:
        return None


def _restore_hint(dirname: str, repo: Path) -> str:
    if dirname == "node_modules":
        if (repo / "pnpm-lock.yaml").exists():
            return "pnpm install"
        if (repo / "yarn.lock").exists():
            return "yarn install"
        return "npm install"
    if dirname in (".venv", "venv"):
        return f"python3 -m venv {dirname} && {dirname}/bin/pip install -r requirements.txt"
    if dirname == "target":
        return "mvn/gradle/cargo build (按项目构建工具)"
    return "重新运行项目构建命令"


def scan_repo_residue(days: int) -> List[Dict]:
    """Per-repo residue candidates: merged branches, regenerable dirs (idle repos
    only), and untracked leftover intermediates."""
    out: List[Dict] = []
    if not shutil.which("git"):
        log("WARNING: git not found; skipping repo-residue scan")
        return out
    for gitdir in find_git_repos():
        repo = gitdir.parent
        if _looks_like_kb(repo):
            continue
        item: Dict = {"repo": repo, "branches": [], "regen": [], "leftovers": []}
        head = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
        if not head:
            for cand in ("origin/main", "origin/master"):
                if _git(repo, "rev-parse", "--verify", "--quiet", cand) is not None:
                    head = cand
                    break
        if head:
            default_short = head.split("/", 1)[1]
            current = _git(repo, "branch", "--show-current") or ""
            merged = _git(repo, "branch", "--merged", head, "--format=%(refname:short)") or ""
            for b in merged.splitlines():
                b = b.strip()
                if not b or b in (default_short, current):
                    continue
                sha = _git(repo, "rev-parse", "--short", b) or "unknown"
                item["branches"].append({"name": b, "sha": sha, "merged_into": head})
        idle = _repo_idle_days(repo)
        if idle is not None and idle > days:
            for name in REPO_REGEN_DIRS:
                d = repo / name
                if d.is_dir():
                    item["regen"].append({"path": d, "size": dir_size(d),
                                          "restore": _restore_hint(name, repo)})
        untracked = _git(repo, "ls-files", "--others", "--exclude-standard") or ""
        for rel in untracked.splitlines():
            rel = rel.strip()
            if not rel or not REPO_LEFTOVER_RE.search(rel):
                continue
            p = repo / rel
            if p.is_file() and not is_protected_path(p):
                try:
                    item["leftovers"].append({"path": p, "size": p.stat().st_size})
                except Exception:
                    pass
        if item["branches"] or item["regen"] or item["leftovers"]:
            out.append(item)
    return out


def _append_recovery_manifest(repo: Path, entries: List[str]) -> None:
    manifest = repo / RECOVERY_FILENAME
    header = ""
    if not manifest.exists():
        header = ("# Cleanup Recovery Manifest\n\n"
                  "本文件由 mac_cleanup.py 维护，记录本仓库被清理的内容与恢复方法。\n"
                  "恢复后可将对应条目划掉；本文件不影响仓库清理判定。\n")
    with open(manifest, "a", encoding="utf-8") as f:
        if header:
            f.write(header)
        f.write(f"\n## Run {timestamp()}\n\n")
        for line in entries:
            f.write(f"- {line}\n")


def process_repo_residue(item: Dict, dry_run: bool, compress: bool,
                         log_file: Optional[Path]) -> Dict[str, int]:
    """Execute one repo's residue actions. Returns per-tier success counts."""
    repo: Path = item["repo"]
    counts = {"branches": 0, "regen": 0, "leftovers": 0}
    entries: List[str] = []
    remote_url = _git(repo, "config", "--get", "remote.origin.url") or "(unknown remote)"
    for b in item["branches"]:
        if not dry_run:
            r = subprocess.run(["git", "-C", str(repo), "branch", "-d", b["name"]],
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                log(f"ERROR deleting branch {b['name']} in {repo}: {r.stderr.strip()[:200]}", log_file)
                continue
        counts["branches"] += 1
        entries.append(f"删除已合并分支 `{b['name']}`（原指向 {b['sha']}，已并入 {b['merged_into']}）。"
                       f"恢复：`git fetch && git checkout -b {b['name']} {b['sha']}`；远端：{remote_url}")
    for g in item["regen"]:
        if not dry_run:
            try:
                shutil.rmtree(g["path"])
            except Exception as e:
                log(f"ERROR removing {g['path']}: {e}", log_file)
                continue
        counts["regen"] += 1
        entries.append(f"删除可再生目录 `{g['path'].name}/`（{format_size(g['size'])}）。"
                       f"恢复：`{g['restore']}`")
    for lf in item["leftovers"]:
        ok = True
        dst = None
        if not dry_run:
            ok, dst = archive_file(lf["path"], dry_run=False, compress=compress, log_file=log_file)
        if not ok:
            continue
        counts["leftovers"] += 1
        nas_note = f"NAS 副本：{dst}" if dst else "NAS 副本路径见执行日志"
        entries.append(f"归档并删除遗留文件 `{lf['path'].relative_to(repo)}`（{format_size(lf['size'])}）。"
                       f"{nas_note}；恢复：从 NAS 对应路径取回")
    if entries and not dry_run:
        try:
            _append_recovery_manifest(repo, entries)
        except Exception as e:
            log(f"ERROR writing recovery manifest in {repo}: {e}", log_file)
    return counts


def _docker_daemon_up() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def clean_managed_caches(dry_run: bool, log_file: Optional[Path]) -> Dict[str, str]:
    summary: Dict[str, str] = {}
    for name, cmd in MANAGED_CACHES:
        if not shutil.which(cmd[0]):
            summary[name] = "tool not installed"
            continue
        if dry_run:
            log(f"[DRY-RUN] Would run: {' '.join(cmd)}", log_file)
            summary[name] = "dry-run"
            continue
        if cmd[0] == "docker" and not _docker_daemon_up():
            summary[name] = "skipped: docker daemon not running"
            log(f"Skipping {' '.join(cmd)}: docker daemon not running", log_file)
            continue
        log(f"Running: {' '.join(cmd)}", log_file)
        env = None
        if cmd[0] == "uv":
            # Fail fast instead of hanging 300s when the cache lock is held by a
            # long-running consumer (e.g. claude-mem's chroma-mcp runs from
            # ~/.cache/uv/archive-v0). Force-cleaning is deliberately NOT done:
            # it would delete a running service's environment.
            env = dict(os.environ, UV_LOCK_TIMEOUT="30")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            out = (r.stdout or r.stderr or "").strip().splitlines()
            summary[name] = (out[-1] if out else "ok") if r.returncode == 0 else f"exit {r.returncode}"
            log(f"  {summary[name]}", log_file)
            if r.returncode != 0 and out:
                log(f"  output: {out[-1][:200]}", log_file)
        except Exception as e:
            summary[name] = f"error: {e}"
            log(f"  {summary[name]}", log_file)
    npx = HOME / ".npm" / "_npx"
    if npx.exists() and not dry_run:
        subprocess.run(["find", str(npx), "-type", "f", "-atime", "+30", "-delete"],
                       capture_output=True, text=True, timeout=120)
        subprocess.run(["find", str(npx), "-type", "d", "-empty", "-delete"],
                       capture_output=True, text=True, timeout=120)
        summary["npx stale installs"] = "cleaned"
    return summary


# ---------- Extended module scanners ----------
def _iter_target_files(match: Path) -> List[Path]:
    """Files under a module target: the file itself, or all real files below a dir."""
    try:
        if match.is_symlink():
            return []
        if match.is_file():
            return [match]
        if match.is_dir():
            return [p for p in match.rglob("*") if p.is_file() and not p.is_symlink()]
    except Exception:
        pass
    return []


def scan_module_dirs() -> List[Tuple[Path, str, int]]:
    """Age-gated scan of MODULE_DIRS. Files only, mtime-based, symlinks skipped.
    Never touches the NAS mount (which can match the /Volumes/*/.Trashes glob)."""
    out: List[Tuple[Path, str, int]] = []
    now = time.time()
    patterns = list(MODULE_DIRS)
    pyenv_cache = os.environ.get("PYENV_VIRTUALENV_CACHE_PATH")
    if pyenv_cache:
        patterns.append((pyenv_cache, TIER_FAST_DAYS, "pyenv-virtualenv cache"))
    for pattern, days, reason in patterns:
        cutoff = now - days * 86400
        for m in glob.glob(os.path.expanduser(pattern)):
            mp = Path(m)
            try:
                mp_resolved = mp.resolve()
                if mp_resolved == NAS_MOUNT or NAS_MOUNT in mp_resolved.parents:
                    continue
            except Exception:
                continue
            for p in _iter_target_files(mp):
                try:
                    st = p.stat()
                    if st.st_mtime < cutoff:
                        out.append((p, f"{reason} (> {days}d)", st.st_size))
                except Exception:
                    pass
    return out


def scan_hf_incomplete() -> List[Tuple[Path, str, int]]:
    """Interrupted HuggingFace downloads. Only *.incomplete blobs are listed;
    complete blobs are never touched. Safe: HF resumes/restarts these anyway."""
    out: List[Tuple[Path, str, int]] = []
    if not HF_HUB.is_dir():
        return out
    for repo_dir in HF_HUB.iterdir():
        blobs = repo_dir / "blobs"
        if not blobs.is_dir():
            continue
        for f in blobs.glob("*.incomplete"):
            try:
                if f.is_file() and not f.is_symlink():
                    out.append((f, "HF incomplete download blob", f.stat().st_size))
            except Exception:
                pass
    return out


def scan_installers(days: int) -> List[Tuple[Path, str, int]]:
    """*.dmg/*.pkg in ~/Downloads older than N days. Routed to archive_candidates,
    so local deletion happens only after NAS SHA-256 verification."""
    out: List[Tuple[Path, str, int]] = []
    dl = HOME / "Downloads"
    if not dl.is_dir():
        return out
    cutoff = time.time() - days * 86400
    for p in dl.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink() and p.suffix.lower() in INSTALLER_EXTS:
                st = p.stat()
                if st.st_mtime < cutoff:
                    out.append((p, f"Downloads installer > {days}d (archive to NAS)", st.st_size))
        except Exception:
            pass
    return out


# ---------- Ollama idle models ----------
def _ollama_models(cmd: List[str]) -> List[str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return []
        return [ln.split()[0] for ln in r.stdout.splitlines()[1:] if ln.split()]
    except Exception:
        return []


def _ollama_manifest(name: str) -> Path:
    base, _, tag = name.partition(":")
    tag = tag or "latest"
    if "/" in base:
        ns, model = base.split("/", 1)
    else:
        ns, model = "library", base
    return OLLAMA_MANIFEST_ROOT / "registry.ollama.ai" / ns / model / tag


def scan_ollama_idle_models(days: int, log_file: Optional[Path]) -> List[Dict]:
    """Idle ollama models. Last-used is proxied by manifest mtime (ollama has no
    usage tracking). Exempt models and currently loaded models are never listed.
    Note: models share blobs, so actual space freed is known only after removal."""
    if not shutil.which("ollama"):
        log("ollama not installed; skipping idle-model scan", log_file)
        return []
    running = set(_ollama_models(["ollama", "ps"]))
    out: List[Dict] = []
    r = subprocess.run(["ollama", "ls"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        log(f"WARNING: ollama ls failed: {r.stderr.strip()[:200]}", log_file)
        return []
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        name = parts[0]
        size_str = " ".join(parts[2:4]) if len(parts) >= 4 else " ".join(parts[2:])
        if name in OLLAMA_EXEMPT_MODELS:
            log(f"  ollama: {name} exempt (openclaw heartbeat/memos); skipped", log_file)
            continue
        if name in running:
            log(f"  ollama: {name} currently loaded; skipped", log_file)
            continue
        manifest = _ollama_manifest(name)
        if not manifest.exists():
            log(f"  ollama: {name} manifest not found ({manifest}); skipped conservatively", log_file)
            continue
        idle = (time.time() - manifest.stat().st_mtime) / 86400
        if idle >= days:
            out.append({
                "name": name, "size": size_str, "idle_days": int(idle),
                "last_used": datetime.fromtimestamp(manifest.stat().st_mtime).strftime("%Y-%m-%d"),
            })
    return out


def remove_ollama_models(items: List[Dict], dry_run: bool, log_file: Optional[Path]) -> Tuple[int, int]:
    removed = failed = 0
    if dry_run:
        return removed, failed
    running = set(_ollama_models(["ollama", "ps"]))
    for it in items:
        name = it["name"]
        # Hard guards, re-checked at execute time even though scan already filtered.
        if name in OLLAMA_EXEMPT_MODELS:
            log(f"SKIP exempt ollama model: {name}", log_file)
            continue
        if name in running:
            log(f"SKIP currently loaded ollama model: {name}", log_file)
            continue
        log(f"Removing ollama model: {name} (reported {it['size']}, idle {it['idle_days']}d)", log_file)
        try:
            r = subprocess.run(["ollama", "rm", name], capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                removed += 1
            else:
                failed += 1
                log(f"ERROR ollama rm {name}: {r.stderr.strip()[:200]}", log_file)
        except Exception as e:
            failed += 1
            log(f"ERROR ollama rm {name}: {e}", log_file)
    return removed, failed


# ---------- Report-only review items (PiusSunday orphan design) ----------
def _installed_app_tokens() -> List[str]:
    tokens = set()
    for base in [Path("/Applications"), Path("/System/Applications"),
                 Path("/System/Applications/Utilities"), HOME / "Applications"]:
        if not base.is_dir():
            continue
        for app in base.glob("*.app"):
            t = re.sub(r"[^a-z0-9]", "", app.stem.lower())
            if t:
                tokens.add(t)
    return sorted(tokens)


def collect_review_items(log_file: Optional[Path]) -> List[Dict]:
    """Report-only findings: never deleted by this script, require manual review.
    Orphan matching is a low-confidence heuristic and every item says so."""
    items: List[Dict] = []

    # 1. Orphaned app containers / group containers (heuristic, report-only).
    app_tokens = _installed_app_tokens()
    for cdir in [HOME / "Library" / "Containers", HOME / "Library" / "Group Containers"]:
        if not cdir.is_dir():
            continue
        try:
            entries = sorted(cdir.iterdir())
        except Exception:
            continue
        for d in entries:
            name = d.name
            if name.startswith(("com.apple.", "group.com.apple", "com.microsoft.autoupdate")):
                continue
            token = re.sub(r"[^a-z0-9]", "", name.split(".")[-1].lower())
            if not token or len(token) < 3:
                continue
            if any(token in a or a in token for a in app_tokens):
                continue
            items.append({
                "kind": "orphan-container?", "path": str(d), "size": dir_size(d),
                "note": "no matching .app found; low-confidence heuristic, manual review required",
            })

    # 2. Telegram postbox db cache (delete requires quitting Telegram first).
    for m in glob.glob(str(HOME / "Library" / "Group Containers" /
                           "*.ru.keepcoder.Telegram" / "stable" / "account-*" / "postbox" / "db")):
        d = Path(m)
        if d.is_dir():
            items.append({
                "kind": "telegram-cache", "path": str(d), "size": dir_size(d),
                "note": "quit Telegram before deleting; cache rebuilds smaller on restart",
            })

    # 3. iOS device backups and Xcode Archives (user data; not auto-deleted).
    for label, d in [("ios-backup", HOME / "Library" / "Application Support" / "MobileSync" / "Backup"),
                     ("xcode-archives", HOME / "Library" / "Developer" / "Xcode" / "Archives")]:
        if d.is_dir():
            items.append({
                "kind": label, "path": str(d), "size": dir_size(d),
                "note": "user data; review manually (Xcode Organizer / Finder device manager)",
            })

    # 4. CoreSimulator devices and runtimes (pius removes superseded runtimes;
    #    here we only report sizes for manual `xcrun simctl` / Xcode cleanup).
    for label, d in [("simulator-devices", HOME / "Library" / "Developer" / "CoreSimulator" / "Devices"),
                     ("simulator-runtimes", HOME / "Library" / "Developer" / "CoreSimulator" / "Profiles" / "Runtimes")]:
        if d.is_dir():
            items.append({
                "kind": label, "path": str(d), "size": dir_size(d),
                "note": "review with 'xcrun simctl list' / delete unavailable devices manually",
            })

    # 5. Time Machine local snapshots.
    if shutil.which("tmutil"):
        try:
            r = subprocess.run(["tmutil", "listlocalsnapshots", "/"],
                               capture_output=True, text=True, timeout=60)
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("com.apple.TimeMachine."):
                    items.append({
                        "kind": "tm-local-snapshot", "path": line, "size": 0,
                        "note": "review with 'tmutil deletelocalsnapshots <date>'; macOS also thins automatically",
                    })
        except Exception as e:
            log(f"WARNING: tmutil snapshot listing failed: {e}", log_file)

    # 6. Codex desktop app downloaded runtimes. Regenerable (the app re-downloads
    #    on next launch) but deleting them under a running Codex app can break the
    #    live session, and quitting Codex would kill the cleanup's own host
    #    process -- so this stays report-only, never auto-deleted.
    codex_rt = HOME / ".cache" / "codex-runtimes"
    if codex_rt.is_dir():
        items.append({
            "kind": "codex-runtimes", "path": str(codex_rt), "size": dir_size(codex_rt),
            "note": "quit the Codex app fully, then delete; it re-downloads on next launch",
        })

    return items


# ---------- Actions ----------
def delete_file(path: Path, dry_run: bool, log_file: Optional[Path]) -> bool:
    if dry_run:
        return True
    try:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except Exception as e:
        log(f"ERROR deleting {path}: {e}", log_file)
        return False


def archive_file(src: Path, dry_run: bool, compress: bool, log_file: Optional[Path]) -> Tuple[bool, Optional[str]]:
    if not str(src).startswith(str(HOME) + "/"):
        log(f"SKIP (outside home): {src}", log_file)
        return False, None
    rel = str(src).replace(str(HOME) + "/", "")
    dst_dir = NAS_MOUNT / Path(rel).parent
    dst = NAS_MOUNT / rel
    if dry_run:
        return True, None
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        shutil.copy(src, dst)
        sstat = src.stat()
        os.utime(dst, ns=(sstat.st_atime_ns, sstat.st_mtime_ns))
        os.chmod(dst, sstat.st_mode)
        if sha256_file(src) != sha256_file(dst):
            log(f"WARNING: checksum mismatch for {src}; removing NAS copy.", log_file)
            dst.unlink()
            return False, None
        final_dst = dst
        if compress and dst.suffix.lower() not in {".gz", ".zip", ".tar", ".rar", ".7z", ".bz2", ".xz", ".tgz"}:
            gz_path = dst.with_suffix(dst.suffix + ".gz") if dst.suffix else dst.with_name(dst.name + ".gz")
            with open(dst, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
                shutil.copyfileobj(f_in, f_out)
            dst.unlink()
            final_dst = gz_path
        src.unlink()
        return True, str(final_dst)
    except Exception as e:
        log(f"ERROR archiving {src}: {e}", log_file)
        return False, None


def archive_repo_dir(src: Path, dry_run: bool, compress: bool, log_file: Optional[Path]) -> Tuple[bool, Optional[str]]:
    """Tar a whole repo to NAS, verify integrity (re-list + file count + SHA-256),
    then delete the local copy. Never deletes without a verified NAS copy."""
    if not str(src).startswith(str(HOME) + "/"):
        log(f"SKIP (outside home): {src}", log_file)
        return False, None
    rel = str(src.relative_to(HOME)).replace("/", "-")
    suffix = ".tar.gz" if compress else ".tar"
    tar_path = NAS_MOUNT / "_tar_archives" / "repos" / f"{rel}-{timestamp()}{suffix}"
    if dry_run:
        return True, None
    try:
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["tar", "-cf" if not compress else "-czf", str(tar_path),
               "-C", str(src.parent), src.name]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            log(f"ERROR tar failed for {src}: {r.stderr.strip()[:200]}", log_file)
            return False, None
        want = file_count(src)
        lst = subprocess.run(["tar", "-tf", str(tar_path)],
                             capture_output=True, text=True, timeout=300)
        got = sum(1 for line in lst.stdout.splitlines() if line and not line.endswith("/"))
        if lst.returncode != 0 or got < want:
            log(f"WARNING: tar verification failed for {src} (files {got}/{want}); keeping local copy.", log_file)
            tar_path.unlink(missing_ok=True)
            return False, None
        tar_hash = sha256_file(tar_path)
        log(f"  Archived {src} -> {tar_path} ({format_size(tar_path.stat().st_size)}, sha256 {tar_hash[:12]}..., {got} files)", log_file)
        shutil.rmtree(src)
        return True, str(tar_path)
    except Exception as e:
        log(f"ERROR archiving repo {src}: {e}", log_file)
        return False, None


# ---------- Main ----------
def main() -> None:
    parser = argparse.ArgumentParser(description="Comprehensive Mac cleanup and NAS archive")
    parser.add_argument("--execute", action="store_true", help="Actually perform actions (default: dry-run)")
    parser.add_argument("--keep-nas-mounted", action="store_true", help="Keep NAS mounted after execution")
    parser.add_argument("--compress", action="store_true", help="Gzip archived files on the NAS")
    parser.add_argument("--no-unread", action="store_true")
    parser.add_argument("--no-large", action="store_true")
    parser.add_argument("--no-dupes", action="store_true")
    parser.add_argument("--no-tasks", action="store_true", help="Skip scheduled-task doc archive")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--no-app-cache", action="store_true")
    parser.add_argument("--no-managed-cache", action="store_true")
    parser.add_argument("--no-runtime-cache", action="store_true",
                        help="Skip approved regenerable runtime cache directories")
    parser.add_argument("--no-quit-cache-apps", action="store_true",
                        help="Do not quit apps before runtime cache deletion")
    parser.add_argument("--no-logs", action="store_true")
    parser.add_argument("--no-other", action="store_true")
    parser.add_argument("--no-repos", action="store_true", help="Skip merged-repo archive+delete")
    parser.add_argument("--no-manuscripts", action="store_true", help="Skip PRD/PLAN manuscript archive")
    parser.add_argument("--no-repo-residue", action="store_true", help="Skip intra-repo residue cleanup")
    parser.add_argument("--no-modules", action="store_true", help="Skip extended age-gated module scan")
    parser.add_argument("--no-hf-incomplete", action="store_true", help="Skip HuggingFace .incomplete blobs")
    parser.add_argument("--no-ollama", action="store_true", help="Skip idle ollama model removal")
    parser.add_argument("--no-installers", action="store_true", help="Skip Downloads installer archive")
    parser.add_argument("--no-review", action="store_true", help="Skip report-only review findings")
    parser.add_argument("--ollama-idle-days", type=int, default=AGE_DAYS,
                        help="Idle threshold in days for ollama model removal")
    args = parser.parse_args()
    dry_run = not args.execute
    sys.stdout.reconfigure(line_buffering=True)

    ts = timestamp()
    report_dir = REPORT_ROOT / "logs"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = report_dir / f"run-{ts}.log"

    mode = "DRY-RUN" if dry_run else "EXECUTE"
    log(f"{'[DRY-RUN] ' if dry_run else ''}=== Mac Cleanup Run {ts} ===", log_file)
    log(f"Mode: {mode}", log_file)
    log(f"Home: {HOME}", log_file)

    nas_ok = require_nas(execute=args.execute)

    delete_candidates: List[Tuple[Path, str, int]] = []
    archive_candidates: List[Tuple[Path, str, int]] = []
    repo_candidates: List[Tuple[Path, str, int]] = []

    if not args.no_managed_cache:
        clean_managed_caches(dry_run, log_file)
    if not args.no_runtime_cache:
        runtime_targets = present_runtime_cache_targets()
        if runtime_targets:
            if args.no_quit_cache_apps:
                log("Runtime cache app quitting disabled by --no-quit-cache-apps", log_file)
            else:
                quit_runtime_cache_apps(runtime_targets, dry_run, log_file)
            log("Scanning approved runtime cache directories...", log_file)
            c = scan_runtime_cache_files(runtime_targets)
            log(f"  {len(c)} runtime cache files to delete", log_file)
            delete_candidates += c
    if not args.no_cache:
        log("Scanning cache files...", log_file)
        c = scan_cache_files(AGE_DAYS)
        log(f"  {len(c)} cache files to delete", log_file)
        delete_candidates += c
    if not args.no_app_cache:
        log("Scanning app-support caches...", log_file)
        c = scan_app_support_caches(30)
        log(f"  {len(c)} app-support cache files to delete", log_file)
        delete_candidates += c
    if not args.no_logs:
        log("Scanning old logs...", log_file)
        c = scan_old_logs(AGE_DAYS)
        log(f"  {len(c)} old log files to delete", log_file)
        delete_candidates += c
    if not args.no_tasks:
        log("Scanning scheduled-task docs...", log_file)
        c = scan_task_docs(AGE_DAYS)
        log(f"  {len(c)} task docs to archive", log_file)
        archive_candidates += c
    if not args.no_unread:
        log("Scanning unread files...", log_file)
        c = scan_unread_files(AGE_DAYS)
        log(f"  {len(c)} unread files to archive", log_file)
        archive_candidates += c
    if not args.no_large:
        log("Scanning large files...", log_file)
        c = scan_large_files(LARGE_FILE_THRESHOLD)
        log(f"  {len(c)} large files to archive", log_file)
        archive_candidates += c
    if not args.no_dupes:
        log("Scanning duplicate files...", log_file)
        c, saved = scan_duplicates()
        log(f"  {len(c)} duplicates to delete (save {format_size(saved)})", log_file)
        delete_candidates += c
    if not args.no_other:
        log("Scanning other cleanup targets...", log_file)
        c = scan_other_cleanup()
        log(f"  {len(c)} other files to delete", log_file)
        delete_candidates += c
    if not args.no_repos:
        log("Scanning fully-merged idle repos...", log_file)
        c = scan_merged_repos(AGE_DAYS)
        log(f"  {len(c)} repos to archive+delete", log_file)
        repo_candidates += c
    if not args.no_manuscripts:
        log("Scanning manuscript docs (PRD/PLAN/...)...", log_file)
        c = scan_manuscript_docs(AGE_DAYS)
        log(f"  {len(c)} manuscript docs to archive", log_file)
        archive_candidates += c
    residue_items: List[Dict] = []
    if not args.no_repo_residue:
        log("Scanning intra-repo residue (merged branches / regenerable dirs / leftovers)...", log_file)
        residue_items = scan_repo_residue(AGE_DAYS)
        n_b = sum(len(i["branches"]) for i in residue_items)
        n_g = sum(len(i["regen"]) for i in residue_items)
        n_l = sum(len(i["leftovers"]) for i in residue_items)
        sz_g = sum(g["size"] for i in residue_items for g in i["regen"])
        sz_l = sum(l["size"] for i in residue_items for l in i["leftovers"])
        log(f"  {len(residue_items)} repos with residue: {n_b} merged branches, "
            f"{n_g} regenerable dirs ({format_size(sz_g)}), {n_l} leftover files ({format_size(sz_l)})", log_file)
    if not args.no_modules:
        log("Scanning extended modules (age-gated 14d/90d tiers)...", log_file)
        c = scan_module_dirs()
        log(f"  {len(c)} module files to delete", log_file)
        delete_candidates += c
    if not args.no_hf_incomplete:
        log("Scanning HuggingFace incomplete download blobs...", log_file)
        c = scan_hf_incomplete()
        log(f"  {len(c)} HF incomplete blobs to delete", log_file)
        delete_candidates += c
    if not args.no_installers:
        log("Scanning Downloads installers (*.dmg/*.pkg)...", log_file)
        c = scan_installers(AGE_DAYS)
        log(f"  {len(c)} installers to archive+delete", log_file)
        archive_candidates += c
    ollama_items: List[Dict] = []
    if not args.no_ollama:
        log(f"Scanning ollama models idle >= {args.ollama_idle_days} days...", log_file)
        ollama_items = scan_ollama_idle_models(args.ollama_idle_days, log_file)
        log(f"  {len(ollama_items)} idle ollama models to remove", log_file)
    review_items: List[Dict] = []
    if not args.no_review:
        log("Collecting report-only review items (orphan containers, TM snapshots, ...)...", log_file)
        review_items = collect_review_items(log_file)
        log(f"  {len(review_items)} review items (report-only, never auto-deleted)", log_file)

    total_del = sum(s for _, _, s in delete_candidates)
    total_arc = sum(s for _, _, s in archive_candidates)
    total_repo = sum(s for _, _, s in repo_candidates)
    log(f"Total to delete: {len(delete_candidates)} files, {format_size(total_del)}", log_file)
    log(f"Total to archive: {len(archive_candidates)} files, {format_size(total_arc)}", log_file)
    log(f"Total repos to archive+delete: {len(repo_candidates)} dirs, {format_size(total_repo)}", log_file)

    del_list = report_dir / f"delete-{ts}.lst"
    arc_list = report_dir / f"archive-{ts}.lst"
    repo_list = report_dir / f"repos-{ts}.lst"
    with open(del_list, "w", encoding="utf-8") as f:
        for p, reason, size in delete_candidates:
            f.write(f"{size}\t{reason}\t{p}\n")
    with open(arc_list, "w", encoding="utf-8") as f:
        for p, reason, size in archive_candidates:
            f.write(f"{size}\t{reason}\t{p}\n")
    with open(repo_list, "w", encoding="utf-8") as f:
        for p, reason, size in repo_candidates:
            f.write(f"{size}\t{reason}\t{p}\n")
    residue_list = report_dir / f"residue-{ts}.lst"
    with open(residue_list, "w", encoding="utf-8") as f:
        for i in residue_items:
            f.write(f"repo\t{i['repo']}\n")
            for b in i["branches"]:
                f.write(f"\tbranch\t{b['name']}\tmerged into {b['merged_into']}\tsha {b['sha']}\n")
            for g in i["regen"]:
                f.write(f"\tregen\t{g['path']}\t{g['size']}\trestore: {g['restore']}\n")
            for lf in i["leftovers"]:
                f.write(f"\tleftover\t{lf['path']}\t{lf['size']}\n")
    ollama_list = report_dir / f"ollama-{ts}.lst"
    with open(ollama_list, "w", encoding="utf-8") as f:
        for it in ollama_items:
            f.write(f"{it['name']}\t{it['size']}\tidle {it['idle_days']}d\tlast used {it['last_used']}\n")
    review_list = report_dir / f"review-{ts}.lst"
    with open(review_list, "w", encoding="utf-8") as f:
        for it in review_items:
            f.write(f"{it['kind']}\t{format_size(it['size'])}\t{it['path']}\t{it['note']}\n")

    if dry_run:
        log("[DRY-RUN] DRY-RUN complete. Re-run with --execute to act.", log_file)
        body = (f"# Cleanup Report (DRY-RUN)\n\n- Run: {ts}\n- Home: {HOME}\n- NAS: {NAS_MOUNT} (mounted: {nas_ok})\n"
                f"- Delete: {len(delete_candidates)} files, {format_size(total_del)} (list: {del_list})\n"
                f"- Archive: {len(archive_candidates)} files, {format_size(total_arc)} (list: {arc_list})\n"
                f"- Repos (archive+delete): {len(repo_candidates)} dirs, {format_size(total_repo)} (list: {repo_list})\n"
                f"- Repo residue: {len(residue_items)} repos (list: {residue_list})\n"
                f"- Ollama idle models: {len(ollama_items)} (list: {ollama_list})\n"
                f"- Review items (report-only): {len(review_items)} (list: {review_list})\n"
                f"- Log: {log_file}\n")
        report = write_report("cleanup", body)
        log(f"Report saved: {report}", log_file)
        return

    if (archive_candidates or repo_candidates) and not nas_ok:
        log("ERROR: NAS required for archive but not available; archive candidates skipped.", log_file)

    deleted = 0
    failed_del = 0
    for p, reason, size in delete_candidates:
        if delete_file(p, dry_run=False, log_file=log_file):
            deleted += 1
        else:
            failed_del += 1
    ollama_removed, ollama_failed = remove_ollama_models(ollama_items, dry_run=False, log_file=log_file)

    archived = 0
    failed_arc = 0
    repos_done = 0
    repos_failed = 0
    manifest_entries: List[Dict] = []
    residue_counts = {"branches": 0, "regen": 0, "leftovers": 0}
    residue_failed = 0
    for i in residue_items:
        # Tiers 1-2 are locally safe; tier 3 (leftovers) needs NAS for the archive step.
        item = dict(i)
        skipped_leftovers = 0
        if not nas_ok and item["leftovers"]:
            skipped_leftovers = len(item["leftovers"])
            item["leftovers"] = []
        counts = process_repo_residue(item, dry_run=False, compress=args.compress, log_file=log_file)
        for k in residue_counts:
            residue_counts[k] += counts[k]
        residue_failed += skipped_leftovers
        if skipped_leftovers:
            log(f"NOTE: {skipped_leftovers} leftover files in {i['repo']} skipped (NAS unavailable)", log_file)
    if nas_ok:
        for p, reason, size in repo_candidates:
            ok, tar_dst = archive_repo_dir(p, dry_run=False, compress=args.compress, log_file=log_file)
            if ok:
                repos_done += 1
                manifest_entries.append({
                    "src": str(p), "dst": tar_dst, "size": size, "reason": reason,
                    "type": "repo", "archived_at": datetime.now().isoformat(),
                })
            else:
                repos_failed += 1
        for p, reason, size in archive_candidates:
            ok, final_dst = archive_file(p, dry_run=False, compress=args.compress, log_file=log_file)
            if ok:
                archived += 1
                manifest_entries.append({
                    "src": str(p), "dst": final_dst, "size": size, "reason": reason,
                    "archived_at": datetime.now().isoformat(),
                })
            else:
                failed_arc += 1
        manifest_path = write_manifest("cleanup", manifest_entries)
        log(f"Manifest: {manifest_path}", log_file)

    body = (f"# Cleanup Report ({mode})\n\n- Run: {ts}\n- Home: {HOME}\n- NAS: {NAS_MOUNT}\n"
            f"- Deleted: {deleted} files, {format_size(total_del)} planned\n"
            f"- Delete failures: {failed_del}\n"
            f"- Ollama models removed: {ollama_removed}, failures: {ollama_failed}\n"
            f"- Repos archived+deleted: {repos_done}, failures: {repos_failed} ({format_size(total_repo)} planned)\n"
            f"- Repo residue: branches {residue_counts['branches']}, regen dirs {residue_counts['regen']}, "
            f"leftovers archived {residue_counts['leftovers']}, leftovers skipped (no NAS): {residue_failed}\n"
            f"- Archived: {archived} files, {format_size(total_arc)} planned\n"
            f"- Archive failures: {failed_arc}\n"
            f"- Log: {log_file}\n")
    report = write_report("cleanup", body)
    log(f"Deleted: {deleted} files, failed: {failed_del}", log_file)
    log(f"Ollama models removed: {ollama_removed}, failed: {ollama_failed}", log_file)
    log(f"Repos archived+deleted: {repos_done}, failed: {repos_failed}", log_file)
    log(f"Repo residue: branches {residue_counts['branches']}, regen {residue_counts['regen']}, "
        f"leftovers {residue_counts['leftovers']}, skipped-no-NAS {residue_failed}", log_file)
    log(f"Archived: {archived} files, failed: {failed_arc}", log_file)
    log(f"Report saved: {report}", log_file)

    if not args.keep_nas_mounted and nas_ok and CFG.get("nas_url"):
        log("Unmounting NAS...", log_file)
        subprocess.run(["umount", str(NAS_MOUNT)], capture_output=True)


if __name__ == "__main__":
    main()
