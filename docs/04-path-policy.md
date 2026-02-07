# 04 - Path Policy

This document defines how archive paths are parsed, validated, sanitized, and constrained under `storage.root`.

## 1. Input Fields

### `custom_fields.archive_path` (required)

Accepted formats:
- string with `>` separators, for example: `Customers > ACME GmbH > 2026`
- list of strings, for example: `[
  "Customers", "ACME GmbH", "2026"
]`

After trimming empty fragments, at least one segment must remain.

### `custom_fields.archive_user_mode` (optional, default `owner`)

Defines the first directory segment (`archive user`):
- `owner` -> `ticket.owner.login`
- `current_agent` -> webhook `payload.user.login`, fallback `ticket.updated_by.login`
- `fixed` -> `custom_fields.archive_user`

## 2. Segment Validation Rules

Applied to raw segments before sanitization:
- type must be string
- segment must not be empty
- segment must not be `.` or `..`
- segment must not contain `/`, `\\`, or NUL byte
- max segment length: `64`
- max depth for `archive_path`: `10`

Any violation causes a permanent processing failure.

## 3. Sanitization Rules

After validation, each segment is sanitized deterministically:
- Unicode normalization: NFKD
- combining marks removed
- whitespace collapsed to `_`
- allowed characters: `[A-Za-z0-9._-]`
- all other characters replaced with `_`
- repeated underscores collapsed

Examples:
- `Müller` -> `Muller`
- `Sales Team / EMEA` -> `Sales_Team_EMEA`
- `客户` -> `_`
- `🤷` -> `_`
- fullwidth dot traversal attempt `．．` -> rejected (normalizes to `..` then fails validation)

## 4. Root Containment and Prefix Policy

### Root containment

Resolved target path must remain under `storage.root`.
If a target escapes root, write is rejected.

### Optional allow-list prefixes

`storage.path_policy.allow_prefixes` can restrict allowed archive path prefixes.

Prefix entries may use either separator style:
- `Customers > ACME GmbH`
- `Customers/ACME GmbH`

Comparison is done on sanitized segments.

## 5. Filename Policy

Filename template key:
- `storage.path_policy.filename_pattern`

Default:
- `Ticket-{ticket_number}_{timestamp_utc}.pdf`

Supported placeholders:
- `{ticket_number}`
- `{timestamp_utc}` (date string provided by job: `YYYY-MM-DD`)
- `{date_utc}` (alias)

Validation constraints:
- single segment only (no path separators)
- max length `255`
- no NUL bytes

## 6. Final Output Layout

Given `storage.root=/mnt/archive`:

`/mnt/archive/<archive_user>/<archive_path...>/<filename>.pdf`

Audit sidecar:

`/mnt/archive/<archive_user>/<archive_path...>/<filename>.pdf.json`

## 7. Worked Example

Inputs:
- `archive_user_mode=owner`
- `ticket.owner.login=john.doe@example.local`
- `archive_path=Customers > ACME GmbH > 2026`
- `ticket_number=123456`
- date: `2026-02-07`

Outputs:
- user dir: `john.doe_example.local`
- path: `Customers/ACME_GmbH/2026`
- PDF:
  `/mnt/archive/john.doe_example.local/Customers/ACME_GmbH/2026/Ticket-123456_2026-02-07.pdf`
- sidecar:
  `/mnt/archive/john.doe_example.local/Customers/ACME_GmbH/2026/Ticket-123456_2026-02-07.pdf.json`
