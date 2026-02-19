# FAQ

## Why did `/ingest` return `202` but no PDF exists yet?

`202` means the request was accepted, not that processing completed. Processing after `202` is best-effort: there is no guaranteed retry, and work can be lost on process restart (no durable queue). If the service restarted or the job never ran, re-trigger by saving the ticket or reapplying the macro.

Check:
- ticket tags (`pdf:processing`, `pdf:signed`, `pdf:error`)
- latest internal ticket note
- service logs by `ticket_id` or `X-Request-Id`

## Why do I get `403 forbidden` on `/ingest`?

HMAC validation failed while signed mode is active.

Check:
- `WEBHOOK_HMAC_SECRET`
- header `X-Hub-Signature`
- format `sha1=<hex>` or `sha256=<hex>`
- request body not modified by proxies

See [`api.md`](api.md).

## Why do I get `503 webhook_auth_not_configured`?

No webhook secret is configured and unsigned mode is disabled.

Fix:
- set `WEBHOOK_HMAC_SECRET`, or
- for internal test only: `HARDENING_WEBHOOK_ALLOW_UNSIGNED=true`

## Why do I get `400 missing_delivery_id`?

`hardening.webhook.require_delivery_id=true` is enabled but `X-Zammad-Delivery` was not sent.

## Why was the ticket marked `pdf:error` with Permanent classification?

Common causes:
- missing/invalid ticket fields (`archive_path`, `archive_user_mode`, `archive_user` for `fixed` mode)
- storage path policy violations
- signing configuration/material errors

Permanent failures remove the trigger tag. Re-add it after fixing root cause.

## Why is a ticket stuck with `pdf:processing`?

Usually process interruption during job execution.

Recovery:
1. remove `pdf:processing`
2. ensure trigger tag is present
3. trigger a new ticket update/macro

## Why are duplicate deliveries skipped?

Delivery dedupe is active for `workflow.delivery_id_ttl_seconds`.

To process again:
- trigger a new Zammad event (new delivery ID), or
- reduce TTL

## Why does storage write fail with permissions errors?

Check:
- correct `STORAGE_ROOT`
- mount read/write mode
- UID/GID mapping for runtime user (`10001`)
- share ACLs and free space/quota

See [`07-storage.md`](07-storage.md).

## Why does signing fail?

Check:
- `SIGNING_ENABLED=true`
- PFX exists at `SIGNING_PFX_PATH`
- correct `SIGNING_PFX_PASSWORD`
- certificate validity period

See [`06-signing-and-timestamp.md`](06-signing-and-timestamp.md).

## Why does timestamping fail?

Check:
- `TSA_URL`
- `TSA_CA_BUNDLE_PATH` (if private CA)
- `TSA_USER` + `TSA_PASS` when auth is required
- outbound connectivity and TLS trust

## Why do large tickets fail to render?

`pdf.max_articles` (default `250`) may be exceeded.

Options:
- increase `PDF_MAX_ARTICLES`
- set `PDF_MAX_ARTICLES=0` (disable cap)
- switch to `PDF_TEMPLATE_VARIANT=minimal`
