# 07 - Storage

This document describes how archive files are written and what storage assumptions are required.

## 1. Output Files

Each successful ticket run writes:
- one PDF file
- one sidecar JSON file (`<pdf_filename>.json`)

Output root is configured by:
- `storage.root` / `STORAGE_ROOT`

Layout:
- `<storage.root>/<archive_user>/<archive_path...>/<filename>.pdf`
- sidecar next to PDF: `<filename>.pdf.json`

Path building and validation:
- `src/zammad_pdf_archiver/adapters/storage/layout.py`
- `src/zammad_pdf_archiver/domain/path_policy.py`

## 2. Permissions Model

Default container user:
- UID/GID `10001:10001`

Required permissions on target filesystem:
- execute (`x`) on parent directories
- write (`w`) in destination directory
- create/remove temporary files (when atomic writes enabled)

For CIFS/SMB mounts, share ACLs and UID/GID mapping must permit these operations.

## 3. Atomic Write Behavior

With `storage.atomic_write=true`:
1. create temp file in destination directory
2. write bytes and flush
3. optional file `fsync` (`storage.fsync=true`)
4. `os.replace(temp, target)`
5. best-effort directory `fsync`

With `storage.atomic_write=false`:
- write directly to target with truncate/create semantics

Implementation:
- `src/zammad_pdf_archiver/adapters/storage/fs_storage.py`

## 4. Path Safety and Symlink Defense

Before writing, storage layer enforces:
- final path must remain under `storage.root`
- no symlink traversal under storage root path components
- destination directory creation when missing

This reduces path traversal and symlink abuse risk.

**Residual risk (TOCTOU):** The symlink check runs before the write. A symlink could be created between the check and the write. For high-assurance deployments, use a dedicated mount or filesystem controls; see [09-security.md](09-security.md).

## 5. CIFS/SMB Deployment Assumptions

Recommended production pattern:
1. mount share on host OS
2. bind-mount host path into container as `STORAGE_ROOT`

Why:
- keeps mount credentials/lifecycle outside container
- avoids privileged mount operations in app container

Helper script:
- `scripts/ops/mount-cifs.sh`

Treat helper script as baseline only; review options for your environment.

## 6. Operational Checklist

- confirm effective `STORAGE_ROOT` path
- confirm mount is read/write
- verify UID/GID mapping for runtime user
- verify ACLs on all parent directories
- verify quota/free space

If write failures continue, check ticket error note and service logs, then follow:
- [`08-operations.md`](08-operations.md)

## 7. Durability and Integrity Notes

The service writes checksums and optional signatures, but does not enforce immutable storage policy itself.

For archive-grade operation, use storage-platform controls:
- snapshots/versioning
- append-only or tamper-evident controls
- periodic checksum/signature verification
