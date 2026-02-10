# Product Requirements Document (PRD)
# Zammad PDF Archiver

**Version:** 0.1  
**Status:** Living document (aligned with current implementation and docs)  
**Last updated:** 2026-02-10

NFR verification tests live in `test/nfr/`; implementation order and per-NFR guidance: [docs/NFR-implementation-order.md](NFR-implementation-order.md).

---

## 1. Overview

### 1.1 Product name and summary

**Zammad PDF Archiver** (`zammad-ticket-archiver` / `zammad-pdf-archiver`) is a webhook-driven service that converts Zammad help-desk tickets into archived, human-readable PDF files on filesystem storage (local disk or mounted CIFS/SMB). It can optionally apply PAdES signatures and RFC3161 timestamps to support long-term legal and audit requirements.

### 1.2 Problem statement

Organizations using Zammad need to:

- Preserve closed or sensitive tickets as immutable, auditable documents.
- Store archives on existing file shares or retention systems without a separate archive database.
- Optionally meet compliance needs for signed and timestamped PDFs (e.g. eIDAS, national archiving rules).

Manual export does not scale; a dedicated UI would duplicate Zammad’s workflow. A small, trigger-based service that turns “archive this ticket” into a PDF (and optional signature) fits into existing Zammad workflows and storage infrastructure.

### 1.3 Solution summary

A single FastAPI service that:

1. Exposes a webhook endpoint (`POST /ingest`) for Zammad triggers.
2. Fetches ticket, tags, and articles from the Zammad REST API.
3. Builds a normalized snapshot, renders PDF via Jinja2 + WeasyPrint.
4. Optionally signs (PAdES) and timestamps (RFC3161) the PDF.
5. Writes the PDF and an audit sidecar JSON to a configurable filesystem path.
6. Updates the ticket with an internal note and tag state (`pdf:signed` / `pdf:error`).

Processing is asynchronous (202 Accepted); path and security are configurable and documented.

---

## 2. Goals and non-goals

### 2.1 Goals

| Goal | Rationale |
|------|------------|
| Reliable webhook-to-PDF pipeline | Core value: ticket → PDF + sidecar with minimal operator intervention. |
| Configurable, policy-bound storage paths | Fit org structure (e.g. by customer, year) and existing shares/ACLs. |
| Optional PAdES + RFC3161 | Support compliance and long-term evidential value without mandating PKI. |
| Secure and observable by default | HMAC webhook auth, rate limits, body limits, structured logs, optional metrics. |
| Operate in single-process / single-node deployments | No mandatory message queue or distributed store. |

### 2.2 Non-goals (out of scope by design)

| Non-goal | Reason |
|----------|--------|
| Export of attachment binary payloads | Attachments remain metadata-only in snapshot/PDF; reduces scope and storage. |
| Archive browsing or search UI | Archive is filesystem-based; search is handled by OS/tools or other systems. |
| Distributed durable queue | Acceptable to use in-process background tasks and best-effort dedupe. |
| Durable distributed idempotency store | In-memory delivery-ID dedupe is explicit tradeoff for simplicity. |
| Built-in retention/WORM policy engine | Retention and WORM are handled by storage/OS or external systems. |
| Built-in encryption-at-rest management | Handled by filesystem or storage layer. |
| Multi-tenant isolation beyond path policy | Isolation via path policy + external ACLs; no in-app tenant DB. |

---

## 3. User personas and stakeholders

| Persona | Role | Needs |
|---------|------|--------|
| **Zammad administrator** | Configures triggers, custom fields, webhooks, macros | Clear setup steps, field names, trigger tag, webhook URL and HMAC. |
| **Agent (help desk)** | Closes tickets and requests archiving | One-click macro (e.g. “Archive (sign PDF)”) that adds trigger tag; visibility via ticket note and tags. |
| **Compliance / legal** | Needs evidence of what was archived and when | Signed/timestamped PDFs, audit sidecar with checksum and metadata, verification scripts. |
| **Operations / DevOps** | Deploys and runs the service | Config via env/YAML, health and metrics, logs, single container or systemd, known limitations (e.g. in-memory dedupe). |
| **Security** | Evaluates trust boundaries and hardening | Documented threat model, HMAC, rate/body limits, path confinement, secret handling, optional transport checks. |

---

## 4. User stories and use cases

### 4.1 Primary flow

- **As an** agent, **I want to** archive a ticket as a signed PDF **so that** it is stored in our standard archive path and the ticket shows a clear “archived” state.
- **Acceptance:** Agent runs macro → trigger tag added → Zammad sends webhook → service returns 202 → background job fetches ticket, renders PDF, signs (if enabled), writes PDF + sidecar, adds internal note and `pdf:signed` tag.

### 4.2 Configuration and path control

- **As a** Zammad admin, **I want to** define where each ticket is archived (e.g. by customer and year) **so that** files land in the correct share and folder structure.
- **Acceptance:** Ticket has `archive_path` (and optionally `archive_user_mode` / `archive_user`); service builds path under `storage.root`, validates and sanitizes segments, enforces optional `allow_prefixes`.

### 4.3 Error handling and retries

- **As an** agent or operator, **I want to** see why archiving failed and whether to retry **so that** I can fix data or infrastructure and re-run.
- **Acceptance:** On failure, ticket gets internal error note (scrubbed message + classification + action hint), `pdf:error` tag; transient failures keep trigger tag for retry; permanent failures remove it; docs describe tag states and recovery.

### 4.4 Security and hardening

- **As** operations, **I want** webhook calls to be authenticated and the service to reject oversized or excessive requests **so that** only Zammad can trigger work and the service is resilient to abuse.
- **Acceptance:** HMAC verification when secret is set; optional delivery ID requirement; body size limit and rate limiting; transport safety options (block unsafe HTTP/TLS/loopback by default).

---

## 5. Functional requirements

### 5.1 Ingest and workflow

| ID | Requirement | Priority |
|----|-------------|----------|
| F1 | Accept `POST /ingest` with JSON body (schema requires ticket id); extract ticket ID from `ticket.id` or `ticket_id`. | P0 |
| F2 | Return `202 Accepted` and process work in background; response body `{"accepted": true, "ticket_id": <id>}`. | P0 |
| F3 | Support configurable trigger tag (default `pdf:sign`); require trigger tag when `workflow.require_tag=true`. | P0 |
| F4 | Skip processing when ticket already has `pdf:signed`. | P0 |
| F5 | Apply tag state machine: processing → remove done/error/trigger, add `pdf:processing`; success → add `pdf:signed`; failure → add `pdf:error`, optionally keep trigger for retry. | P0 |
| F6 | Optional in-memory deduplication by `X-Zammad-Delivery` with configurable TTL. | P1 |
| F7 | Per-ticket in-flight guard so the same ticket is not processed concurrently. | P0 |

### 5.2 Zammad integration

| ID | Requirement | Priority |
|----|-------------|----------|
| F8 | Fetch ticket, tags, and articles via Zammad REST API (Token auth). | P0 |
| F9 | Create internal ticket note on success (path, filename, sidecar, size, sha256, request_id, delivery_id, time). | P0 |
| F10 | Create internal ticket note on failure (classification, scrubbed error, action hint). | P0 |
| F11 | Map HTTP errors to retry policy (transient vs permanent). | P0 |

### 5.3 Path and storage

| ID | Requirement | Priority |
|----|-------------|----------|
| F12 | Require ticket custom field `archive_path` (string with `>` or list of segments); validate and sanitize segments; enforce max depth and length. | P0 |
| F13 | Support `archive_user_mode`: `owner`, `current_agent`, `fixed`; first path component = user (owner login, current agent, or custom `archive_user`). | P0 |
| F14 | Build path under `storage.root` only; reject paths outside root. | P0 |
| F15 | Optional allow-list of path prefixes (`allow_prefixes`). | P1 |
| F16 | Configurable filename pattern (e.g. `Ticket-{ticket_number}_{timestamp_utc}.pdf`). | P1 |
| F17 | Write PDF and audit sidecar JSON (same base name + `.json`); configurable atomic write and fsync. | P0 |

### 5.4 Snapshot and PDF

| ID | Requirement | Priority |
|----|-------------|----------|
| F18 | Build normalized snapshot from ticket + tags + articles; sanitize HTML; deterministic article order. | P0 |
| F19 | Render PDF from snapshot using Jinja2 template and WeasyPrint; support configurable template variant and locale/timezone. | P0 |
| F20 | Limit number of articles included (configurable `max_articles`). | P1 |
| F21 | Attachments in snapshot are metadata-only (no binary embedding). | P0 |

### 5.5 Signing and timestamping (optional)

| ID | Requirement | Priority |
|----|-------------|----------|
| F22 | Optional PAdES signing via PKCS#12/PFX; configurable reason and location. | P1 |
| F23 | Optional RFC3161 timestamp token from configurable TSA URL; TSA auth via env (e.g. TSA_USER/TSA_PASS). | P1 |
| F24 | Include signing/TSA metadata in audit sidecar (e.g. cert fingerprint, tsa_used). | P1 |

### 5.6 API and observability

| ID | Requirement | Priority |
|----|-------------|----------|
| F25 | Expose `GET /healthz` with status, service name, version, time; optional omit of version/service via `HEALTHZ_OMIT_VERSION`. | P0 |
| F26 | Optionally expose `GET /metrics` (Prometheus) when `observability.metrics_enabled=true`; optional Bearer auth via `METRICS_BEARER_TOKEN`. | P1 |
| F27 | Emit structured logs with request_id, ticket_id, delivery_id; redact secrets in logs and error notes. | P0 |

---

## 6. Non-functional requirements

### 6.1 Security

| ID | Requirement | Priority |
|----|-------------|----------|
| NFR1 | Verify webhook payload with HMAC (`X-Hub-Signature: sha1=<hex>` or `sha256=<hex>`); fail closed when secret is configured. | P0 |
| NFR2 | Enforce request body size limit and token-bucket rate limiting on ingest. | P0 |
| NFR3 | Validate and confine all storage paths under `storage.root`; reject path traversal and unsafe segments. | P0 |
| NFR4 | Scrub secrets and secret-like values from logs and from text written to ticket notes. | P0 |
| NFR5 | By default disallow plaintext HTTP upstreams, disabled TLS verification, and loopback/link-local upstreams; require explicit hardening overrides for exceptions. | P1 |

### 6.2 Operational

| ID | Requirement | Priority |
|----|-------------|----------|
| NFR6 | Configuration via environment variables and optional YAML; precedence env > YAML > defaults. | P0 |
| NFR7 | Run as single process (e.g. uvicorn); support Docker and systemd deployment. | P0 |
| NFR8 | Document Zammad setup (custom fields, core workflow, macro, webhook), path policy, signing/TSA, storage, operations, and security. | P0 |

### 6.3 Compatibility and constraints

| ID | Requirement | Priority |
|----|-------------|----------|
| NFR9 | Support Python 3.12+; depend on FastAPI, httpx, WeasyPrint, pyHanko, and documented versions. | P0 |
| NFR10 | No mandatory external queue or distributed idempotency store; in-memory dedupe and in-flight guard are best-effort and process-local. | P0 |

---

## 7. Success criteria

| Criterion | Measure |
|----------|---------|
| Correctness | Ticket → PDF + sidecar written under correct path; tags and note updated; SHA-256 in sidecar matches file. |
| Security | Webhook rejected without valid HMAC when secret set; no path escape; no secret leakage in logs/notes. |
| Operability | Health endpoint and optional metrics; logs and ticket notes sufficient to debug failures and retry. |
| Documentation | Admins can configure Zammad and archiver from docs; operators can deploy and troubleshoot from runbooks. |
| Verification | NFRs are covered by dedicated tests in `test/nfr/`; see [NFR implementation order](NFR-implementation-order.md). |

---

## 8. Scope and phases

### 8.1 Current scope (as implemented)

- All P0 and P1 items above that are implemented in the codebase.
- Single-service architecture: FastAPI + in-process background tasks.
- Config: env + optional YAML; Zammad, workflow, storage, PDF, signing, observability, hardening.

Verification: dedicated NFR tests in `test/nfr/` and integration/unit coverage; see [NFR implementation order](NFR-implementation-order.md).

### 8.2 Future considerations (not committed)

- Durable idempotency (e.g. Redis or DB) for delivery ID.
- Optional attachment binary inclusion (configurable, with size limits).
- Additional template variants or localization: **compact** variant is implemented; further variants can be added under `templates/<name>/`.
- Other items above remain out of scope until explicitly added to this PRD and roadmap.

---

## 9. Dependencies and constraints

### 9.1 External dependencies

- **Zammad** (REST API, webhooks, triggers, macros).
- **Storage**: filesystem (local or CIFS/SMB mount) with write access for the process.
- **Optional**: TSA endpoint (RFC3161) and PKCS#12/PFX for signing.

### 9.2 Technical constraints

- Processing is best-effort after 202: no guaranteed delivery or exactly-once across restarts.
- One writer per ticket at a time (in-memory lock); delivery-ID dedupe is in-memory and TTL-based.
- Attachments are not written to disk by the archiver; only metadata in snapshot/PDF.

---

## 10. Glossary

| Term | Definition |
|------|------------|
| **Audit sidecar** | JSON file next to each PDF with checksum, storage path, signing/TSA metadata, service version. |
| **Archive path** | Ticket custom field defining path segments under `storage.root`. |
| **Archive user mode** | Strategy for first path segment: `owner`, `current_agent`, or `fixed` (custom field). |
| **Delivery ID** | `X-Zammad-Delivery` header; used for best-effort in-memory deduplication. |
| **PAdES** | PDF Advanced Electronic Signatures profile. |
| **RFC3161** | Timestamp protocol used by Time Stamping Authorities. |
| **Snapshot** | Normalized, render-ready model built from Zammad ticket + tags + articles. |
| **TSA** | Time Stamping Authority endpoint for timestamp tokens. |
| **Trigger tag** | Tag that causes processing when present (e.g. `pdf:sign`). |

---

## 11. References

- [NFR implementation order and verification](NFR-implementation-order.md)
- [00 - Overview](00-overview.md)
- [01 - Architecture](01-architecture.md)
- [02 - Zammad Setup](02-zammad-setup.md)
- [03 - Data Model](03-data-model.md)
- [04 - Path Policy](04-path-policy.md)
- [05 - PDF Rendering](05-pdf-rendering.md)
- [06 - Signing and Timestamp](06-signing-and-timestamp.md)
- [07 - Storage](07-storage.md)
- [08 - Operations](08-operations.md)
- [09 - Security](09-security.md)
- [API](api.md)
- [Config reference](config-reference.md)
