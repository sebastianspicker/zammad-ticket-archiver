# 03 – Data Model

This document describes the runtime data objects that matter for rendering, signing/timestamping, and
storage/audit output.

## Snapshot (render input)

The renderer operates on a strict “snapshot” object:
- it is built from Zammad API data
- it is the single source of truth for HTML templates
- it deliberately keeps attachments as **metadata only** (no embedded binary content)

Example (human-friendly):
- [`examples/ticket-snapshot.sample.json`](../examples/ticket-snapshot.sample.json)

The core fields used by the shipped templates are documented in [`05-pdf-rendering.md`](05-pdf-rendering.md).

### Path-related fields

The following fields drive output placement (see [`04-path-policy.md`](04-path-policy.md)):
- `ticket.custom_fields.archive_path` (string split on `>` or list of strings)
- `ticket.custom_fields.archive_user_mode` (`owner` | `current_agent` | `fixed`)
- (only for `fixed`) `ticket.custom_fields.archive_user`

## Audit sidecar JSON (storage metadata)

For every archived PDF, the service writes an audit record next to it:

`<pdf-filename>.json`

Purpose:
- provide a machine-readable record of what was written and when
- store the SHA-256 checksum for integrity checks
- store signing/timestamp flags and (best-effort) signer certificate fingerprint

Fields (current):
- `ticket_id` (int)
- `ticket_number` (string)
- `title` (string; empty string if missing)
- `created_at` (UTC timestamp when the archive record was created)
- `storage_path` (full path where the PDF was written)
- `sha256` (hex)
- `signing`:
  - `enabled` (bool)
  - `tsa_used` (bool)
  - `cert_fingerprint` (string; optional SHA-256 hex of signer certificate)
- `service`:
  - `name`
  - `version` (may be `"unknown"` in non-packaged deployments)
  - `python` (runtime Python version)

Operational note:
- The PDF signature validation scripts (`scripts/ops/verify-pdf.sh`) validate the embedded signature and
  timestamp token (if present). The audit sidecar checksum validates the on-disk file integrity.
