# `src/`

This directory contains the service implementation.

High-level layout:

- `src/main.py` – entry point (loads config, configures logging, runs Uvicorn)
- `src/app/` – FastAPI app wiring, middleware, routes
  - `routes/ingest.py` – `POST /ingest` webhook endpoint (always returns 202; runs best-effort processing)
  - `routes/healthz.py` – `GET /healthz`
  - `routes/metrics.py` – `GET /metrics` (only mounted when enabled)
  - `middleware/` – request ID, HMAC verification, rate limit, body size limit
  - `jobs/process_ticket.py` – end-to-end ticket processing pipeline
- `src/adapters/` – external integrations and IO
  - `zammad/` – Zammad REST API client
  - `pdf/` – HTML rendering + PDF generation (WeasyPrint)
  - `signing/` – PAdES signing + RFC3161 TSA client (pyHanko)
  - `storage/` – path layout + atomic writes
  - `snapshot/` – snapshot builder
- `src/domain/` – domain logic (path policy, audit sidecar schema, idempotency, state machine)
- `src/config/` – settings model and config loading/validation

Operator docs live in `docs/` (start with `docs/08-operations.md`).
