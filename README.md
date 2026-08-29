# mac-cleanup-archive

A portable macOS cleanup + NAS archive toolkit, built as a Codex/agent skill.
Everything defaults to **dry-run**; nothing is deleted without `--execute`,
and no local file is deleted before its NAS copy passes SHA-256 verification.

See [SKILL.md](SKILL.md) for the full documentation.

## Highlights

- Age-gated cleanup tiers: 14d for regenerable caches/logs, 90d for
  rebuild-costly data (Gradle, NuGet, Conan, iOS .ipa archives)
- Managed caches via the tools themselves: npm, yarn, pip, brew, uv, pnpm,
  pod, go, gem, composer, conan, plus dangling-only `docker system prune -f`
- HuggingFace interrupted-download residue (`*.incomplete` blobs only)
- Idle ollama model removal with a hardcoded exemption list and
  currently-loaded-model protection
- Git hygiene: fully-merged idle repos tarred to NAS and verified before
  deletion; merged branches, regenerable dirs, and leftover intermediates
  cleaned per-repo with a `CLEANUP-RECOVERY.md` manifest
- Downloads installer archiving (`.dmg`/`.pkg`), Mail attachment/email
  archiving, duplicate and large-file scans
- Report-only review findings (orphaned app containers, Time Machine local
  snapshots, iOS backups, simulator runtimes) that are never auto-deleted
- Works without a NAS: delete-only sections still run, archive sections skip
  safely

## Quick start

```bash
python3 scripts/analyze_storage.py   # read-only storage report
python3 scripts/mac_cleanup.py       # dry-run, writes review lists
python3 scripts/mac_cleanup.py --execute
```

Per-machine settings (NAS mount, thresholds) live in
`~/.config/mac-archive/config.json` — see
[references/configuration.md](references/configuration.md).

## Safety rules

1. Never delete without dry-run review.
2. Never delete locally before the NAS copy verifies (SHA-256).
3. Project roots, dotfiles, agent/tool runtime dependencies, and source files
   are protected automatically.
4. Actions needing sudo are logged and skipped, never forced.
