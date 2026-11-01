# FAQ

## Does `202 Accepted` mean the PDF was archived?

No. `202` only means the request was accepted and background processing was
scheduled. Confirm completion with:

- ticket tags (`pdf:signed` or `pdf:error`); `pdf:signed` is the retained workflow
  success tag and does not by itself prove that optional PAdES signing ran
- latest internal ticket note
- service logs using `ticket_id`, `delivery_id`, or `request_id`
- archive PDF and sidecar presence on disk

## Why do I get `403 forbidden` on `/ingest`?

HMAC validation failed.

Check:

- `ZAMMAD__WEBHOOK_HMAC_SECRET` matches Zammad
- the request uses `X-Hub-Signature`
- the proxy does not modify the body after Zammad signs it

## Why do I get `503 webhook_auth_not_configured`?

The app was constructed without validated webhook authentication settings. Normal
service startup rejects a missing secret; set `ZAMMAD__WEBHOOK_HMAC_SECRET` to a random,
non-placeholder value containing at least 32 characters.

## Why do I get `400 missing_delivery_id`?

`hardening.webhook.require_delivery_id=true` is enabled and the request did not
include `X-Zammad-Delivery`.

## Why is a ticket stuck with `pdf:processing`?

The process may have stopped during background work, or a worker failed before
final tag cleanup.

Do this:

1. Check logs. If journal history is enabled, query `/jobs/history` using
   `OBSERVABILITY__HISTORY_BEARER_TOKEN`.
2. Remove stale `pdf:processing` if the job is no longer running.
3. Fix the underlying failure.
4. Trigger a fresh webhook or, when `RETRY_BEARER_TOKEN` is configured, use
   authenticated `POST /retry/{ticket_id}`.

## Why did the ticket get `pdf:error`?

The internal note should include a scrubbed error message and classification.
Common causes:

- storage root missing or not writable
- path policy rejected the archive path
- Zammad API token lacks permissions
- PDF rendering limit exceeded
- signing PFX path/password invalid
- TSA endpoint unreachable or rejected the timestamp request

## Why does storage fail on CIFS/SMB?

Check:

- the mount is active and read-write
- the container UID/GID can create directories and files
- free space and quota
- path prefix and segment policy
- `GET /healthz?deep=true`

## Can I run multiple service replicas?

No. The supported alpha deployment is one process per durable journal. The
journal retains admission, delivery IDs and history across restarts, but this
is not a multi-replica coordination contract. Do not share one journal between
service instances; see the operations guide.

## Where should secrets live?

Use environment variables, deployment secret stores, or files outside the
repository. Do not commit `.env`, PFX files, private keys, API tokens, or
archive output.
