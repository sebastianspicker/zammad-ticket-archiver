# Promise Matrix (README + docs 00/01/02/04/05/06/07/08/09)

Legend: `Implemented` / `Partially Implemented` / `Missing` / `Unverified`

Last updated: 2026-02-07

## Ingress and workflow

- [x] `POST /ingest` accepts webhook events and returns `202 Accepted` while processing in background.  
  Source: `README.md`, `docs/00-overview.md`, `docs/01-architecture.md`, `docs/08-operations.md`  
  Code: `src/zammad_pdf_archiver/app/routes/ingest.py`, `src/zammad_pdf_archiver/app/server.py`  
  Status: `Implemented`

- [x] Request ID is created/preserved via `X-Request-Id` and propagated into processing + notes.  
  Source: `docs/08-operations.md`  
  Code: `src/zammad_pdf_archiver/app/middleware/request_id.py`, `src/zammad_pdf_archiver/app/routes/ingest.py`, `src/zammad_pdf_archiver/app/jobs/process_ticket.py`  
  Status: `Implemented`

- [x] Delivery ID (`X-Zammad-Delivery`) is passed into processing and used for best-effort replay dedupe with TTL.  
  Source: `docs/00-overview.md`, `docs/02-zammad-setup.md`, `docs/08-operations.md`, `docs/09-security.md`  
  Code: `src/zammad_pdf_archiver/app/routes/ingest.py`, `src/zammad_pdf_archiver/app/jobs/process_ticket.py`, `src/zammad_pdf_archiver/domain/idempotency.py`  
  Status: `Implemented`

- [x] Processing gate respects trigger/done tags: process only when allowed, skip when already `pdf:signed`.  
  Source: `README.md`, `docs/01-architecture.md`, `docs/08-operations.md`  
  Code: `src/zammad_pdf_archiver/domain/state_machine.py`, `src/zammad_pdf_archiver/app/jobs/process_ticket.py`  
  Status: `Implemented`

- [x] Deterministic tag transitions and failure behavior:
  - running: add `pdf:processing`
  - success: add `pdf:signed`
  - failure: add `pdf:error`
  - transient keeps/re-adds trigger; permanent removes trigger.  
  Source: `README.md`, `docs/01-architecture.md`, `docs/08-operations.md`  
  Code: `src/zammad_pdf_archiver/domain/state_machine.py`, `src/zammad_pdf_archiver/app/jobs/retry_policy.py`, `src/zammad_pdf_archiver/app/jobs/process_ticket.py`  
  Status: `Implemented`

- [x] Success and error internal notes include required operational fields (path/hash/IDs/time and classification/action).  
  Source: `README.md`, `docs/08-operations.md`  
  Code: `src/zammad_pdf_archiver/app/jobs/process_ticket.py`  
  Status: `Implemented`

## Security and hardening

- [x] HMAC verification for `/ingest` uses `X-Hub-Signature: sha1=<hex>` over raw body bytes; rejects invalid signatures.  
  Source: `docs/02-zammad-setup.md`, `docs/09-security.md`  
  Code: `src/zammad_pdf_archiver/app/middleware/hmac_verify.py`  
  Status: `Implemented`

- [x] Fail-closed webhook auth defaults: reject unsigned requests unless explicitly allowed.  
  Source: `docs/09-security.md`  
  Code: `src/zammad_pdf_archiver/app/middleware/hmac_verify.py`, `src/zammad_pdf_archiver/config/validate.py`  
  Status: `Implemented`

- [x] Optional requirement for `X-Zammad-Delivery` header at ingress.  
  Source: `docs/09-security.md`  
  Code: `src/zammad_pdf_archiver/app/middleware/hmac_verify.py`, `src/zammad_pdf_archiver/config/settings.py`, `src/zammad_pdf_archiver/config/validate.py`  
  Status: `Implemented`

- [x] Request size limit (`413`) and token-bucket rate limit (`429`) are enforced from config.  
  Source: `docs/09-security.md`  
  Code: `src/zammad_pdf_archiver/app/middleware/body_size_limit.py`, `src/zammad_pdf_archiver/app/middleware/rate_limit.py`, `src/zammad_pdf_archiver/config/settings.py`  
  Status: `Implemented`

- [x] Upstream transport hardening defaults:
  - reject insecure HTTP/TLS unless explicit opt-in
  - default `trust_env=false` for HTTP clients, overridable with `hardening.transport.trust_env=true`.  
  Source: `docs/09-security.md`  
  Code: `src/zammad_pdf_archiver/config/validate.py`, `src/zammad_pdf_archiver/adapters/zammad/client.py`, `src/zammad_pdf_archiver/adapters/signing/tsa_rfc3161.py`  
  Status: `Implemented`

## Snapshot and PDF rendering

- [x] Snapshot builder fetches ticket/tags/articles and emits strict snapshot model contract.  
  Source: `docs/00-overview.md`, `docs/01-architecture.md`, `docs/05-pdf-rendering.md`  
  Code: `src/zammad_pdf_archiver/adapters/snapshot/build_snapshot.py`, `src/zammad_pdf_archiver/domain/snapshot_models.py`  
  Status: `Implemented`

- [x] HTML sanitization for `article.body_html` is applied before templates render with `|safe`; text fallback exists.  
  Source: `docs/05-pdf-rendering.md`  
  Code: `src/zammad_pdf_archiver/domain/html_sanitize.py`, `src/zammad_pdf_archiver/adapters/snapshot/build_snapshot.py`  
  Status: `Implemented`

- [x] Jinja template rendering contract (`snapshot`, `ticket`, `articles`) with autoescape enabled.  
  Source: `docs/05-pdf-rendering.md`  
  Code: `src/zammad_pdf_archiver/adapters/pdf/template_engine.py`  
  Status: `Implemented`

- [x] HTML -> PDF via WeasyPrint; template variant support; deterministic PDF identifier; `pdf.max_articles` enforcement.  
  Source: `docs/05-pdf-rendering.md`  
  Code: `src/zammad_pdf_archiver/adapters/pdf/render_pdf.py`, `src/zammad_pdf_archiver/config/settings.py`  
  Status: `Implemented`

## Signing and timestamping

- [x] Optional PAdES signing path uses PKCS#12/PFX material.  
  Source: `README.md`, `docs/06-signing-and-timestamp.md`, `docs/08-operations.md`  
  Code: `src/zammad_pdf_archiver/adapters/signing/sign_pdf.py`  
  Status: `Implemented`

- [x] Optional RFC3161 timestamp path classifies retryability as documented:
  - transient: network/timeouts/HTTP 5xx
  - permanent: non-200 (except 5xx), malformed content type/response, misconfiguration.  
  Source: `docs/06-signing-and-timestamp.md`, `docs/08-operations.md`  
  Code: `src/zammad_pdf_archiver/adapters/signing/tsa_rfc3161.py`, `src/zammad_pdf_archiver/app/jobs/retry_policy.py`  
  Status: `Implemented`

- [x] Optional TSA basic auth via `TSA_USER`/`TSA_PASS` requires both values.  
  Source: `docs/06-signing-and-timestamp.md`  
  Code: `src/zammad_pdf_archiver/adapters/signing/tsa_rfc3161.py`  
  Status: `Implemented`

## Path policy and storage

- [x] `archive_path` parsing supports string (`>`) or list-of-strings; empty/invalid values fail permanently.  
  Source: `README.md`, `docs/04-path-policy.md`  
  Code: `src/zammad_pdf_archiver/app/jobs/process_ticket.py`  
  Status: `Implemented`

- [x] `archive_user_mode` resolution supports `owner`, `current_agent`, and `fixed` (with `archive_user`).  
  Source: `README.md`, `docs/04-path-policy.md`  
  Code: `src/zammad_pdf_archiver/app/jobs/process_ticket.py`  
  Status: `Implemented`

- [x] Segment validation/sanitization and root escape prevention are enforced before writes.  
  Source: `docs/04-path-policy.md`, `docs/09-security.md`  
  Code: `src/zammad_pdf_archiver/domain/path_policy.py`, `src/zammad_pdf_archiver/adapters/storage/layout.py`  
  Status: `Implemented`

- [x] Optional `allow_prefixes` enforcement is applied after sanitization.  
  Source: `docs/04-path-policy.md`, `docs/09-security.md`  
  Code: `src/zammad_pdf_archiver/adapters/storage/layout.py`  
  Status: `Implemented`

- [x] Deterministic filename pattern with `{ticket_number}` and `{timestamp_utc}` placeholders.  
  Source: `docs/04-path-policy.md`, `docs/07-storage.md`, `docs/08-operations.md`  
  Code: `src/zammad_pdf_archiver/adapters/storage/layout.py`  
  Status: `Implemented`

- [x] PDF and audit sidecar are written in target location with per-file atomic write (`tmp -> fsync -> replace`) and checksum sidecar content.  
  Source: `README.md`, `docs/01-architecture.md`, `docs/07-storage.md`, `docs/08-operations.md`  
  Code: `src/zammad_pdf_archiver/adapters/storage/fs_storage.py`, `src/zammad_pdf_archiver/domain/audit.py`, `src/zammad_pdf_archiver/app/jobs/process_ticket.py`  
  Status: `Implemented`

- [ ] CIFS/SMB durability semantics are fully guaranteed across all mount/server combinations.  
  Source: `docs/07-storage.md`  
  Code: `src/zammad_pdf_archiver/adapters/storage/fs_storage.py`  
  Status: `Unverified` (depends on runtime storage stack; cannot be proven in unit tests)

## Operations and observability

- [x] `/healthz` endpoint exists and `/metrics` is exposed only when enabled by config.  
  Source: `docs/08-operations.md`  
  Code: `src/zammad_pdf_archiver/app/routes/healthz.py`, `src/zammad_pdf_archiver/app/routes/metrics.py`, `src/zammad_pdf_archiver/app/server.py`  
  Status: `Implemented`

- [x] Prometheus metrics names promised by docs are present and updated by job execution.  
  Source: `docs/08-operations.md`  
  Code: `src/zammad_pdf_archiver/observability/metrics.py`, `src/zammad_pdf_archiver/app/jobs/process_ticket.py`  
  Status: `Implemented`

- [ ] `/metrics` network protection (reverse proxy/firewall policy) is enforced.  
  Source: `docs/08-operations.md`, `docs/09-security.md`  
  Code: external deployment layer  
  Status: `Unverified` (outside repository runtime code)
