# zammad-ticket-archiver

`zammad-ticket-archiver` is a FastAPI webhook service that archives Zammad tickets as PDF files on a filesystem target (local path or mounted CIFS/SMB).

Processing pipeline:

`webhook -> fetch ticket data -> build snapshot -> render PDF -> optional sign -> optional timestamp -> store PDF + audit sidecar -> update ticket tags + note`

## What It Does

- Exposes `POST /ingest` to receive Zammad webhooks.
- Fetches ticket, tags, and articles from Zammad REST API.
- Renders a PDF with Jinja2 templates + WeasyPrint.
- Optionally applies:
  - PAdES signature (PKCS#12/PFX)
  - RFC3161 timestamp token (TSA)
- Writes two files:
  - PDF
  - audit sidecar JSON (`<filename>.json`, for PDFs usually `...pdf.json`)
- Writes an internal note to the ticket and transitions archive tags.

## Scope

This repository provides:

- FastAPI service endpoints:
  - `POST /ingest`
  - `POST /ingest/batch`
  - `POST /retry/{ticket_id}`
  - `GET /jobs/{ticket_id}`
  - `GET /healthz`
  - `GET /metrics` (when enabled)
- End-to-end ticket processing:
  1. receive webhook
  2. fetch ticket + tags + articles from Zammad
  3. build normalized snapshot model
  4. render PDF
  5. optionally sign and timestamp
  6. write PDF + sidecar JSON to storage
  7. update ticket note + tags
- Runtime hardening controls:
  - webhook HMAC verification
  - optional delivery ID requirement
  - request size limits
  - rate limiting
  - transport safety checks for upstream URLs

## Non-goals

Out of scope by design:

- exporting attachment binary payloads by default (attachments are metadata-only in snapshot/PDF; optional `pdf.include_attachment_binary` can write binaries to disk and the sidecar)
- archive browsing/search UI
- distributed durable queue
- durable distributed idempotency store (default; optional Redis backend available)
- built-in retention/WORM policy engine
- built-in encryption-at-rest management
- multi-tenant isolation beyond path policy and external ACLs

## How Archiving Works

### Trigger Tag

Default trigger tag is `pdf:sign` (`workflow.trigger_tag`).

Processing behavior:
- `workflow.require_tag=true` (default): ticket is processed only when trigger tag is present.
- If ticket already has `pdf:signed`, processing is skipped.

### Required Ticket Fields

Defaults are configurable for the first two fields:
- `archive_path` (`fields.archive_path`, required)
- `archive_user_mode` (`fields.archive_user_mode`, optional, default `owner`)

`archive_user_mode` values:
- `owner`: use `ticket.owner.login`
- `current_agent`: use webhook `payload.user.login`, fallback `ticket.updated_by.login`
- `fixed`: use the custom field configured as `fields.archive_user` (default `archive_user`; required in this mode)

The field names for `archive_path`, `archive_user_mode`, and `archive_user` are configurable via `fields.*` in config or `FIELDS_ARCHIVE_PATH`, `FIELDS_ARCHIVE_USER_MODE`, `FIELDS_ARCHIVE_USER` in the environment.

### Tag State Transitions

- Start: `apply_processing()`
  - remove `pdf:signed`, `pdf:error`, trigger tag
  - add `pdf:processing`
- Success: `apply_done()`
  - remove `pdf:processing`, `pdf:error`, trigger tag
  - add `pdf:signed`
- Failure: `apply_error()`
  - remove `pdf:processing`, `pdf:signed`
  - add `pdf:error`
  - transient failures keep/re-add trigger tag
  - permanent failures remove trigger tag

## Architecture Overview

```mermaid
flowchart LR
  Z["Zammad"] -->|POST /ingest| I["FastAPI ingress"]
  I --> J["Background job: process_ticket"]
  J --> ZA["Zammad adapter"]
  ZA --> SN["Snapshot builder"]
  SN --> PDF["Jinja2 + WeasyPrint"]
  PDF --> SG["pyHanko signer (optional)"]
  SG --> TSA["RFC3161 TSA (optional)"]
  PDF --> ST["Storage adapter"]
  SG --> ST
  ST --> ZA
```

Detailed architecture and state diagrams:
- [`docs/01-architecture.md`](docs/01-architecture.md)

## High-Level Behavior

```mermaid
flowchart TD
  A["Agent applies macro (adds pdf:sign)"] --> B["Zammad trigger sends webhook"]
  B --> C["POST /ingest returns 202"]
  C --> D["Background processing job"]
  D --> E["PDF + sidecar written"]
  E --> F["Ticket note + final tags"]
```

## Quickstart (Development)

Prerequisites:
- Docker with `docker compose`
- Python 3.12+ (for local lint/test)

1. Create local environment file:

```bash
cp .env.example .env
```

2. Set minimum required values in `.env`:
- `ZAMMAD_BASE_URL`
- `ZAMMAD_API_TOKEN`
- `STORAGE_ROOT`
- webhook auth:
  - recommended: `WEBHOOK_HMAC_SECRET`
  - test-only fallback: `HARDENING_WEBHOOK_ALLOW_UNSIGNED=true`

3. Start dev stack:

```bash
make dev
```

4. Run validation (lint, test, type-check, build):

```bash
make lint
make test
mypy . --config-file pyproject.toml
python -m build
```

Optional smoke test (requires env and optional services):

```bash
bash scripts/ci/smoke-test.sh
```

Endpoints:
- `POST /ingest`
- `POST /ingest/batch`
- `POST /retry/{ticket_id}`
- `GET /jobs/{ticket_id}`
- `GET /healthz`
- `GET /metrics` (only when enabled)

## Configuration

Precedence (highest first):
1. Environment variables (including values loaded from `.env`)
2. Flat env aliases (backward-compat keys)
3. YAML config (`CONFIG_PATH`, or `config/config.yaml` when present)
4. Defaults in settings model

Configuration references:
- [`.env.example`](.env.example)
- [`config/config.example.yaml`](config/config.example.yaml)
- [`docs/config-reference.md`](docs/config-reference.md)

## Operational Notes

- All output paths are validated and confined under `storage.root`.
- Default storage writes are atomic (`storage.atomic_write=true`) and fsynced (`storage.fsync=true`).
- Signing requires `signing.enabled=true` and `signing.pfx_path`.
- Timestamping requires signing plus:
  - `signing.timestamp.enabled=true`
  - `signing.timestamp.rfc3161.tsa_url`
- TSA basic auth (if needed) uses env-only keys:
  - `TSA_USER`
  - `TSA_PASS`
- Delivery ID dedupe is in-memory only and resets on process restart. For consistent deduplication across restarts or multiple instances, use Redis (`workflow.idempotency_backend=redis`, `workflow.redis_url`); see [Operations](docs/08-operations.md).
- Processing after `202` is **best-effort**: there is no guaranteed retry and work may be lost on process restart. A durable queue is a possible future feature. See [Processing and Idempotency](docs/08-operations.md#4-processing-and-idempotency-behavior).
- If a ticket is stuck in `pdf:processing` after a crash, see [Stuck in pdf:processing](docs/faq.md#why-is-a-ticket-stuck-with-pdfprocessing) in the FAQ.

Operational docs:
- [`docs/04-path-policy.md`](docs/04-path-policy.md)
- [`docs/06-signing-and-timestamp.md`](docs/06-signing-and-timestamp.md)
- [`docs/07-storage.md`](docs/07-storage.md)
- [`docs/08-operations.md`](docs/08-operations.md)
- [`docs/09-security.md`](docs/09-security.md)

## Validation commands

| Action | Command |
|--------|--------|
| Lint | `make lint` (ruff) |
| Test | `make test` (pytest) |
| Test (fast) | `make test-fast` (static + unit) |
| Type-check | `mypy . --config-file pyproject.toml` |
| Full QA | `make qa` (lint + mypy + static + unit + integration + nfr) |
| Build | `python -m build` (sdist + wheel) |
| Smoke test | `bash scripts/ci/smoke-test.sh` (optional; needs env) |
| Dev run | `make dev` (Docker Compose dev stack) |

## Documentation (index)

- [`docs/PRD.md`](docs/PRD.md) – Product Requirements Document
- [`docs/adr/`](docs/adr/) – Architecture Decision Records
- [`docs/01-architecture.md`](docs/01-architecture.md)
- [`docs/02-zammad-setup.md`](docs/02-zammad-setup.md)
- [`docs/03-data-model.md`](docs/03-data-model.md)
- [`docs/04-path-policy.md`](docs/04-path-policy.md)
- [`docs/05-pdf-rendering.md`](docs/05-pdf-rendering.md)
- [`docs/06-signing-and-timestamp.md`](docs/06-signing-and-timestamp.md)
- [`docs/07-storage.md`](docs/07-storage.md)
- [`docs/08-operations.md`](docs/08-operations.md)
- [`docs/09-security.md`](docs/09-security.md)
- [`docs/api.md`](docs/api.md)
- [`docs/config-reference.md`](docs/config-reference.md)
- [`docs/faq.md`](docs/faq.md)
- [`docs/release-checklist.md`](docs/release-checklist.md) – Release and deployment checklist
- [`docs/deploy.md`](docs/deploy.md) – Production deployment

## Glossary

- **Audit sidecar**: JSON file written next to each PDF containing checksum and processing metadata.
- **Archive path**: ticket custom field defining path segments under storage root.
- **Archive user mode**: strategy that selects the first directory component (`owner`, `current_agent`, `fixed`).
- **Delivery ID**: `X-Zammad-Delivery` header used for best-effort in-memory deduplication.
- **HMAC**: webhook signature validation via `X-Hub-Signature: sha1=<hex>` or `sha256=<hex>`.
- **PAdES**: PDF Advanced Electronic Signatures profile.
- **RFC3161**: timestamp protocol used by Time Stamping Authorities.
- **TSA**: Time Stamping Authority endpoint used for timestamp tokens.
