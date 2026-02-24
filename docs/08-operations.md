# 08 - Operations

This runbook is for deployment, monitoring, troubleshooting, and recovery.

## 1. Endpoint Semantics

- `POST /ingest`
  - returns `202` with `{"status":"accepted","ticket_id":...}` on accepted payload
  - processing happens asynchronously in background
- `POST /ingest/batch`
  - returns `202` with `{"status":"accepted","count":...}`
  - schedules one background task per payload
- `POST /retry/{ticket_id}`
  - returns `202` with `{"status":"accepted","ticket_id":...}`
  - schedules one explicit retry job without delivery-ID dedupe
- `GET /jobs/{ticket_id}`
  - returns process-local status: `ticket_id`, `in_flight`, `shutting_down`
- `GET /healthz`
  - basic liveness/status payload (optionally omit version/service via `HEALTHZ_OMIT_VERSION`)
- `GET /metrics`
  - available only when `observability.metrics_enabled=true`; optional Bearer auth via `METRICS_BEARER_TOKEN`

Common ingest error responses:
- `403` invalid/missing HMAC signature when signed mode is active
- `503` no webhook auth configured and unsigned mode disabled
- `400` missing delivery header when `require_delivery_id=true`
- `413` request exceeds configured body size
- `422` invalid body (e.g. missing or invalid ticket id)
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

### Background processing (202)

After `POST /ingest` returns `202`, work is run asynchronously in process. This is **best-effort**: there is no guaranteed retry (e.g. no durable queue). If the process restarts or exits before the job finishes, that work is lost; the ticket will not be updated and no PDF is written. Operators can re-trigger by saving the ticket or reapplying the macro so a new webhook is sent. A durable queue for accepted payloads is a possible future improvement.

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

### Workflow and idempotency limitations (Bugs #32–#37)

Operators should be aware of the following; some are documented only, others are inherent to the current design:

- **In-flight lock is process-local:** Per-ticket concurrency is in-memory. Multiple processes or replicas can process the same ticket concurrently; use a single instance or accept possible tag races when scaling out.
- **should_process:** The gate skips when the “done” tag (`pdf:signed`) is present. Tickets in `pdf:processing` or `pdf:error` can be considered eligible depending on `require_tag` and tag state; a second worker may start if in-flight state is not shared.
- **TOCTOU on tag updates:** Two workers can both pass `should_process`; the slower one may then call `apply_processing`, removing `pdf:signed` and setting `pdf:processing`, undoing the first worker’s completion. Conditional or atomic tag updates are not used; accept or avoid concurrent workers per ticket.
- **Error path orphans:** If `apply_error` fails after the trigger was removed, the ticket can end with no state tags and be skipped when `require_tag=true`. Recovery: re-add trigger and remove stale `pdf:processing` if present, then re-trigger.
- **Delivery ID claim order:** The delivery ID is claimed before `should_process` is evaluated. If the run exits early (e.g. no trigger tag), that delivery ID is still “seen” for the TTL, so a later replay with the same ID is skipped until TTL expires.
- **Claim before success:** The delivery ID is marked seen when the job starts, not after successful completion. A failure after claim but before `apply_done` prevents the same delivery ID from retrying until TTL expires; use a new webhook (new delivery ID) or wait for TTL to retry.

## 5. Reprocessing Workflow

Use this procedure after failed runs:

1. Read latest ticket error note and identify classification (Transient/Permanent).
2. Fix root cause (storage permissions, credentials, network, signing, TSA, etc.).
3. Normalize tags:
  - remove stale `pdf:processing` if present
  - ensure trigger tag is present
  - remove `pdf:signed` only if you intentionally want a new archive output
4. Trigger a fresh ticket update/macro to emit a new webhook, or call `POST /retry/{ticket_id}`.
5. Confirm final state (`pdf:signed` or new `pdf:error` with updated note).

## 6. Troubleshooting Matrix

### `403 forbidden` on `/ingest`

Check:
- identical HMAC secret on Zammad and service
- header name `X-Hub-Signature`
- format `sha1=<hex>` or `sha256=<hex>`
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
- ticket has more articles than `pdf.max_articles` and `pdf.article_limit_mode` is `fail` (default).

Fix:
- increase `PDF_MAX_ARTICLES` or set to `0` for unlimited; or
- set `PDF_ARTICLE_LIMIT_MODE=cap_and_continue` to truncate and archive with a warning; or
- use `minimal` template.

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

## 9. Scripts

| Script | Purpose |
|--------|--------|
| `scripts/ci/smoke-test.sh` | Optional CI smoke test (requires env and optional services). |
| `scripts/dev/run-local.sh` | Run the service locally (e.g. with env loaded). |
| `scripts/dev/gen-dev-certs.sh` | Generate development certificates. |
| `scripts/ops/verify-pdf.sh` | Verify PDF signatures (wrapper: uses `pyhanko`/`pyhanko-cli` if available, else `scripts/ops/verify-pdf.py`). |
| `scripts/ops/verify-pdf.py` | Python fallback for PDF verification when pyHanko CLI is not installed. |
| `scripts/ops/mount-cifs.sh` | Mount CIFS/SMB share (operations helper). |

For local development, to remove untracked and ignored files (e.g. `.mypy_cache`, `build/`): `git clean -fdx` (use with care).

## 10. Residual risks and release checklist

External risks operators should be aware of:

- **CIFS/network storage:** Durability and consistency depend on the share and network; consider fsync and atomic write settings.
- **`/metrics` access:** When enabled, protect with `METRICS_BEARER_TOKEN` or network policy; otherwise metrics may be exposed.
- **TSA certificate trust:** RFC3161 timestamp validation depends on TSA and CA trust configuration.

Before deployment, run through [Release and deployment checklist](release-checklist.md) for safety checks.
