---
name: mac-cleanup-archive
description: Clean up macOS disk space and archive files to a NAS with verification. Use when the user asks to clean up/free disk space (清理空间/磁盘清理/无用文件删除/缓存清理), delete old logs (删除旧日志/运行日志), archive files to NAS (归档到NAS/备份到NAS/旧文件归档), clean Mail attachments or old emails (邮件附件清理/旧邮件归档), remove unused AI models or Chrome/VS Code caches, find duplicates or large files, or asks for a periodic Mac cleanup routine. Works on any Mac via $HOME-relative paths and runtime NAS mount detection; safe when NAS is absent or mounted elsewhere.
---

# Mac Cleanup Archive

## Overview

Portable macOS cleanup + NAS archive toolkit. All scripts default to **dry-run** and only act
with `--execute`. Everything derives from `$HOME`; the NAS mount point and machine-specific
settings come from config, not from the scripts themselves.

## Quick Start

```bash
SCRIPTS=<skill-dir>/scripts

# 1. Storage analysis (read-only, always safe)
python3 $SCRIPTS/analyze_storage.py

# 2. Full cleanup dry-run, review the lists, then execute
python3 $SCRIPTS/mac_cleanup.py
python3 $SCRIPTS/mac_cleanup.py --execute
```

Always dry-run first and read the generated `delete-*.lst` / `archive-*.lst` lists before
executing. Every run writes a report and a log under the report root (default
`~/Documents/MacArchiveReports/logs/`).

## Scripts

| Script | Purpose | Needs NAS |
|--------|---------|-----------|
| `analyze_storage.py` | Read-only top-directory / large-file report. | No |
| `mac_cleanup.py` | Orchestrator: caches, old logs (user agents + system), scheduled-task docs, unread files, large files, duplicates, managed caches. | Only for archive sections |
| `archive_mail_attachments.py` | Tar Mail attachments older than N days to NAS, verify, delete local. | Yes |
| `archive_old_emails.py` | Archive Apple Mail `.emlx` emails older than a cutoff date (parses `Date:` headers), verify, delete, rebuild Mail index. Quits Mail first in execute mode. | Yes |
| `archive_dirs.py` | Tar configured directories to NAS; per-item `keep_local` decides whether the local copy is deleted. | Yes |
| `cleanup_caches.py` | Targeted removals: Chrome on-device models, unused HuggingFace models, old VS Code Server versions. | No |
| `restore_archived_files.py` | Rescue pass: copy back project/config files that were archived by mistake. | Yes |

All scripts: `python3 <script> --execute` to act; without it they only report.

## mac_cleanup.py sections

Selective execution via flags: `--no-cache --no-app-cache --no-managed-cache --no-logs
--no-tasks --no-unread --no-large --no-dupes --no-other --no-repos --no-manuscripts
--no-repo-residue --no-modules --no-hf-incomplete --no-ollama --no-installers
--no-review`, plus `--ollama-idle-days N`, `--compress` to gzip archived copies and
`--keep-nas-mounted`.

Extra sections:

- **Merged repos** (`--no-repos` to skip): a git repo under `$HOME` (up to 3 levels, runtime
  dirs excluded) becomes a candidate only when ALL hold: a remote exists, working tree clean
  (incl. untracked), no stashes, no unpushed commits on any branch, every local branch merged
  into the remote default branch, and the last commit is older than the age threshold.
  Candidates are tarred to `NAS/_tar_archives/repos/`, integrity-verified (re-list + file
  count + SHA-256), and only then deleted locally. Requires NAS; skipped entirely without it.
- **Manuscript docs** (`--no-manuscripts` to skip): files named like PRD/PLAN/SPEC/DESIGN/
  ROADMAP/BLUEPRINT (`.md`/`.markdown`/`.txt`) whose mtime is older than the age threshold.
  If inside a git repo, the file must additionally be tracked, unmodified, and have no
  unpushed commits touching it (repos without a remote are excluded). Archived to NAS with
  SHA-256 verification before local deletion.
- **Repo residue** (`--no-repo-residue` to skip): three tiers inside each git repo:
  (1) local branches merged into the remote default branch are deleted (recoverable from
  remote); (2) regenerable dirs (node_modules/.venv/dist/build/.next/.turbo/target/
  __pycache__) are deleted only when the repo has been idle longer than the age threshold;
  (3) untracked leftover intermediates (`*.bak`, `*.orig`, `*.rej`, `*rollback*`) are
  archived to NAS with SHA-256 verification before local deletion (skipped without NAS).
  Every executed action is appended to `<repo>/CLEANUP-RECOVERY.md` with restore commands
  (branch SHA + refetch, reinstall/build command, NAS path), and that manifest file is
  ignored by the clean-tree check of the merged-repo section.
- **Extended modules** (`--no-modules` to skip): age-gated tiers borrowed from
  mac-cleanup/mac-cleanup-py, adapted to non-interactive use per PiusSunday/mac-cleanup.
  14d tier (regenerable): JetBrains/Adobe/Chrome/DriveFS/Steam/Minecraft/Lunar/Teams/
  Obsidian/EA/Chromium/Arc caches & logs, Xcode DerivedData + device logs, CoreSimulator
  logs, Dropbox cache, Composer/nsurlsessiond caches, Android/Kite/Cacher caches,
  `/private/var/folders` temp, wget leftovers, `~/*.hprof` heap dumps, and Trash items
  (`~/.Trash` + external-volume `.Trashes`, NAS mount excluded) older than 14d.
  Also in the 14d tier: bun install cache (`~/.bun/install/cache`).
  90d tier (rebuild-costly): Gradle caches, NuGet packages, Conan packages, iOS app
  `.ipa` archives. Files only, mtime-based; nothing younger than the tier is touched.
- **Managed caches**: the tool-command list also covers uv, pnpm, pod, go, gem,
  composer, conan, and a dangling-only `docker system prune -f` (never `-a`).
  bun's install cache is an age-gated module dir instead (bun 1.3 `bun pm cache rm`
  requires a package.json in cwd); the docker command is skipped automatically when
  the daemon is not running.
- **HF incomplete blobs** (`--no-hf-incomplete` to skip): only
  `~/.cache/huggingface/hub/**/blobs/*.incomplete` (interrupted downloads); complete
  blobs are never touched. Direct delete, no archive.
- **Idle ollama models** (`--no-ollama`, `--ollama-idle-days N`, default 60): models
  whose manifest mtime is older than the threshold are removed with `ollama rm` on
  execute. `qwen3.5:4b-q4_K_M` is hardcoded-exempt (openclaw heartbeat + memos
  summarizer); currently loaded models (`ollama ps`) and models without a local
  manifest are always skipped. Models share blobs, so freed space is only known
  after removal.
- **Downloads installers** (`--no-installers` to skip): `~/Downloads/**/*.dmg|.pkg`
  older than the age threshold, archived to NAS with SHA-256 verification before
  local deletion.
- **Review findings** (`--no-review` to skip): report-only, never auto-deleted —
  orphaned app containers (low-confidence heuristic), Telegram postbox cache, iOS
  device backups, Xcode Archives, CoreSimulator devices/runtimes, and Time Machine
  local snapshots. Written to `review-*.lst` for manual action.

Protected automatically: anything under a project root (dirs containing package.json/.git/
pyproject.toml etc.), dotfiles and shell/agent configs (`AGENTS.md`, `.ssh`, `.env` …),
runtime dependencies (node_modules, .venv, .git …), and source-code extensions are never
archived or deleted by the archive/dupes/unread/large scans.

## NAS behavior

- Scripts detect the mount at runtime (`nas_mount` config, env override, or auto-mount via
  `nas_url` using Keychain credentials).
- Without a NAS: delete-only sections still run; archive sections warn and skip (dry-run)
  or abort cleanly before touching files (execute).
- Every archive is SHA-256 verified on the NAS before any local deletion.

## Configuration

Per-machine settings (NAS path, NAS URL, report root, thresholds, archive items, email
cutoff) live in `~/.config/mac-archive/config.json` or `MAC_ARCHIVE_*` environment
variables. See [references/configuration.md](references/configuration.md) for the full
schema and setup on a new machine.

## Safety rules

1. Never delete without dry-run review.
2. Never delete locally before the NAS copy verifies (SHA-256).
3. Never touch openclaw/Codex/Claude runtime dependencies, project roots, or system
   configuration files.
4. System logs under `/var/log`, `/Library/Logs` require sudo; without it, log the failures
   and continue.
5. `archive_old_emails.py --execute` quits Mail, deletes Envelope Index files, and requires
   the user to restart Mail afterwards.

## Operational notes (learned 2026-09-04)

- Run targeted cleanup before the full orchestrator. Use `cleanup_caches.py` for Chrome
  on-device models; use `mac_cleanup.py` with all unrelated sections disabled when the
  intent is only managed package caches. Review the dry-run output before each execute run.
- A managed-cache command can partially succeed. Continue only after recording each
  command's exit status and re-measuring the cache. In particular, npm may fail with
  `EACCES` when root-owned entries exist under `~/.npm`; do not use sudo or change
  ownership implicitly. Leave the remaining entries for a separately authorized repair.
- Never recursively delete `/private/var/folders`. After the owning app exits, inspect the
  exact leftover subtree, confirm no `lsof` holders, and move only that subtree to
  `~/.Trash` so it remains recoverable. Do not use broad globs or delete the `T`, `C`, or
  `X` roots.
- Treat `~/Library/Caches/com.apple.python` as a sandbox/workspace, not a homogeneous
  cache. Do not remove its `private/var` mapping. In the default cleanup flow, only consider individual directories
  under `private/tmp` that are older than the selected age threshold, not open, and
  clearly tied to completed disposable work; move them to `~/.Trash` and verify the
  source path is absent.
- Reported size is not guaranteed reclaimable size: macOS privacy restrictions, open
  files, sparse/dynamic directories, and application regeneration can change totals.
- Enterprise/security logs under `/Library/Logs` require application or IT-managed
  rotation; never remove them as ordinary cache cleanup.

## Operational notes (learned 2026-11-10)

- A timed-out or excluded subtree is **unknown**, not zero. Complete bounded, resumable
  measurements before claiming a cache total; record skipped paths and permission errors.
  Python's central bytecode cache can retain millions of `.pyc` files mirroring deleted
  pip/build/test temp directories. Inspect non-`pip-*` mirrors too, not only pip caches.
- Read-only review of Python `private/var` mirrors is allowed; default deletion remains
  blocked. A narrow exception requires a reviewed, SHA-256-bound exact-child manifest
  and explicit informed approval of permanent deletion without guaranteed recovery.
  Never remove mapping ancestors, touch the real `/private/var`, or turn this exception
  into an automatic orchestrator rule. See [Python bytecode review](references/python-bytecode-review.md).
- For macOS/System Data diagnosis, compare APFS volume allocation, directory allocation,
  live swap, and snapshots separately. VM may be at `/System/Volumes/VM`; `du` can
  overcount APFS clones/shared blocks. A numerical match is not proof of Storage UI
  category membership. Do not double-count swap or hand-delete system/VM/Preboot files.

## Operational notes (learned 2026-08-29)

- `uv cache clean` cannot run while a long-lived consumer holds `~/.cache/uv/.lock` —
  on this machine claude-mem's `chroma-mcp` (persistent MCP server) runs from
  `~/.cache/uv/archive-v0/`. The script sets `UV_LOCK_TIMEOUT=30` so it fails fast
  and logs the lock instead of hanging. Do NOT use `uv cache clean --force`: it
  deletes the running service's environment. To reclaim the cache, stop the holder
  (it respawns on next MCP use and re-downloads its env), then clean.
- The docker prune entry is dangling-only and auto-skips when the daemon is down.
