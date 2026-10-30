# Storage

The archive store writes each preservation package once under `storage.root`. The package format
is described in [03 - Data model](03-data-model.md); the decision is recorded in
[ADR 0013](adr/0013-write-once-archive-store.md).

## Output shape

Each revision is one directory, addressed by a key prefix:

```text
<storage.root>/<record_class>/<archive-user>/<archive-path>/Ticket-<number>/r<NNNN>_<revision_id>/
```

`<record_class>` comes from the ticket's record-class field and the retention policy file.
`<NNNN>` is the per-ticket revision number and `<revision_id>` a UUIDv7, so two captures on the same
day never share a key. Archive user and path segments follow the [path policy](04-path-policy.md).

The old layout (`Ticket-<number>_<timestamp>.pdf` with a `.json` sidecar beside it) is gone, and
`storage.filename_pattern` is ignored with a startup warning. Existing pairs are left untouched and
not imported. Tools or scripts that read the flat layout must change.

## Write-once behavior

The store is create-only. `ArchiveStore.begin_revision` returns a writer; the writer adds objects and
`finalize` makes the revision visible.

1. Objects are written into a private staging directory,
   `<storage.root>/.chronikwerk-staging/<hash of prefix>/`. The staging root carries a marker file
   (`.chronikwerk-staging`) and each prefix has a lock file held for the lifetime of the writer.
2. Each object is written to a temporary name, synced, and linked into place, so a crash leaves at
   most a temporary file, never a torn object. Writing identical content again counts as done;
   different content under the same key is a conflict (`store_conflict`).
3. After the pipeline has read every object back against the manifest, `finalize` refuses an empty
   revision, checks that the final prefix does not exist, publishes the staged directory with one
   rename, and makes it read-only (files `0440`, directories `0550`).
4. Resuming a published revision re-applies the modes, so a crash between rename and `chmod` is
   repaired.

A final prefix therefore either does not exist or holds a complete revision. The staging area lives
below `storage.root` because the rename must stay on one file system; do not put anything else
under `.chronikwerk-staging`.

Reads open files relative to the root without following symlinks. A symlink anywhere below the root
is an error, and key prefixes must not be nested.

## Retention and read-only modes

Chronikwerk computes `retain_until` from the record class in the retention policy file
(`retention.policy_path`) and records it in the package's `capture.json`, in the catalog
and in the Zammad success note. There are no built-in retention values.

Whether the store itself enforces that date is a separate fact, recorded per revision as
`retention_enforced`:

- **Filesystem store:** `retention_enforced` equals `storage.filesystem.worm_enforced`, which
  defaults to `false`. The setting is an operator assertion that the share enforces write-once
  retention itself, for example a WORM share. Chronikwerk cannot record `retain_until` on a plain
  file system and does not check the assertion.
- **Read-only modes are not retention.** The `0440`/`0550` modes stop accidental writes by other
  processes. They do not stop the owner, an administrator, or the share's own tools from changing
  modes or deleting files.
- **Plain filesystem: test use only.** On a share without enforced retention the service can still
  write revisions, but they are recorded with `retention_enforced` false, and the success note shows
`retention_enforced: no`. Use
  this for testing, not for records.
- **S3 Object Lock:** not available in this build. The settings exist (`storage.backend`,
  `storage.s3.*`), but no S3 store is implemented, so do not select `s3` yet.

Write permission for the service account on a WORM share is the operator's responsibility; the
service account should be able to create objects but not delete or overwrite them. Holds, expiry
and disposal are not built yet; see [08 - Operations](08-operations.md).

## Safety properties

- Path segments are validated and sanitized.
- Final paths are resolved under `storage.root`.
- Symlinks under the storage root are rejected before reads and writes.
- Objects are created once; a published revision is never replaced.
- Optional fsync is enabled by default with `storage.fsync=true`.
- Staging deletion is limited to entries inside the marked staging root: an aborted writer removes
  its own staging directory, and `FilesystemArchiveStore.cleanup_abandoned_staging` removes
  unlocked staging directories that have not been modified for a given age. Nothing outside the
  staging root is removed. No scheduled job calls the cleanup yet, so abandoned staging
  directories stay until a resume or an operator handles them.

## Operational checks

Before production use, verify:

- the mount exists and is read-write for the service UID/GID
- the service can create directories and files under `storage.root`, including
  `.chronikwerk-staging`
- enough free space and quota for packages (attachments are now stored; see `capture.max_*`)
- `GET /healthz?deep=true` reports writable storage
- one real capture produces a revision directory that `chronikwerk-admin verify --path` accepts
  (see [06 - Signing and timestamping](06-signing-and-timestamp.md))
- the journal path is on local disk, not on the archive share

If a request stops during commit, nothing incomplete enters the catalog or the final prefix. A
restart resumes from what the store holds, or discards the staged revision and captures again
(see [01 - Architecture](01-architecture.md)).

The archive commit completes before Chronikwerk applies `pdf:signed` or creates the success note. A
revision can therefore exist while the ticket still shows `pdf:processing` or `pdf:error`. Check the
journal state and the logs, and do not delete a valid revision because Zammad finalization failed;
a retry resolves to the existing revision when the ticket content has not changed.

## CIFS/SMB notes

CIFS/SMB durability and locking semantics depend on mount options, server behavior, and network
reliability. Treat the share as an operational dependency and monitor write failures.

The filesystem store relies on two behaviors that some SMB mounts do not provide: hard links
(objects are linked into place) and `flock` (the per-prefix lock). Test both on the target share
before use, with the mount options you will deploy, and with the service UID. The lock covers
writers on one host; it does not cover other hosts that share the archive or foreign processes
that create directories there.
