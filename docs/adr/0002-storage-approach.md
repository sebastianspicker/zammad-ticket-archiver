# ADR 0002: Storage approach (network share)

## Status

Accepted (implemented)

## Context

Archived PDFs must be written to a shared storage location (commonly CIFS/SMB). The system must avoid
partial/corrupted files if it crashes mid-write, and operations should be able to manage mounts and
credentials safely.

## Decision

- Mount the CIFS/SMB share on the **host** (systemd/fstab).
- Bind-mount the host mountpoint into the container and use it as `STORAGE_ROOT`.
- Write archive outputs using an atomic replace strategy:
  - create temp file in target directory
  - write bytes + `fsync`
  - `os.replace` to the final path
  - best-effort directory `fsync`

## Consequences

- Avoids privileged containers and reduces the blast radius of mount credentials.
- Ops owns mount options, credentials, ACLs, and availability.
- Atomic writes reduce the risk of “half PDFs” after crashes.
- The archive share must be treated as part of the trusted environment:
  - restrict delete/overwrite permissions (append-only policy when possible)
  - back up/snapshot regularly

See also:
- [`../07-storage.md`](../07-storage.md) (ops details)
- [`../08-operations.md`](../08-operations.md) (permission troubleshooting)
