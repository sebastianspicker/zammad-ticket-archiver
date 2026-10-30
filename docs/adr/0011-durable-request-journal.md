# ADR 0011: Durable request journal

## Status

Accepted (2026-10-30); implementation in progress. Supersedes the process-local admission and
delivery-dedupe parts of [ADR 0009](0009-composition-owned-runtime-state.md).

## Context

`POST /ingest` answers `202` after process-local admission. A crash or restart loses every admitted
and running job, delivery-ID dedupe and per-ticket locks reset, and operators must find and retry
the affected tickets by hand. The HfMT design ("Zammad Ticket Archiving at HfMT Köln", section VI)
requires that pending requests survive a restart and that a repeated notification is harmless
afterwards.

## Decision

- Chronikwerk records every request in a transactional SQLite journal (`journal.path`, WAL,
  `synchronous=FULL`) on local disk before answering `202`. The journal is never placed on the
  archive share.
- A request moves through `waiting`, `collecting`, `verifying`, `complete` or `failed`. A request
  for a ticket that already has an active request is stored as `coalesced`; a duplicate
  `X-Zammad-Delivery` ID adds no row. A partial unique index allows one active request per ticket.
- Transient failures return to `waiting` with exponential back-off (`capture.retry.*`); permanent
  failures and exhausted retries end in `failed`.
- On startup, `collecting` requests return to `waiting`, and `verifying` requests resume at their
  recorded commit step (ADR 0013).
- The admin application's job history reads the journal instead of process memory.
- The same database holds the archive catalog (ADR 0012); each concern owns its tables.

## Consequences

- `202` now means "durably recorded", not "archived"; completion is still visible only through
  Zammad tags, the catalog and the package itself.
- Chronikwerk stays a single process. SQLite is not shared between instances; horizontal scaling
  remains out of scope.
- The journal file needs backup like any other state file, although the archive store remains
  the authoritative record and the catalog can be rebuilt from it.
