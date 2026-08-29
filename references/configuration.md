# Configuration

All scripts in this skill derive paths from `$HOME` and read machine-specific settings from
a single config. Nothing in the skill directory itself needs editing for a new machine.

## Resolution order

Later sources override earlier ones:

1. Built-in defaults (see table below)
2. `~/.config/mac-archive/config.json`
3. Environment variables

## Config file

Create `~/.config/mac-archive/config.json` on the new machine:

```json
{
  "nas_mount": "~/Volumes/MacArchive",
  "nas_url": "//myuser@192.168.1.100/MacArchive",
  "report_root": "~/Documents/MacArchiveReports",
  "mail_root": "~/Library/Mail",
  "age_days": 60,
  "large_file_mb": 200,
  "email_cutoff": "2025-01-01",
  "archive_items": [
    { "src": "~/Desktop/old-project", "keep_local": false },
    { "src": "~/Documents/keep-this", "keep_local": true }
  ]
}
```

| Key | Default | Meaning |
|-----|---------|---------|
| `nas_mount` | `~/Volumes/MacArchive` | Local mount point used for archives and manifests. |
| `nas_url` | none | SMB URL used for auto-mount. Never embed a password; rely on Keychain. |
| `report_root` | `~/Documents/MacArchiveReports` | Where reports/logs/lists are written. |
| `mail_root` | `~/Library/Mail` | Apple Mail data root. |
| `age_days` | 60 | Age threshold for logs, caches, unread files, attachments. |
| `large_file_mb` | 200 | Threshold for the large-file archive scan. |
| `email_cutoff` | 2025-01-01 | Cutoff date for archive_old_emails.py. |
| `archive_items` | [] | Directories for archive_dirs.py. `keep_local: true` archives but does not delete. |

## Environment variables (override config)

`MAC_ARCHIVE_NAS_MOUNT`, `MAC_ARCHIVE_NAS_URL`, `MAC_ARCHIVE_REPORT_ROOT`,
`MAC_ARCHIVE_MAIL_ROOT`, `MAC_ARCHIVE_AGE_DAYS`, `MAC_ARCHIVE_LARGE_FILE_MB`,
`MAC_ARCHIVE_EMAIL_CUTOFF`

## No NAS / different NAS

- If no NAS is needed, only run dry-run and delete-only sections (logs, caches, duplicates):
  they work without the NAS.
- If a NAS exists but mounts elsewhere, set `nas_mount` to that path or export
  `MAC_ARCHIVE_NAS_MOUNT`.
- If the NAS should be auto-mounted, set `nas_url` (Keychain must hold the credentials:
  `open "smb://server/share"` once in Finder, or `security add-internet-password`).
- If the NAS is unreachable, archive operations abort without changing local files;
  delete-only sections still run.

## Legacy compatibility

If `~/.config/mac-archive/config.sh` exists (older shell-based setup), its `ARCHIVE_ROOT`,
`REPORT_ROOT`, and `ARCHIVE_AGE_DAYS` values are read as defaults when `config.json` is absent.
