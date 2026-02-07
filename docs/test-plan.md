# Test Plan (CI / GitHub-ready)

This repository is a webhook service that:

Zammad ticket → snapshot → PDF → (optional) PAdES signature + RFC3161 timestamp → storage + audit sidecar → ticket tags + note.

The goal of this test plan is to keep CI reliable, deterministic, fast, and representative of real behavior while avoiding:
- network access to real Zammad/TSA instances
- flaky “golden PDF” byte-for-byte comparisons
- wall-clock dependencies (unless injected/frozen)

## Test categories

### Unit tests (`test/unit/`)

Unit tests validate pure functions and single components with minimal IO.

Examples (non-exhaustive):
- path policy sanitization/validation (`domain.path_policy`, `adapters.storage.layout`)
- idempotency TTL behavior (`domain.idempotency`)
- Zammad client behavior and error mapping (`adapters.zammad.client`)
- PDF signing primitives (`adapters.signing.sign_pdf`) using an ephemeral PFX created in the test
- TSA request/response wiring at the adapter boundary (mocked HTTP)
- state machine transitions for ticket tags (`domain.state_machine`)

Rules:
- Use `tmp_path` for all filesystem interactions.
- Avoid real time; use dependency injection/monkeypatching for timestamps where needed.
- Any HTTPX calls must be mocked (prefer `respx`).

### Integration tests (`test/integration/`)

Integration tests exercise the request/job pipeline with multiple components wired together:
- FastAPI app wiring/middleware order
- ingestion endpoint behavior
- `process_ticket` end-to-end behavior: snapshot → PDF render → storage write(s) → Zammad tag transitions + internal note

Rules:
- No real network: Zammad API and TSA are mocked with `respx`.
- Time is frozen by patching `app.jobs.process_ticket._now_utc` when validating timestamps/paths/audit payloads.
- PDF assertions are structural, not golden:
  - `pdf_bytes.startswith(b"%PDF")`
  - size thresholds (if needed) and key markers (e.g. signature `/ByteRange` when signing is enabled)
  - sidecar JSON is validated structurally (required keys, stable formatting expectations)

## Mocked components

### Zammad API (required in most integration tests)

Mocked via `respx` (HTTPX).

Endpoints commonly mocked:
- `GET /api/v1/tickets/{id}`
- `GET /api/v1/tags?object=Ticket&o_id={id}`
- `GET /api/v1/ticket_articles/by_ticket/{id}`
- `POST /api/v1/tags/add`
- `POST /api/v1/tags/remove`
- `POST /api/v1/ticket_articles`

Policy:
- No unexpected outbound HTTP; tests should fail if a new endpoint is introduced without a mock.
- Prefer response payloads that match real Zammad shapes (including “version quirks”, e.g. tags list wrapper).

### RFC3161 TSA (optional)

Mocked via `respx` at the HTTP boundary.

Policy:
- Transient vs permanent error classification must be asserted (network errors + HTTP 5xx => transient).
- Content-Type enforcement (`application/timestamp-reply`) must be asserted.

## Required fixtures

This repo primarily uses inline fixtures to keep tests self-contained and deterministic. When adding new tests, prefer:

- **Ticket payloads:** minimal but realistic JSON for `ticket`, `tags`, and `ticket_articles`.
- **Snapshot samples:** use `adapters.snapshot.build_snapshot` via mocked Zammad responses where possible.
- **Signing material:** generate an ephemeral PKCS#12/PFX in-test (do not store real certs/keys in the repo).
- **Storage roots:** always a `tmp_path` subdirectory.

If future tests need larger payloads, store them under `test/fixtures/` as JSON files and keep them small,
stable, and versioned.

## Determinism and anti-flake rules

- **Time:** patch `_now_utc()` in `app.jobs.process_ticket` to a fixed `datetime(…, tzinfo=UTC)` whenever the
  output includes timestamps or date-based filenames.
- **Filesystem:** never write outside `tmp_path`; validate traversal protection (including symlink escapes).
- **PDF checks:** never compare full PDF bytes. Prefer:
  - header check (`%PDF-`)
  - presence/absence of signature markers (e.g. `/ByteRange`)
  - sidecar JSON structure checks
- **Network:** use `respx` with explicit mocked routes; do not depend on any external service.
- **Randomness:** avoid depending on random serial numbers or ordering; when unavoidable, assert structure only.

## Coverage expectations for critical paths

The test suite should cover, at minimum:

1) **HMAC verification**
   - correct parsing and enforcement of `X-Hub-Signature` (sha1)
   - missing/malformed signatures rejected when a secret is configured

2) **Idempotency**
   - duplicate webhook deliveries (`X-Zammad-Delivery`) must not trigger multiple processing runs

3) **Path policy safety**
   - traversal protection: dot segments, separators, and symlink escapes must be rejected
   - allowlist prefixes (`allow_prefixes`) enforced when configured

4) **Storage atomicity**
   - atomic replace semantics (`os.replace`) used for both PDF and sidecar
   - temp files are cleaned up on error

5) **Signing + TSA**
   - signing succeeds with a valid PFX and produces a structurally valid PDF
   - TSA transient vs permanent errors are classified correctly

6) **Workflow/tag transitions**
   - success path: `pdf:processing` → `pdf:signed` and trigger tag removed
   - failure path: `pdf:error` applied; transient failures keep trigger, permanent failures drop trigger

## Top 5 critical behavior matrix (implemented)

1) **HMAC verification correctness**
   - `test/integration/test_hmac_verify.py`
   - Covers valid/invalid/missing/malformed signatures, request-body integrity, and legacy secret fallback.

2) **Idempotency with `X-Zammad-Delivery`**
   - `test/integration/test_e2e_smoke.py`
   - Covers duplicate webhook deliveries at `/ingest` with the same delivery ID; second request is accepted but processing is skipped.

3) **Path policy traversal protection**
   - `test/unit/test_path_policy.py`
   - `test/integration/test_process_ticket_v01.py`
   - Covers dot-segment/separator rejection, symlink escape checks, and end-to-end permanent failure behavior for invalid archive paths.

4) **Atomic write behavior for PDF + sidecar**
   - `test/integration/test_audit_sidecar.py`
   - `test/integration/test_storage_atomic.py`
   - Covers atomic write usage for both artifacts, write ordering expectations, overwrite semantics, and temp-file cleanup on errors.

5) **Signing + TSA transient classification + tag transitions**
   - `test/integration/test_process_ticket_signing.py`
   - `test/unit/test_tsa_rfc3161.py`
   - Covers signing with generated PFX material, TSA network/HTTP transient failures, and workflow tag behavior (transient keeps trigger, permanent drops trigger).

## CI command matrix

CI (and local) should run:

- `ruff check .`
- `pytest -q`
- `mypy . --config-file pyproject.toml` (if enabled)
- `python -m build` (sdist/wheel)

Optional (recommended):
- `docker build .`
