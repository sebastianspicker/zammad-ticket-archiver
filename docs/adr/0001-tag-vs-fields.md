# ADR 0001: Trigger via tag vs fields

## Status

Accepted (implemented)

## Context

The service must not archive every ticket automatically. Operators/agents should be able to explicitly
request archiving from within Zammad, and the service must behave deterministically when it receives multiple
webhook deliveries for the same ticket.

Two common approaches:
- trigger via tag (e.g. `pdf:sign`)
- trigger via dedicated fields (e.g. `archive_enabled=true`)

## Decision

Use the tag **`pdf:sign`** as the primary trigger for the archiver service.

Use ticket fields as parameters (not as the primary trigger):
- `archive_path` – determines the target directory structure
- `archive_user_mode` – determines the `<archive_user>` directory component
- (optional) `archive_user` – only required when `archive_user_mode=fixed`

For additional gating/UX, Zammad should use a boolean field like `archive_request` in its Trigger conditions,
but the service itself only requires the tag and the parameter fields.

## Consequences

- Agent UX: archiving is a deliberate action (macro sets tag + required fields).
- Deterministic states are visible via tags:
  - `pdf:sign` (requested)
  - `pdf:processing` (in progress)
  - `pdf:signed` (success)
  - `pdf:error` (failure)
- Idempotency:
  - the service skips tickets that already have `pdf:signed`
  - best-effort webhook delivery dedupe uses `X-Zammad-Delivery` with TTL (in-memory)
- Reprocessing:
  - transient failures keep/re-add `pdf:sign`
  - permanent failures remove `pdf:sign` to prevent accidental infinite loops

See also:
- [`../01-architecture.md`](../01-architecture.md) (sequence + state diagrams)
- [`../08-operations.md`](../08-operations.md) (reprocessing runbook)
