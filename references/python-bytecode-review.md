# Targeted review of historical Python bytecode mirrors

This is an inspection and approval protocol, **not an automatic cleanup section**.
Keep the default protection of `~/Library/Caches/com.apple.python/private/var`.
Do not remove that mapping, its ancestors, Python's entire cache root, or real system
temporary directories. No broad globs, implicit sudo, ownership repair, or service stops.

## Why ordinary cache scans can miss this

On some macOS Python builds, `sys.pycache_prefix` points to
`~/Library/Caches/com.apple.python` even without `PYTHONPYCACHEPREFIX` in the environment.
Query the actual interpreter; do not assume all Python installations behave alike.
The cache mirrors absolute source paths. Temporary pip/build/test source trees can
disappear while their compiled `.pyc` mirrors remain. Cache paths beginning with
`private/var` are not the real `/private/var` tree. Other subtrees may contain workspace
data and must not be classified as disposable by name alone.

A completed November 2026 audit found about 51.34 decimal GB and 4.39 million `.pyc`
files in one temp-path mirror. `pip-*` represented only 13.41 GB; non-pip temporary
mirrors represented 37.93 GB. These are a case example, not portable thresholds or
expected savings. A previously excluded subtree must be reported as an inventory gap,
not sudden growth.

## Read-only inspection

1. Discover the interpreter's cache prefix and actual temporary directory at runtime.
   Do not hardcode a username, a `/var/folders` hash, Python version, or bytecode magic.
   Do not execute cached bytecode or untrusted project startup files to inspect it.
2. Measure nonoverlapping components with `du -x -s -k`; bound runtime and record
   timeouts/permission failures as incomplete, never zero. Avoid parallel duplicate
   full-tree traversals. For millions of files, scan direct children incrementally,
   report progress, and retain completeness markers.
3. For each direct temp-mirror child, record the exact path, directory device/inode,
   regular-file count, logical and allocated sizes, newest file **and directory** mtime,
   extensions, links/special files, errors, and corresponding original-path existence.
   Use no-follow metadata and stay on the same device. Absence means confirmed ENOENT;
   permissions or any other uncertainty exclude the item. Check original paths before
   and after the scan.
4. An initial candidate must be nonempty, completely readable, only `.pyc`, have no
   symlinks, special files, or multiply-linked files, have no existing original temp
   child, and be older than the reviewed age threshold (normally at least 14 days).
   Sample bytecode magic per child against the actual interpreter without deserializing
   or executing it. Extension plus a sample is not proof of every file's semantics.
5. Check open files before and after with a bounded `lsof` query. Record command status,
   warnings and visibility. A current-UID query does not establish absence of root/other
   user holders. No open handle does not prove no logical consumer or future access.
   Exclude known holders; disclose unresolved visibility before approval, never silently
   elevate permissions. Retain original environments, recent updates and uncertain data.
6. Freeze the exact candidate list and SHA-256 digest with counts, bytes and exclusions.
   Never expand approval from `pip-*` to all temp mirrors implicitly; review each group.

## Separate execution approval and recovery

Present the exact frozen scope, evidence limits, exclusions, expected allocation and
recovery limits. Explicitly ask for a narrow exception to the protected-mapping rule
before permanent deletion. Prior cleanup approval or a large size alone is insufficient.
Retain default protection everywhere else and do not add candidates to unattended runs.

- Moving to Trash on the same filesystem preserves a recovery option but does **not**
  release disk space. Emptying Trash is a separate irreversible operation.
- Archiving to external storage/NAS requires verified content before local deletion;
  do not mount or send data elsewhere without the required authorization.
- Permanent deletion without backup needs informed approval. Bytecode can generally be
  rebuilt **if the source/environment is available**. Deleted temp sources mean exact
  old bytecode cannot be promised recoverable. Regeneration is not backup restoration.

## Execution and verification within approved scope

1. Validate frozen manifest digests and exact direct-child paths. Recheck directory
   identity, original-path absence, complete file types/mtime and holders. Skip changed
   or uncertain items; never broaden to a freshly discovered candidate.
2. Use no-follow, directory-descriptor-anchored operations where supported, and validate
   node identities/metadata immediately before removal. Do not follow path replacements
   or links. A mutation error must be logged as partial, not successful cleanup; stop
   and review instead of retrying broadly.
3. Journal per-item validation and outcomes; do not overwrite prior execution evidence.
   Record APFS container free capacity before/after, confirm deleted paths absent, and
   verify excluded items and mapping ancestors remain. A metadata fingerprint verifies
   unchanged observed metadata, not a full content hash.
4. Report allocation removed separately from actual free-space increase. APFS shared
   blocks, open files, concurrent writes and regeneration affect reclaim; verify rather
   than promising the sum of file allocations. Keep local audit paths, user identifiers,
   chat data and enterprise details out of public skill updates.

## macOS storage classification checks

Use `diskutil apfs list -plist` for volume allocation, `sysctl vm.swapusage` for live
swap, and snapshot commands for local snapshots. Modern swap may reside under
`/System/Volumes/VM`, not `/private/var/vm`. Check system assets and Preboot separately.
APFS clones/shared blocks can make `du` totals exceed actual volume use; apparent
duplicate Cryptex images are not deletion candidates. Do not infer exact Storage UI
category membership from a coincidental sum or count VM in two categories.

Large `top` MEM/CMPRS figures are accounting metrics, not additive physical RAM totals
or proof of a leak. Enterprise/security agents and logs require IT/application-managed
handling; do not disable agents, delete active swap or force log removal. A saved-work,
authorized reboot followed by baseline/growth measurement can distinguish temporary
swap accumulation from recurrence, without promising permanent savings.
