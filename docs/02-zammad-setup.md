# Zammad setup

This guide covers the Zammad-side setup for sending archive requests to Chronikwerk.

## Prerequisites

- Zammad admin access.
- Reachable Chronikwerk endpoint, for example
  `https://archiver.example.com/ingest`.
- `ZAMMAD__WEBHOOK_HMAC_SECRET` configured in Chronikwerk.

## Custom fields

Create ticket fields matching the configured Chronikwerk field names. Zammad returns each one
as a top-level ticket attribute under its technical name; there is no separate custom-field
object.

| Technical name | Required | Notes |
| --- | --- | --- |
| `archive_path` | yes | Target path under `storage.root`; prefer controlled values. |
| `archive_user_mode` | no | Defaults to `owner` (the ticket owner's login; unassigned tickets fail); may be `owner`, `current_agent` (the agent who triggered the webhook), or `fixed`. |
| `archive_user` | conditional | Required only for `archive_user_mode=fixed`. |
| `archive_request` | optional | Useful for Zammad-side workflow conditions. |

Admin path: `Admin -> System -> Objects -> Ticket`.

Keep `archive_path`, `archive_user_mode` and `archive_user` agent-only: in each field's screen
settings, do not show or allow editing for customers. Whoever can edit these fields chooses the
archive directory. Paths stay confined under `storage.root`, but a customer could otherwise
file a ticket into another user's directory.

## Core workflow rules

Recommended validation:

1. When `archive_request=true`, require `archive_path`. Set
   `archive_user_mode` only when the default `owner` behavior is not sufficient.
2. When `archive_user_mode=fixed`, require `archive_user`.

Admin path: `Admin -> Core Workflows`.

## Archive macro

Create a macro that:

- adds the trigger tag, default `pdf:sign`
- optionally sets `archive_request=true`

Admin path: `Admin -> Manage -> Macros`.

## Webhook

Create a Zammad webhook targeting:

```text
https://archiver.example.com/ingest
```

Configure the same HMAC secret as `ZAMMAD__WEBHOOK_HMAC_SECRET`. Zammad signs each delivery
with `X-Hub-Signature: sha1=<hex>`, which the default compatibility mode accepts. Leave
`HARDENING__WEBHOOK__REQUIRE_DELIVERY_ID` unset: strict mode needs a signing proxy.

## Trigger

Create a trigger that sends the webhook when the archive macro updates a
ticket. Zammad's default webhook payload (top-level `ticket` and `article`) is sufficient; a
custom payload must contain `ticket.id` or `ticket_id`.

## Smoke test

1. Fill `archive_path`; optionally choose a non-default `archive_user_mode`.
2. Apply the archive macro.
3. Confirm Zammad receives HTTP `202`.
4. Confirm temporary `pdf:processing`.
5. Confirm final `pdf:signed` or `pdf:error`.
6. Confirm the internal Chronikwerk note.

`pdf:signed` is the retained workflow success tag and does not prove that optional PAdES
signing ran. Verify signatures from the PDF and audit sidecar.

## Common issues

### `403 forbidden`

HMAC secret mismatch, wrong signature header, or body transformation by a proxy.

### `400 missing_delivery_id`

The service requires `X-Zammad-Delivery`, but Zammad did not send it.

### Trigger does not fire

Check macro tag changes, trigger conditions, and workflow validation rules.

### Ticket ends in `pdf:error`

Zammad setup may be correct; inspect storage, signing, TSA, and service logs.
