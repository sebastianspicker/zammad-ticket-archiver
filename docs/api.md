# API reference

This document describes the HTTP contract exposed by Chronikwerk.

## `POST /ingest`

Accepts one Zammad webhook payload and schedules background processing.

Query parameters:

- `dry_run` (bool, default `false`): validate the payload and return `202`
  without scheduling work.

Headers:

- `Content-Type: application/json`
- `X-Hub-Signature`: required HMAC signature, either Zammad's native
  `sha1=<hex>` or `sha256=<hex>`. Supported startup configuration requires a
  webhook HMAC secret.
- `X-Zammad-Delivery`: required only when
  `hardening.webhook.require_delivery_id=true`.
- `X-Request-Id`: optional request correlation ID.

Body:

- JSON object containing either `ticket.id` or `ticket_id`.

Success (`202`):

```json
{"status":"accepted","ticket_id":123}
```

Dry run success:

```json
{"status":"dry_run_accepted","ticket_id":123}
```

Common errors:

| Status | Detail/code | Meaning |
| --- | --- | --- |
| `400` | `missing_delivery_id` | Strict delivery-ID mode is enabled and the header is missing. |
| `403` | `forbidden` | HMAC validation failed. |
| `413` | `request_too_large` | Request body exceeds the configured limit. |
| `422` | validation error | Payload does not contain a positive ticket ID. |
| `429` | `rate_limited` | Rate limit exceeded. |
| `503` | `job_capacity_exhausted` | Admission capacity is full, or the app was built without a scheduler (read-only). Sent with `Retry-After: 1`. |
| `503` | `shutting_down` | The process is draining for shutdown and accepts no new work. |
| `503` | `webhook_auth_not_configured` | Defensive fail-closed response if an app is constructed without validated webhook authentication settings. Normal startup rejects this configuration. |

## `POST /ingest/batch`

Accepts a JSON array of webhook payloads and schedules one job per item.

Limits:

- Maximum batch size: `100`; larger batches return `422 batch_too_large`.
- An empty array returns `422 batch_empty`.
- Admission is all-or-nothing: if capacity cannot hold every item, nothing is scheduled.
- Each item must contain either `ticket.id` or `ticket_id`.

Headers and error behavior match `POST /ingest`. When a batch-level
`X-Zammad-Delivery` header is present, per-item delivery IDs are derived as
`<delivery-id>:<index>`.

Success (`202`):

```json
{"status":"accepted","count":2}
```

Dry run success:

```json
{"status":"dry_run_accepted","count":2}
```

## `POST /retry/{ticket_id}`

Accepts and schedules one reprocessing attempt for a ticket. The forced attempt bypasses
the normal completed-tag skip, but `202` still reports admission rather than completion.

Headers:

- `Authorization: Bearer <RETRY_BEARER_TOKEN>`

Success (`202`):

```json
{"status":"accepted","ticket_id":123}
```

Common errors:

- `401`: missing or invalid bearer token.
- `503`: retry token is not configured.
- `503 job_capacity_exhausted`: admission capacity is full or the process is shutting down.

## `GET /jobs/history`

Returns process-local processing history when enabled. The route is
disabled by default and requires `Authorization: Bearer <OBSERVABILITY__HISTORY_BEARER_TOKEN>`.

Query parameters:

- `limit` (default `100`, max enforced by runtime history store)
- `ticket_id` (optional integer filter)

Example response:

```json
{
  "entries": [
    {
      "id": "1",
      "status": "processed",
      "ticket_id": 123,
      "classification": null,
      "message": "",
      "delivery_id": "delivery-1",
      "request_id": "req-1",
      "created_at": 1710000000.0
    }
  ]
}
```

## `GET /healthz`

Returns shallow service health by default.

Query parameters:

- `deep` (bool, default `false`): include a storage writability check.

Deep checks are single-flight. If one storage probe is already running, another
deep request returns `503 deep_health_check_busy` with `Retry-After: 1`; shallow
health requests remain available.

Example:

```json
{
  "status": "ok",
  "service": "chronikwerk",
  "version": "0.3.0a1",
  "time": "2026-02-07T12:00:00+00:00"
}
```

When `OBSERVABILITY__HEALTHZ_OMIT_VERSION=true`, the response omits `service`
and `version`.

## `GET /metrics`

Mounted only when `observability.metrics_enabled=true`; configuration also
requires a non-blank bearer token.

Authentication:

- `OBSERVABILITY__METRICS_BEARER_TOKEN` is required when metrics are enabled;
  requests must include `Authorization: Bearer <token>`.

Response format: Prometheus text exposition.

## Administration application and API

All `/admin` routes are absent (`404`) unless `admin.enabled=true`. Admin responses use
`Cache-Control: no-store`, a strict same-origin CSP, frame denial, `nosniff`, and
`Referrer-Policy: no-referrer`. Login exchanges the external access token for an
`HttpOnly`, `SameSite=Strict`, secure-by-default process-local session cookie. Every
authenticated state-changing request requires per-session CSRF proof: JSON API calls use
`X-CSRF-Token`, while HTML forms submit the same token in a hidden `csrf_token` field.
Creating a session or submitting the login form is the authentication boundary and does
not yet have a session CSRF token.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/admin/api/v1/session` | Create a process-local admin session; returns `204`. |
| `DELETE` | `/admin/api/v1/session` | CSRF-protected logout. |
| `GET` | `/admin/api/v1/status` | Runtime, admission, volatile-history, and revision state. |
| `POST` | `/admin/api/v1/status/storage-check` | User-initiated deep storage check. |
| `GET` | `/admin/api/v1/jobs` | Cursor-paginated process-local history (`limit<=100`). |
| `POST` | `/admin/api/v1/jobs/{ticket_id}/retry` | Acknowledged retry request; returns `202`. |
| `GET` | `/admin/api/v1/config` | Allowlisted values, ownership, secret-presence booleans, revisions. |
| `POST` | `/admin/api/v1/config/validate` | Validate a flat field overlay and return a normalized diff. |
| `PUT` | `/admin/api/v1/config/staged` | Stage with `If-Match`; restart remains external. |
| `GET` | `/admin/api/v1/config/revisions` | Bounded non-secret revision metadata. |
| `POST` | `/admin/api/v1/config/revisions/{revision}/restore` | Stage a previous overlay as a new revision. |

Admin API errors use stable `code`, localized `message`, and `request_id` fields. Machine
status values such as `accepted`, `processed`, and `failed` are never localized.

## Webhook HMAC

Supported signature header:

- `X-Hub-Signature`

Supported algorithms:

- `sha1=<hex>`: HMAC-SHA1, the form Zammad sends natively. Compatibility mode
  only.
- `sha256=<hex>`: HMAC-SHA256. Accepted in both modes.

When `hardening.webhook.require_delivery_id=false`, the signature is computed
over the raw request body bytes for compatibility. In strict delivery-ID mode,
the normalized `X-Zammad-Delivery` value is authenticated as well. The signed
bytes are, in order:

```text
b"zammad-webhook-v1\\0"
+ uint64_be(len(delivery_id.strip().encode("utf-8")))
+ delivery_id.strip().encode("utf-8")
+ raw_request_body
```

Changing the delivery ID therefore invalidates a strict-mode signature. Enable
strict mode only after the webhook sender uses this canonical form. Zammad
does not produce it, so strict mode needs a signing proxy. Strict mode accepts
`sha256=` only. See
[ADR 0010](adr/0010-zammad-native-webhook-signature.md).

Compatibility-mode example:

Run this only against an isolated test service configured with the same
`webhook_secret` value.

```bash
webhook_secret='local-example-webhook-secret-at-least-32-characters'
hex_signature="$(
  WEBHOOK_SECRET="$webhook_secret" python -c \
    'import hashlib,hmac,os,sys; print(hmac.new(os.environ["WEBHOOK_SECRET"].encode(), sys.stdin.buffer.read(), hashlib.sha256).hexdigest())' \
    < examples/webhook-payload.sample.json
)"
curl -i \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature: sha256=$hex_signature" \
  -H "X-Zammad-Delivery: delivery-001" \
  --data-binary @examples/webhook-payload.sample.json \
  http://127.0.0.1:8080/ingest
```

Supported deployments always configure `ZAMMAD__WEBHOOK_HMAC_SECRET`; there is no
unsigned runtime mode. Tests that construct an intentionally invalid app may exercise
the defensive `503 webhook_auth_not_configured` response.
