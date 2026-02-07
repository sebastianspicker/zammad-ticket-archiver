# Bug Log

Date: 2026-02-07  
Repo: `zammad-pdf-archiver`

## Baseline

- `pytest -vv`: PASS (`156 passed`)
- `ruff check`: PASS
- `mypy src`: PASS
- Local smoke run:
  - Started live service + mock Zammad API
  - Sent `examples/webhook-payload.sample.json` to `POST /ingest`
  - Response: `202 {"accepted":true,"ticket_id":12345}`
  - Output files created:
    - `.../Ticket-12345_2026-02-07.pdf`
    - `.../Ticket-12345_2026-02-07.pdf.json`

## Top 5 Issues (current cycle)

### 1) P0 - HMAC middleware can hang on client disconnect during body read

- Symptom summary:
  - `/ingest` request handling can stall when body streaming is interrupted and ASGI emits `http.disconnect`.
- Reproduction steps:
  1. Run `pytest -q test/unit/test_hmac_verify_middleware.py::test_read_body_returns_when_client_disconnects`.
  2. Before fix: test times out (`TimeoutError`) because `_read_body()` never returns.
- Root cause analysis:
  - `src/zammad_pdf_archiver/app/middleware/hmac_verify.py` `_read_body()` ignored non-`http.request` messages and looped forever on repeated `http.disconnect`.
- Fix:
  - Handle `http.disconnect` explicitly and return collected chunks immediately.
- Regression test:
  - `test/unit/test_hmac_verify_middleware.py::test_read_body_returns_when_client_disconnects`
- Status: Fixed
- Commit: `495e44f`

### 2) P1 - Success note HTML uses unescaped interpolated values

- Symptom summary:
  - Internal success note HTML is assembled with raw `path`/`filename`/`sidecar` strings.
- Reproduction steps:
  1. Call `_success_note_html()` with values containing `<` or `&`.
  2. Output contains raw HTML characters instead of escaped entities.
- Suspected module(s):
  - `src/zammad_pdf_archiver/app/jobs/process_ticket.py` (`_success_note_html`)
- Status: Open

### 3) P1 - No per-ticket concurrency guard can race tag state transitions

- Symptom summary:
  - Two concurrent deliveries for the same ticket (different delivery IDs) can both process and race final tags/notes.
- Reproduction steps:
  1. Trigger two concurrent `/ingest` calls for the same ticket with distinct delivery IDs.
  2. Observe both jobs execute and issue overlapping tag transitions.
- Suspected module(s):
  - `src/zammad_pdf_archiver/app/jobs/process_ticket.py`
  - `src/zammad_pdf_archiver/domain/state_machine.py`
- Status: Open

### 4) P2 - Excessive third-party INFO logging during PDF generation

- Symptom summary:
  - Runtime logs are flooded by `fontTools.subset` INFO lines, reducing operational signal.
- Reproduction steps:
  1. Run local smoke flow.
  2. Inspect app log tail; most lines are font subset internals.
- Suspected module(s):
  - `src/zammad_pdf_archiver/observability/logger.py`
- Status: Open

### 5) P2 - Deprecation warning from pydyf indicates forward-compatibility risk

- Symptom summary:
  - Test runs emit pydyf deprecation warning about PDF identifier/version handling.
- Reproduction steps:
  1. Run `pytest -vv`.
  2. Observe warnings from `pydyf` about identifier handling.
- Suspected module(s):
  - `src/zammad_pdf_archiver/adapters/pdf/render_pdf.py`
  - dependency compatibility (`weasyprint`/`pydyf`)
- Status: Open
