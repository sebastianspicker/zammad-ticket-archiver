# ADR 0013: Write-once archive store and enforced retention

## Status

Accepted (2026-10-30); implementation in progress. Replaces the storage layout described in
`docs/07-storage.md` before this date.

## Context

Chronikwerk writes `Ticket-<number>_<UTC date>.pdf` and its sidecar with atomic replacement. A
second capture on the same day overwrites the first, and nothing stops the service account or an
administrator from deleting or changing archived files. The HfMT design requires that accepted
revisions cannot be overwritten or deleted by the capture identity, and that retention is
enforced by the store itself.

## Decision

- Packages are stored under
  `<record_class>/<archive_user>/<archive_path…>/Ticket-<number>/r<NNNN>_<revision_id>/`, once,
  through an `ArchiveStore` protocol with create-only writes.
- Two stores implement it. The filesystem store stages a revision under a marked staging root,
  reads it back, renames it into place and makes it read-only; it reports
  `retention_enforced = storage.filesystem.worm_enforced` (an operator assertion for shares with
  WORM). The S3 store writes with Object Lock (`COMPLIANCE` by default) through the optional
  `boto3` extra `s3`, and refuses to start against a bucket without Object Lock.
- Commit order: content files with a short staging retention, then manifest, seal and token,
  read back every object, extend retention to the record-class date and confirm it, then write
  the catalog row and update Zammad. Each step is recorded in the journal (ADR 0011) and is
  idempotent, so an interrupted commit resumes.
- Retention comes from a required policy file (`retention.policy_path`). There are no built-in
  retention values. Holds and disposal run through `chronikwerk-admin` with custodian credentials
  that the service process never holds.
- The breaking layout change is accepted for the alpha. `storage.filename_pattern` is ignored with
  a startup warning. Existing `Ticket-<n>_<date>.pdf` pairs are left untouched and not imported.

## Consequences

- Same-day captures no longer overwrite each other.
- An interrupted S3 upload leaves objects that expire after `storage.s3.staging_retention_days`
  and are reported by reconciliation; nothing incomplete enters the catalog.
- A plain file share without WORM is suitable only for testing; revisions stored there are marked
  "retention not enforced".
- Tools or scripts that read the old flat layout must change.
