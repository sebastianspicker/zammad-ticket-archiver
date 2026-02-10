# API

This document defines the HTTP contract for `zammad-pdf-archiver`.

## 1. Endpoints

### `POST /ingest`

Webhook ingestion endpoint.

#### Request headers

- `Content-Type: application/json` (recommended)
- `X-Request-Id: <id>` (optional)
- `X-Hub-Signature: sha1=<hex>` or `sha256=<hex>` (required when secret is configured)
- `X-Zammad-Delivery: <id>` (required only when `hardening.webhook.require_delivery_id=true`)

#### Request body

JSON object. Ticket ID is extracted from either:
- `ticket.id`
- `ticket_id`

If ticket ID is missing or invalid, the request is rejected with `422` (schema validation); valid payloads get `202` and background processing.

Example payload:
- [`../examples/webhook-payload.sample.json`](../examples/webhook-payload.sample.json)

#### Success response

- status: `202`
- body: `{"accepted": true, "ticket_id": 123}`
- header: `X-Request-Id` is always returned

#### Error responses

- `400` `{"detail":"missing_delivery_id"}`
- `403` `{"detail":"forbidden"}`
- `422` invalid body (e.g. missing or invalid ticket id)
- `413` `{"detail":"request_too_large"}`
- `429` `{"detail":"rate_limited"}`
- `503` `{"detail":"webhook_auth_not_configured"}`

### `GET /healthz`

Always available.

Example response:

```json
{
  "status": "ok",
  "service": "zammad-pdf-archiver",
  "version": "0.1.0",
  "time": "2026-02-07T12:00:00+00:00"
}
```

Notes:
- `version` comes from installed package metadata; fallback may be `0.0.0` in some non-packaged contexts.
- When `HEALTHZ_OMIT_VERSION=true`, the response contains only `status` and `time` (no `service` or `version`).

### `GET /metrics`

Only mounted when `observability.metrics_enabled=true`. When `METRICS_BEARER_TOKEN` is set, requests must include `Authorization: Bearer <token>`; otherwise `401` is returned.

Response format:
- Prometheus text exposition (`text/plain`)

## 2. Webhook Security Contract

### HMAC verification

When a secret is configured:
- header: `X-Hub-Signature`
- format: `sha1=<hex>` or `sha256=<hex>`
- algorithms: HMAC-SHA1 and HMAC-SHA256 (sender chooses; prefer SHA-256 for new setups)
- message: raw request body bytes

Secret sources:
- preferred: `zammad.webhook_hmac_secret` (`WEBHOOK_HMAC_SECRET`)
- legacy fallback: `server.webhook_shared_secret` (`WEBHOOK_SHARED_SECRET`)

### Unsigned mode

Default is fail-closed.

To allow unsigned requests (internal testing only):
- `hardening.webhook.allow_unsigned=true`

### Delivery ID requirement

Optional strict mode:
- set `hardening.webhook.require_delivery_id=true`
- then `X-Zammad-Delivery` is mandatory

## 3. Idempotency Contract

`X-Zammad-Delivery` is used for best-effort dedupe:
- duplicate delivery IDs are skipped for `workflow.delivery_id_ttl_seconds`
- dedupe state is in-memory only and not durable across restarts

## 4. Example Signed Request

SHA-1 (Zammad typically sends this):

```bash
sig="sha1=$(openssl dgst -sha1 -hmac "$WEBHOOK_HMAC_SECRET" -hex payload.json | awk '{print $2}')"
curl -i \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature: $sig" \
  -H "X-Zammad-Delivery: delivery-001" \
  --data-binary @payload.json \
  http://127.0.0.1:8080/ingest
```

SHA-256 is also accepted: use `sha256=<hex>` in the header and compute HMAC-SHA256 over the raw body.
