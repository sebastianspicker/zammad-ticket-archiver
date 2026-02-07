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
- Commit: `8d32208`

### 8) P0 - Human exception logs leaked secrets in traceback output

- Symptom summary:
  - In human log mode, `logger.exception(...)` emitted raw credential values from exception text
    (and rich traceback context), violating log-redaction expectations.
- Reproduction steps:
  1. Run
     `pytest -q test/unit/test_logger_config.py::test_human_logging_redacts_secrets_in_exception_traceback`.
  2. Before fix: assertion fails because output contains `topsecret` and `abc123`.
- Root cause analysis:
  - `src/zammad_pdf_archiver/observability/logger.py:77` configured `ConsoleRenderer` with its default
    rich traceback formatter, which renders raw `exc_info` details.
  - Human mode intentionally omitted `format_exc_info`, so `_scrub_event_dict` never received a stringified
    exception payload to redact.
- Fix summary:
  - Added `_redacted_exception_formatter` in
    `src/zammad_pdf_archiver/observability/logger.py:40` that renders traceback text via
    `structlog.dev.plain_traceback(...)` and applies `scrub_secrets_in_text(...)`.
  - Wired this formatter into human `ConsoleRenderer` at
    `src/zammad_pdf_archiver/observability/logger.py:77`.
- Regression tests added:
  - `test/unit/test_logger_config.py::test_human_logging_redacts_secrets_in_exception_traceback`
  - `test/unit/test_logger_config.py::test_json_logging_redacts_secrets_in_exception_traceback`
- Status: Fixed
- Commit: n/a

### 9) P2 - Test plan documented wrong success tag (`pdf:done`)

- Symptom summary:
  - Documentation claimed workflow success transitions to `pdf:done`, but implementation and other docs
    use `pdf:signed`.
- Reproduction steps:
  1. Run `grep -RIn "pdf:done" docs`.
  2. Before fix: `docs/test-plan.md:120` reported `pdf:done`.
- Root cause analysis:
  - Stale wording in `docs/test-plan.md:120` diverged from state-machine constants and runtime behavior.
- Fix summary:
  - Updated `docs/test-plan.md:120` to `pdf:signed`.
- Regression test added:
  - Not applicable (documentation-only change). Alternative validation:
    `grep -RIn "pdf:done" docs` no longer reports operational docs mismatches.
- Status: Fixed
- Commit: n/a

## Final summary (current cycle)

- Open issues: 0 (P0: 0, P1: 0, P2: 0)
- Final verification:
  - `ruff check .` ✅
  - `pytest -q` ✅ (`176 passed`)
  - `mypy .` ✅

## Additional cycle (2026-02-07, deep pass)

### 10) P2 - Processing-tag cleanup failures were silently swallowed

- Symptom summary:
  - When the fallback cleanup `remove_tag(..., pdf:processing)` failed in the error path, no log signal
    was emitted, making stuck processing tags hard to diagnose.
- Reproduction steps:
  1. Run
     `pytest -q test/unit/test_process_ticket_cleanup.py::test_process_ticket_logs_processing_tag_cleanup_failures`.
  2. Before fix: test failed because only `process_ticket.error` was logged; no cleanup-failure event.
- Root cause analysis:
  - `src/zammad_pdf_archiver/app/jobs/process_ticket.py:530` had `except Exception: pass` around final
    processing-tag cleanup, swallowing actionable failures.
- Fix summary:
  - Replaced silent swallow with structured exception logging event
    `process_ticket.processing_tag_cleanup_failed` at
    `src/zammad_pdf_archiver/app/jobs/process_ticket.py:531`.
- Regression test added:
  - `test/unit/test_process_ticket_cleanup.py::test_process_ticket_logs_processing_tag_cleanup_failures`
- Status: Fixed
- Commit: n/a

### 11) P2 - Concurrency test was order-dependent due leaked global idempotency state

- Symptom summary:
  - `test_process_ticket_serializes_same_ticket_concurrent_runs` could fail when run after tests that
    reused delivery IDs (`d-1`, `d-2`) because global in-memory replay state was not reset.
- Reproduction steps:
  1. Run:
     `pytest -q test/unit/test_process_ticket_cleanup.py test/unit/test_process_ticket_inflight_idempotency.py test/unit/test_process_ticket_concurrency.py`.
  2. Before fix: concurrency test failed with both deliveries skipped as already seen.
- Root cause analysis:
  - `test/unit/test_process_ticket_concurrency.py` did not clear
    `process_ticket_module._DELIVERY_ID_SETS` / `_IN_FLIGHT_TICKETS` before running.
- Fix summary:
  - Added explicit global-state reset at test start in
    `test/unit/test_process_ticket_concurrency.py:26`.
- Regression test added:
  - Existing test became deterministic with reset:
    `test/unit/test_process_ticket_concurrency.py::test_process_ticket_serializes_same_ticket_concurrent_runs`
- Status: Fixed
- Commit: n/a

## Final summary (latest cycle)

- Open issues: 0 (P0: 0, P1: 0, P2: 0)
- Final verification:
  - `ruff check .` ✅
  - `pytest -q` ✅ (`177 passed`)
  - `mypy .` ✅

## Additional cycle (2026-02-07, deep pass #2)

### 12) P0 - Non-integer ticket IDs could be coerced to wrong ticket numbers

- Symptom summary:
  - Webhook payloads with boolean/float ticket IDs were coerced via `int(...)`
    (`True -> 1`, `False -> 0`, `1.5 -> 1`), risking processing/tagging the wrong ticket.
- Reproduction steps:
  1. Run:
     `pytest -q test/integration/test_ingest.py::test_ingest_does_not_schedule_background_for_boolean_ticket_id test/unit/test_process_ticket_ticket_id.py::test_extract_ticket_id_rejects_non_integer_values`.
  2. Before fix:
     - `/ingest` response returned `ticket_id: true` and scheduled background processing.
     - `_extract_ticket_id` returned numeric IDs for boolean/float inputs.
- Root cause analysis:
  - `src/zammad_pdf_archiver/app/jobs/process_ticket.py:82` used permissive `int(ticket_id)` conversion.
  - `src/zammad_pdf_archiver/app/routes/ingest.py:22` passed through raw payload ticket IDs and queued jobs
    for any non-`None` value.
- Fix summary:
  - Added strict coercion helper in `src/zammad_pdf_archiver/domain/ticket_id.py:6`:
    accepts only positive integer values (`int` or numeric strings), rejects booleans, floats, zero, and negatives.
  - Wired helper into:
    - `src/zammad_pdf_archiver/app/jobs/process_ticket.py:82`
    - `src/zammad_pdf_archiver/app/routes/ingest.py:22`
- Regression tests added:
  - `test/integration/test_ingest.py::test_ingest_does_not_schedule_background_for_boolean_ticket_id`
  - `test/unit/test_process_ticket_ticket_id.py::test_extract_ticket_id_accepts_integer_values`
  - `test/unit/test_process_ticket_ticket_id.py::test_extract_ticket_id_rejects_non_integer_values`
- Status: Fixed
- Commit: n/a

## Final summary (latest cycle #2)

- Open issues: 0 (P0: 0, P1: 0, P2: 0)
- Final verification:
  - `ruff check .` ✅
  - `pytest -q` ✅ (`193 passed`)
  - `mypy .` ✅
