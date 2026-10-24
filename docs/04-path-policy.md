# Path policy

Archive paths are parsed from ticket fields, sanitized, and confined under
`storage.root`.

## Input fields

### `archive_path`

Accepted formats:

- string with `>` separators, for example `Customers > ACME GmbH > 2026`
- list of strings, for example `["Customers", "ACME GmbH", "2026"]`

Empty fragments are ignored, but at least one segment must remain.

### `archive_user_mode`

Optionally controls the user directory segment. A missing value defaults to `owner`:

- `owner`: the ticket owner's login (an unassigned ticket fails)
- `current_agent`: the triggering agent from the webhook's `ticket.updated_by.login`, or a
  top-level `user.login` in a custom webhook payload; otherwise the fetched ticket's last editor,
  but never Chronikwerk's own API account
- `fixed`: `archive_user`

## Validation

Each segment must:

- be a string
- be non-empty after trimming
- not be `.` or `..`
- not contain `/`, `\`, or NUL
- be at most 64 characters

The archive path is limited to 10 segments. Violations are permanent processing
failures.

## Sanitization

After validation, segments are sanitized deterministically:

- Unicode normalized with NFKD
- combining marks removed
- whitespace collapsed to `_`
- only `A-Za-z0-9._-` kept
- other characters replaced with `_`
- repeated underscores collapsed

Examples:

| Input | Readable sanitized prefix |
| --- | --- |
| `Müller` | `Muller` |
| `Sales Team EMEA` | `Sales_Team_EMEA` |
| `客户` | `_` |

Sanitization is lossy. When a component's sanitized form differs from its raw
input, Chronikwerk appends a `-<32 hex characters>` suffix derived from the
SHA-256 digest of the raw UTF-8 input. Without it, distinct values such as
`alice+hr` and `alice?hr` could map to the same archive location. Components that
are already safe are left unchanged, and the readable prefix is truncated when
needed to keep the stored component within the 64-character limit.

## Root confinement

The final resolved path must stay under `storage.root`. Escape attempts fail the
job before writing.

Example output:

```text
/mnt/archive/john.doe/Customers/ACME_GmbH/2026/Ticket-123_2026-02-07.pdf
/mnt/archive/john.doe/Customers/ACME_GmbH/2026/Ticket-123_2026-02-07.pdf.json
```

The example uses components that are already safe; lossy inputs also carry the
disambiguation suffix described above.
