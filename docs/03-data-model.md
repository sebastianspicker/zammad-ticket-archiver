# Data model

This page describes the data objects that matter for rendering, storage, and
audit output.

## Snapshot

The renderer consumes a normalized snapshot built from Zammad ticket, article,
tag, and user data.

Example:

- [examples/ticket-snapshot.sample.json](../examples/ticket-snapshot.sample.json)

Core template fields:

- `ticket.id`
- `ticket.number`
- `ticket.title`
- `ticket.created_at`
- `ticket.updated_at`
- `ticket.customer`
- `ticket.owner`
- `ticket.tags`
- `ticket.custom_fields`
- `articles[]`
- `articles_total`
- `articles_omitted`

Each article may include attachment metadata (`filename`, `size`, content type,
and identifiers). The storage layer does not persist attachment binaries; only
the metadata is rendered in the PDF.

## Path fields

Path placement is derived from custom ticket attributes (`Admin -> System -> Objects -> Ticket`). The
service fetches the ticket with `GET /api/v1/tickets/{id}?expand=true`, where each attribute is a
top-level key named after its technical name (configurable under `fields.*`):

- `archive_path`
- optional `archive_user_mode` (defaults to `owner`)
- `archive_user` when mode is `fixed`

The archive user is a Zammad login:

- `owner`: the expanded `owner` login. An unassigned ticket (Zammad reports the system login
  `-`) has no owner and fails with an error note.
- `current_agent`: the agent who triggered the webhook (`ticket.updated_by.login` in Zammad's
  webhook body), otherwise the fetched ticket's last editor, which is also what `/retry` uses.
  It never resolves to the API account Chronikwerk itself uses (`GET /api/v1/users/me`): if
  that account is the last editor, the run fails and asks for the archive to be triggered
  from Zammad.
- `fixed`: the `archive_user` attribute.

Only these attributes reach `ticket.custom_fields` in the snapshot, keyed by their canonical
names (`archive_path`, `archive_user_mode`, `archive_user`) whatever `fields.*` configures.

See [04 - Path Policy](04-path-policy.md).

## Audit sidecar

For every archived PDF, the service writes a JSON sidecar next to the PDF:

```text
Ticket-123_2026-02-07.pdf
Ticket-123_2026-02-07.pdf.json
```

The sidecar records:

- ticket ID and number
- title
- archive timestamp
- storage path
- SHA-256 checksum
- signing/timestamp status
- service metadata
- article coverage (`total`, `included`, `omitted`, and `complete`)

The sidecar is for operational audit and integrity checks; it is not a durable
database.
