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
  - Internal success note HTML was assembled with raw values, allowing HTML injection in ticket notes.
- Reproduction steps:
  1. Run `pytest -q test/unit/test_process_ticket_notes.py::test_success_note_html_escapes_untrusted_values`.
  2. Before fix: test fails because `<script>`, `<img>`, `<svg>` appear unescaped in generated HTML.
- Root cause analysis:
  - `src/zammad_pdf_archiver/app/jobs/process_ticket.py` `_success_note_html()` interpolated
    `storage_dir`, `filename`, `sidecar_path`, `request_id`, `delivery_id`, and `timestamp_utc` directly
    into HTML.
- Fix:
  - Escape all untrusted string fields in `_success_note_html()` using `html.escape()`.
- Regression test:
  - `test/unit/test_process_ticket_notes.py::test_success_note_html_escapes_untrusted_values`
- Status: Fixed
- Commit: `b723593`

### 3) P1 - No per-ticket concurrency guard can race tag state transitions

- Symptom summary:
  - Two concurrent deliveries for the same ticket (different delivery IDs) could both process and
    write duplicate success notes/files.
- Reproduction steps:
  1. Run
     `pytest -q test/unit/test_process_ticket_concurrency.py::test_process_ticket_serializes_same_ticket_concurrent_runs`.
  2. Before fix: assertion fails (`_notes_written == 2`), proving concurrent duplicate processing.
- Root cause analysis:
  - `process_ticket()` had no per-ticket in-flight guard, so multiple background jobs for the same ticket
    could enter the pipeline concurrently before tags were transitioned.
- Fix:
  - Added in-flight ticket guard in `process_ticket`:
    - `_IN_FLIGHT_TICKETS` set with async guard lock
    - `_try_acquire_ticket()` / `_release_ticket()` helpers
    - concurrent same-ticket runs now skip with `process_ticket.skip_ticket_in_flight`.
- Regression test:
  - `test/unit/test_process_ticket_concurrency.py::test_process_ticket_serializes_same_ticket_concurrent_runs`
- Status: Fixed
- Commit: `da4ec5f`

### 4) P2 - Excessive third-party INFO logging during PDF generation

- Symptom summary:
  - Runtime logs were flooded by `fontTools.subset` INFO lines, reducing operational signal.
- Reproduction steps:
  1. Run local smoke flow.
  2. Inspect app log tail; most lines are font subset internals.
  3. Run `pytest -q test/unit/test_logger_config.py::test_configure_logging_reduces_fonttools_noise`.
  4. Before fix: effective level for `fontTools` is `INFO`.
- Root cause analysis:
  - `configure_logging()` set root level but left `fontTools` logger inheriting `INFO`.
- Fix:
  - Set `fontTools` logger level to `WARNING` in logger configuration.
- Regression test:
  - `test/unit/test_logger_config.py::test_configure_logging_reduces_fonttools_noise`
- Status: Fixed
- Commit: `b723593`

### 5) P2 - Deprecation warning from pydyf indicates forward-compatibility risk

- Symptom summary:
  - Test runs emitted pydyf deprecation warnings from WeasyPrint internals.
- Reproduction steps:
  1. Run
     `pytest -q test/integration/test_pdf_rendering.py::test_render_pdf_does_not_emit_pydyf_identifier_deprecation_warning`.
  2. Before fix: assertion fails due `DeprecationWarning` message
     `"PDF objects don’t take version or identifier during initialization anymore..."`.
- Root cause analysis:
  - Current WeasyPrint/pydyf combination emits this warning inside dependency code-paths during PDF write.
  - The warning is external to app logic but pollutes test/runtime diagnostics.
- Fix:
  - Added targeted warning filter around `HTML.write_pdf(...)` in
    `src/zammad_pdf_archiver/adapters/pdf/render_pdf.py`.
  - Also removed unsupported CSS declarations in default template that caused additional WeasyPrint
    warning noise (`display: grid`, `gap` on unsupported contexts, `font-weight: 650`).
- Regression tests:
  - `test/integration/test_pdf_rendering.py::test_render_pdf_does_not_emit_pydyf_identifier_deprecation_warning`
  - `test/integration/test_pdf_rendering.py::test_render_pdf_default_template_avoids_ignored_css_warnings`
- Status: Fixed
- Commit: `1f21136`

## Additional scan after top-5 closure

- Performed:
  - `pytest -q` (full suite)
  - `pytest -q -W error`
  - local smoke run with `examples/webhook-payload.sample.json`
- Result:
  - Found and fixed one additional reproducible logic defect (see issue #6 below).

### 6) P1 - In-flight skip could poison delivery-id replay cache

- Symptom summary:
  - A delivery skipped due `process_ticket.skip_ticket_in_flight` could still be recorded as
    `seen` in idempotency cache, causing later legitimate retries with the same delivery ID to be skipped.
- Reproduction steps:
  1. Run
     `pytest -q test/unit/test_process_ticket_inflight_idempotency.py::test_skipped_inflight_delivery_id_is_not_poisoned_for_retry`.
  2. Before fix: retry run logs `process_ticket.skip_delivery_id_seen` and never writes success note.
- Root cause analysis:
  - `process_ticket()` added `delivery_id` to `_delivery_ids` cache before acquiring the in-flight ticket guard.
  - If ticket was already in-flight, function returned early, but delivery ID remained cached.
- Fix:
  - Reordered logic: acquire in-flight ticket guard first; only then evaluate/add delivery ID to replay cache.
- Regression test:
  - `test/unit/test_process_ticket_inflight_idempotency.py::test_skipped_inflight_delivery_id_is_not_poisoned_for_retry`
- Status: Fixed
- Commit: `ec2cc26`

### 7) P2 - Human log format emits structlog warning on every exception

- Symptom summary:
  - Runtime/test logs include `UserWarning: Remove format_exc_info...` whenever `log.exception(...)` is called
    in human log mode, polluting diagnostics.
- Reproduction steps:
  1. Run
     `pytest -q test/unit/test_logger_config.py::test_human_logging_does_not_emit_format_exc_info_warning`.
  2. Before fix: assertion fails because warning is captured from structlog processor chain.
- Root cause analysis:
  - `src/zammad_pdf_archiver/observability/logger.py` always included
    `structlog.processors.format_exc_info` even when using `ConsoleRenderer`, which expects raw exception
    data for pretty rendering and warns when formatted beforehand.
- Fix:
  - Apply `format_exc_info` only for JSON log mode; omit it for human/console mode.
- Regression test:
  - `test/unit/test_logger_config.py::test_human_logging_does_not_emit_format_exc_info_warning`
- Status: Fixed
- Commit: `PENDING` (current workspace change)
