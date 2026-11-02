# Data model

This page describes the data objects that matter for rendering, storage, and
the preservation package.

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
and identifiers). The reading copies render only this metadata; the attachment
bytes themselves are stored in the package (see below).

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

## Preservation package

Each capture of changed ticket content produces one package, stored once under its own key
prefix. The format identifier is `chronikwerk-package/1`. The earlier PDF plus JSON sidecar pair
is no longer produced; see [07 - Storage](07-storage.md) for what happens to existing pairs.

### Revision identity

- **Revision ID:** a UUIDv7, unique without coordination, also for two captures on the same day.
- **Revision number:** a per-ticket counter starting at 1, shown as `r0001`. People quote the
  number; software uses the ID. The catalog enforces one row per ticket and number.
- **Key prefix:** `<record_class>/<archive_user>/<archive_path...>/Ticket-<number>/r<NNNN>_<revision_id>/`.
  The record class matches `[a-z0-9-]{1,64}`. Archive user and path segments follow the
  [path policy](04-path-policy.md).

A capture whose source digest and policy ID match an existing complete revision of the ticket
resolves to that revision and creates no new one. The source digest covers the ticket and article
content fields, the SHA-256 of every original email and attachment, and the record class. It
excludes `updated_at`, tags and statistics, so the tag changes Chronikwerk makes do not create new
revisions. For the same reason it also leaves out internal notes created by Chronikwerk's own API
account (`GET /api/v1/users/me`), such as the success note: they are still archived with the next
revision, but they never cause one. Use an API account that only Chronikwerk uses. The policy ID
covers the record-class rule, the package format and `pdf.reading_copies`.

### Layout

```text
manifest.json
manifest.json.p7s                 seal over manifest.json (CMS, DER)
manifest.json.p7s.tst             RFC 3161 token over the seal bytes (DER)
capture.json                      capture description
reading-copy.pdf                  PDF/A-2u, tagged; PAdES signed when signing is enabled
reading-copy-ua.pdf               PDF/UA-1
source/ticket.json                Zammad ticket response, verbatim
source/articles.json              Zammad article response, verbatim
source/articles/<article_id>.eml  original message of each email article
attachments/<article_id>/<attachment_id>/<filename>
```

Attachment filenames are sanitized for the path; the original name stays in `source/articles.json`.
`pdf.reading_copies` selects which reading copies are rendered. Two further locations are reserved
by the format but are not produced yet: `events/` (mail evidence) and `evidence/` (validation
material and renewal timestamps).

### `manifest.json`

`manifest.json` lists every file except itself, the seal, the token and anything under
`evidence/`. Keys appear in this order, with two-space indentation, UTF-8 and a trailing newline.
Hashes are always taken over the stored bytes, never over a re-serialization.

```json
{
  "format": "chronikwerk-package/1",
  "revision_id": "0192f3a4-7c1e-7b52-9d0e-3f6a1c2b4d5e",
  "revision_number": 2,
  "previous_revision_id": "0192a7c0-0000-7000-8000-000000000000",
  "ticket": {"id": 42, "number": "31042"},
  "captured_at": "2026-10-14T10:02:03Z",
  "record_class": "pilot-test",
  "policy_id": "sha256:...",
  "source_digest": "sha256:...",
  "coverage": {
    "articles": {"total": 7, "included": 7},
    "attachments": {"total": 3, "included": 3},
    "original_emails": {"expected": 5, "included": 5, "unavailable": []},
    "complete": true
  },
  "files": [
    {"path": "source/articles/101.eml", "role": "original_email", "size": 48213,
     "sha256": "<64 hex digits>", "media_type": "message/rfc822", "derived_from": []}
  ],
  "producer": {"name": "chronikwerk", "version": "<package version>"}
}
```

The example is abbreviated: `files` lists every packaged file, sorted by path, and
`previous_revision_id` is `null` for the first revision. A single file is shown here only to
illustrate one entry.

File roles: `reading_copy_archival`, `reading_copy_accessible`, `source_ticket`,
`source_articles`, `original_email`, `attachment`, `capture_description` and `mail_evidence`
(reserved; not produced yet). The reading copies list
`derived_from: ["source/ticket.json", "source/articles.json"]`.

Coverage states what the capture holds compared with what the ticket held. An email article whose
original message Zammad does not provide is listed under `original_emails.unavailable`; Chronikwerk
never synthesizes a message, and an unavailable original does not make the capture incomplete.

### `capture.json`

`capture.json` describes how the capture happened. It records the revision ID, the producer, the
source system and its origin, the request (ID, source, delivery ID, receive time, attempt number),
collection start and end, the number of collection attempts, the inventories taken before and after
collection, the record class with the rule applied and the computed `retain_until`, whether the
store enforces retention, coverage details, the limits in force, and per article the mail
authentication results the receiving mail server reported (`Authentication-Results`,
`Received-SPF` and the `d=` domains of `DKIM-Signature` headers). Chronikwerk copies these values;
it does not validate DKIM or SPF itself.

### Catalog

The catalog is an index of complete revisions in the journal database: revision ID and number,
ticket, record class, archive user and path, key prefix, capture time, source digest, policy ID, the
SHA-256 of `manifest.json`, `retain_until`, whether retention is enforced, and the last
verification result. It can be rebuilt from the manifests in the store; the store is the record.
