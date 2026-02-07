# 08 - Operations

This runbook is for deployment, monitoring, troubleshooting, and recovery.

## 1. Endpoint Semantics

- `POST /ingest`
  - returns `202` on accepted payload
  - processing happens asynchronously in background
- `GET /healthz`
  - basic liveness/status payload
- `GET /metrics`
  - available only when `observability.metrics_enabled=true`

Common ingest error responses:
- `403` invalid/missing HMAC signature when signed mode is active
- `503` no webhook auth configured and unsigned mode disabled
- `400` missing delivery header when `require_delivery_id=true`
- `413` request exceeds configured body size
- `429` request rate-limited

## 2. Start/Stop

### Docker Compose

```bash
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env up -d --build
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env ps
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env logs -f
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env down
```

### Optional systemd wrapper

```bash
sudo systemctl status zammad-archiver.service
sudo journalctl -u zammad-archiver.service -f
```

## 3. Observability Sources

Primary signals:
- structured service logs (`request_id`, `ticket_id`, optional `delivery_id`)
- ticket internal notes (`PDF archived...` / `PDF archiver error...`)
- ticket tags (`pdf:sign`, `pdf:processing`, `pdf:signed`, `pdf:error`)
- metrics (`processed_total`, `failed_total`, timing histograms)

## 4. Processing and Idempotency Behavior

### Tag transitions

- processing start:
  - remove `pdf:signed`, `pdf:error`, trigger tag
  - add `pdf:processing`
- success:
  - remove `pdf:processing`, `pdf:error`, trigger tag
  - add `pdf:signed`
- failure:
  - remove `pdf:processing`, `pdf:signed`
  - add `pdf:error`
  - transient keeps/re-adds trigger
  - permanent removes trigger

### Delivery ID dedupe

- dedupe key: `X-Zammad-Delivery`
- repeated delivery IDs are skipped for `workflow.delivery_id_ttl_seconds`
- dedupe store is in-memory only
- restart clears dedupe history

## 5. Reprocessing Workflow

Use this procedure after failed runs:

1. Read latest ticket error note and identify classification (Transient/Permanent).
2. Fix root cause (storage permissions, credentials, network, signing, TSA, etc.).
3. Normalize tags:
  - remove stale `pdf:processing` if present
  - ensure trigger tag is present
  - remove `pdf:signed` only if you intentionally want a new archive output
4. Trigger a fresh ticket update/macro to emit a new webhook.
5. Confirm final state (`pdf:signed` or new `pdf:error` with updated note).

## 6. Troubleshooting Matrix

### `403 forbidden` on `/ingest`

Check:
- identical HMAC secret on Zammad and service
- header name `X-Hub-Signature`
- format `sha1=<hex>`
- request body not transformed by proxy

### `503 webhook_auth_not_configured`

Cause:
- no webhook secret configured
- `hardening.webhook.allow_unsigned=false`

Fix:
- set `WEBHOOK_HMAC_SECRET`, or
- test-only fallback `HARDENING_WEBHOOK_ALLOW_UNSIGNED=true`

### `400 missing_delivery_id`

Cause:
- `hardening.webhook.require_delivery_id=true`
- missing `X-Zammad-Delivery`

Fix:
- ensure header is sent, or disable strict requirement

### Ticket in `pdf:error` with storage messages

Check:
- `STORAGE_ROOT` path and mount
- UID/GID mapping
- share ACLs and write permissions
- free space/quota

### Ticket in `pdf:error` with signing messages

Check:
- `SIGNING_ENABLED=true`
- PFX present at `SIGNING_PFX_PATH`
- correct PFX password
- certificate validity dates

### Ticket in `pdf:error` with TSA messages

Check:
- `TSA_URL`
- `TSA_CA_BUNDLE_PATH` (private CA)
- `TSA_USER` and `TSA_PASS` when auth required
- outbound connectivity and TLS trust

### Rendering article limit exceeded

Cause:
- ticket has more articles than `pdf.max_articles`

Fix:
- increase `PDF_MAX_ARTICLES`
- set `PDF_MAX_ARTICLES=0` to disable
- optionally use `minimal` template

## 7. Signature Verification Procedure

```bash
scripts/ops/verify-pdf.sh /path/to/file.pdf
```

Optional detailed output:

```bash
VERIFY_PDF_SHOW_DETAILS=1 scripts/ops/verify-pdf.sh /path/to/file.pdf
```

Optional trust inputs:

```bash
VERIFY_PDF_TRUST="/path/root.pem:/path/intermediate.pem" scripts/ops/verify-pdf.sh /path/to/file.pdf
VERIFY_PDF_OTHER_CERTS="/path/extra.pem" scripts/ops/verify-pdf.sh /path/to/file.pdf
```

## 8. On-Call Fast Triage

1. Was webhook accepted (`202`) by `/ingest`?
2. What is current ticket tag state?
3. What does latest internal note report?
4. Is destination path expected and writable?
5. Could run be skipped by delivery-ID dedupe?
