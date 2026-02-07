# Triage Summary

## Iteration 1 (2026-02-07)

### What was checked

- Static scan: `grep -RIn TODO|FIXME|XXX|BUG`, broad exception patterns, http client/timeouts, path policy codepaths
- Lint: `python -m ruff check .`
- Tests: `python -m pytest -q`
- Typing: `python -m mypy .`

### Newly found issues

- P0: 0
- P1: 1
- P2: 0

### Remaining open issues

- P0: 0
- P1: 1
- P2: 0

## Iteration 2 (2026-02-07)

### What was checked

- Fix + regression: `test/unit/test_path_policy.py::test_sanitize_segment_non_ascii_fallback`
- Adversarial add: TSA unreachable → transient ticket state:
  `test/integration/test_process_ticket_signing.py::test_process_ticket_signing_with_unreachable_tsa_is_transient_and_keeps_trigger`
- Full suite rerun:
  - `python -m pytest -q`
  - `python -m ruff check .`
  - `python -m mypy .`

### Newly found issues

- P0: 0
- P1: 0
- P2: 0

### Remaining open issues

- P0: 0
- P1: 0
- P2: 0

### Confidence Checklist (final)

- ✅ ruff clean
- ✅ pytest clean
- ✅ mypy clean
- ✅ adversarial checks covered by tests/scripts (A–H), mapped:
  - A Webhook/HMAC/body: `test/integration/test_hmac_verify.py`, `test/integration/test_body_size_limit.py`,
    `test/integration/test_middleware_order.py`
  - B Idempotency: `test/unit/test_idempotency.py`, `test/integration/test_process_ticket_v01.py`
  - C Path safety: `test/unit/test_path_policy.py`, `test/unit/test_layout.py`
  - D Tag state machine: `test/unit/test_state_machine.py`, `test/integration/test_process_ticket_v01.py`
  - E PDF pipeline: `test/integration/test_pdf_rendering.py`, `test/unit/test_snapshot_builder.py`,
    `test/unit/test_templates_render.py`
  - F Signing/TSA: `test/unit/test_sign_pdf.py`, `test/unit/test_tsa_integration.py`,
    `test/integration/test_process_ticket_signing.py`
  - G Audit sidecar: `test/integration/test_audit_sidecar.py`, `test/unit/test_audit.py`
  - H Observability: `test/integration/test_metrics.py`, `test/unit/test_logging_redaction.py`,
    `test/unit/test_redaction.py`
- ✅ no TODO/FIXME left that indicates broken behavior (only non-code TODO remains in `CODE_OF_CONDUCT.md`)
- ✅ buglog contains 0 open items and a final summary
- ✅ docs match implementation for key behaviors

## Iteration 3 (2026-02-07)

### What was checked

- Global scan: middleware defaults, module-level app wiring, webhook auth fail-closed behavior
- Full suite:
  - `python -m ruff check .`
  - `python -m pytest -q`
  - `python -m mypy .`
- Targeted adversarial probe:
  - Default ASGI app (`server:app`) must fail closed without settings:
    `test/integration/test_hmac_verify.py::test_default_app_fails_closed_without_settings`

### Newly found issues

- P0: 1
- P1: 0
- P2: 0

### Remaining open issues

- P0: 0
- P1: 0
- P2: 0

### Confidence Checklist (final)

- ✅ ruff clean
- ✅ pytest clean
- ✅ mypy clean
- ✅ adversarial checks covered by tests/scripts (A–H)
- ✅ no TODO/FIXME left that indicates broken behavior (only non-code TODO remains in `CODE_OF_CONDUCT.md`)
- ✅ buglog contains 0 open items and a final summary
- ✅ docs match implementation for key behaviors

## Iteration 4 (2026-02-07)

### What was checked

- Global scan:
  - `grep -RIn TODO|FIXME|XXX|BUG`
  - broad exception patterns (`except Exception` / bare `except`)
  - storage/root containment usage (`resolve`, `ensure_within_root`)
  - http client usage + timeouts (`httpx.AsyncClient`, `_timeouts`)
- Full suite:
  - `python -m ruff check .`
  - `python -m pytest -q`
  - `python -m mypy .`
- Adversarial/edge-case checklist (A–H): revalidated via existing unit/integration tests (see Iteration 2/3).

### Newly found issues

- P0: 0
- P1: 0
- P2: 0

### Remaining open issues

- P0: 0
- P1: 0
- P2: 0

### Confidence Checklist (final)

- ✅ ruff clean
- ✅ pytest clean
- ✅ mypy clean
- ✅ adversarial checks covered by tests/scripts (A–H)
- ✅ no TODO/FIXME left that indicates broken behavior (only non-code TODO remains in `CODE_OF_CONDUCT.md`)
- ✅ buglog contains 0 open items and a final summary
- ✅ docs match implementation for key behaviors

## Iteration 5 (2026-02-07)

### What was checked

- Global scan: TODO/FIXME/XXX/BUG, exception swallowing, webhook docs/implementation alignment, idempotency
  data structure behavior under mostly-unique keys.
- Targeted probe + regression:
  - `test/unit/test_idempotency.py::test_add_triggers_eviction_of_expired_keys`
- Full suite:
  - `python -m ruff check .`
  - `python -m pytest -q`
  - `python -m mypy .`

### Newly found issues

- P0: 0
- P1: 1
- P2: 0

### Remaining open issues

- P0: 0
- P1: 0
- P2: 0

### Confidence Checklist (final)

- ✅ ruff clean
- ✅ pytest clean
- ✅ mypy clean
- ✅ adversarial checks covered by tests/scripts (A–H)
- ✅ no TODO/FIXME left that indicates broken behavior (only non-code TODO remains in `CODE_OF_CONDUCT.md`)
- ✅ buglog contains 0 open items and a final summary
- ✅ docs match implementation for key behaviors

## Iteration 6 (2026-02-07)

### What was checked

- Global scan:
  - `grep -RInE "TODO|FIXME|XXX|BUG" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=.mypy_cache --exclude-dir=.pytest_cache --exclude-dir=.ruff_cache`
  - `grep -RIn "except Exception\\|except:" src --exclude-dir=__pycache__`
  - `grep -RIn "httpx\\.AsyncClient\\|timeout" src/zammad_pdf_archiver/adapters --exclude-dir=__pycache__`
- Baseline suite:
  - `ruff check .`
  - `pytest -q`
  - `mypy .`
- Targeted adversarial probes/fixes:
  - Secret-leak repro probe in human logs via `logger.exception(...)`
  - Added/ran:
    - `test/unit/test_logger_config.py::test_human_logging_redacts_secrets_in_exception_traceback`
    - `test/unit/test_logger_config.py::test_json_logging_redacts_secrets_in_exception_traceback`
    - `test/integration/test_metrics.py::test_metrics_endpoint_is_not_exposed_when_disabled`
    - `test/unit/test_path_policy.py::test_unicode_homoglyph_traversal_segments_are_rejected`
  - Revalidated adversarial set:
    - `pytest -q test/integration/test_hmac_verify.py test/integration/test_body_size_limit.py test/unit/test_hmac_verify_middleware.py test/unit/test_idempotency.py test/unit/test_path_policy.py test/unit/test_layout.py test/unit/test_state_machine.py test/integration/test_process_ticket_v01.py test/integration/test_process_ticket_signing.py test/unit/test_snapshot_builder.py test/integration/test_pdf_rendering.py test/integration/test_audit_sidecar.py test/integration/test_metrics.py test/unit/test_logger_config.py test/unit/test_logging_redaction.py test/unit/test_redaction.py`
- Docs/behavior alignment check:
  - `grep -RIn "pdf:done" docs`

### Newly found issues

- P0: 1
- P1: 0
- P2: 1

### Remaining open issues

- P0: 0
- P1: 0
- P2: 0

### Confidence Checklist (final)

- ✅ ruff clean
- ✅ pytest clean
- ✅ mypy clean
- ✅ adversarial checks covered by tests/scripts (A–H), mapped:
  - A Webhook/HMAC/body: `test/integration/test_hmac_verify.py`, `test/integration/test_body_size_limit.py`, `test/unit/test_hmac_verify_middleware.py`
  - B Idempotency: `test/unit/test_idempotency.py`, `test/integration/test_process_ticket_v01.py`, `test/integration/test_e2e_smoke.py`
  - C Path safety: `test/unit/test_path_policy.py`, `test/unit/test_layout.py`
  - D Tag state machine: `test/unit/test_state_machine.py`, `test/integration/test_process_ticket_v01.py`, `test/integration/test_process_ticket_signing.py`
  - E PDF pipeline: `test/unit/test_snapshot_builder.py`, `test/integration/test_pdf_rendering.py`, `test/unit/test_templates_render.py`
  - F Signing/TSA: `test/unit/test_sign_pdf.py`, `test/unit/test_tsa_integration.py`, `test/integration/test_process_ticket_signing.py`
  - G Audit sidecar: `test/integration/test_audit_sidecar.py`, `test/unit/test_audit.py`
  - H Observability: `test/integration/test_metrics.py`, `test/unit/test_logger_config.py`, `test/unit/test_logging_redaction.py`, `test/unit/test_redaction.py`
- ✅ no TODO/FIXME left that indicates broken behavior (only non-code TODO remains in `CODE_OF_CONDUCT.md`)
- ✅ buglog contains 0 open items and a final summary
- ✅ docs match implementation for all key behaviors

## Iteration 7 (2026-02-07)

### What was checked

- Global scan:
  - `grep -RInE "TODO|FIXME|XXX|BUG" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=.mypy_cache --exclude-dir=.pytest_cache --exclude-dir=.ruff_cache`
  - `grep -RIn "except Exception\\|except:" src test --exclude-dir=__pycache__`
  - `grep -RIn "httpx.AsyncClient\\|Timeout\\|_timeouts" src/zammad_pdf_archiver/adapters --exclude-dir=__pycache__`
- Full suite:
  - `ruff check .`
  - `pytest -q`
  - `mypy .`
- Bug reproduction/fix loop:
  - Repro: `pytest -q test/unit/test_process_ticket_cleanup.py::test_process_ticket_logs_processing_tag_cleanup_failures`
  - Fix verification:
    - `pytest -q test/unit/test_process_ticket_cleanup.py`
    - `pytest -q test/unit/test_process_ticket_cleanup.py test/unit/test_process_ticket_inflight_idempotency.py test/unit/test_process_ticket_concurrency.py test/integration/test_process_ticket_v01.py test/integration/test_process_ticket_signing.py`
- Adversarial matrix rerun:
  - `pytest -q test/integration/test_hmac_verify.py test/integration/test_body_size_limit.py test/integration/test_middleware_order.py test/unit/test_hmac_verify_middleware.py test/unit/test_idempotency.py test/integration/test_e2e_smoke.py test/unit/test_path_policy.py test/unit/test_layout.py test/integration/test_storage_atomic.py test/unit/test_state_machine.py test/integration/test_process_ticket_v01.py test/integration/test_process_ticket_signing.py test/unit/test_snapshot_builder.py test/unit/test_templates_render.py test/integration/test_pdf_rendering.py test/integration/test_audit_sidecar.py test/integration/test_metrics.py test/unit/test_logger_config.py test/unit/test_logging_redaction.py test/unit/test_redaction.py test/unit/test_process_ticket_cleanup.py test/unit/test_process_ticket_concurrency.py`

### Newly found issues

- P0: 0
- P1: 0
- P2: 2

### Remaining open issues

- P0: 0
- P1: 0
- P2: 0

### Confidence Checklist (final)

- ✅ ruff clean
- ✅ pytest clean
- ✅ mypy clean
- ✅ adversarial checks covered by tests/scripts (A–H), mapped:
  - A Webhook/HMAC/body: `test/integration/test_hmac_verify.py`, `test/integration/test_body_size_limit.py`, `test/integration/test_middleware_order.py`, `test/unit/test_hmac_verify_middleware.py`
  - B Idempotency: `test/unit/test_idempotency.py`, `test/integration/test_e2e_smoke.py`, `test/unit/test_process_ticket_concurrency.py`
  - C Path safety: `test/unit/test_path_policy.py`, `test/unit/test_layout.py`, `test/integration/test_storage_atomic.py`
  - D Tag state machine: `test/unit/test_state_machine.py`, `test/integration/test_process_ticket_v01.py`, `test/integration/test_process_ticket_signing.py`, `test/unit/test_process_ticket_cleanup.py`
  - E PDF pipeline: `test/unit/test_snapshot_builder.py`, `test/unit/test_templates_render.py`, `test/integration/test_pdf_rendering.py`
  - F Signing/TSA: `test/integration/test_process_ticket_signing.py`, `test/unit/test_sign_pdf.py`, `test/unit/test_tsa_integration.py`
  - G Audit sidecar: `test/integration/test_audit_sidecar.py`, `test/unit/test_audit.py`
  - H Observability: `test/integration/test_metrics.py`, `test/unit/test_logger_config.py`, `test/unit/test_logging_redaction.py`, `test/unit/test_redaction.py`
- ✅ no TODO/FIXME left that indicates broken behavior (only non-code TODO remains in `CODE_OF_CONDUCT.md`)
- ✅ buglog contains 0 open items and a final summary
- ✅ docs match implementation for all key behaviors

## Iteration 8 (2026-02-07)

### What was checked

- Global scan:
  - `grep -RInE "TODO|FIXME|XXX|BUG" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=.mypy_cache --exclude-dir=.pytest_cache --exclude-dir=.ruff_cache`
  - `grep -RIn "except Exception\\|except:" src test --exclude-dir=__pycache__`
  - `grep -RIn "datetime.now\\|time.time\\|utcnow" src --exclude-dir=__pycache__`
- Full suite (required):
  - `ruff check .`
  - `pytest -q`
  - `mypy .`
- Reproduction and fix loop:
  - Failing repro:
    - `pytest -q test/integration/test_ingest.py::test_ingest_does_not_schedule_background_for_boolean_ticket_id test/unit/test_process_ticket_ticket_id.py::test_extract_ticket_id_rejects_non_integer_values`
  - Focused verification:
    - `pytest -q test/integration/test_ingest.py test/unit/test_process_ticket_ticket_id.py test/unit/test_process_ticket_cleanup.py test/unit/test_process_ticket_concurrency.py test/unit/test_process_ticket_inflight_idempotency.py test/integration/test_process_ticket_v01.py test/integration/test_process_ticket_signing.py test/integration/test_hmac_verify.py`
- Adversarial matrix rerun (A–H):
  - `pytest -q test/integration/test_hmac_verify.py test/integration/test_body_size_limit.py test/integration/test_middleware_order.py test/integration/test_ingest.py test/unit/test_hmac_verify_middleware.py test/unit/test_idempotency.py test/integration/test_e2e_smoke.py test/unit/test_process_ticket_ticket_id.py test/unit/test_path_policy.py test/unit/test_layout.py test/integration/test_storage_atomic.py test/unit/test_state_machine.py test/unit/test_process_ticket_cleanup.py test/unit/test_process_ticket_concurrency.py test/unit/test_process_ticket_inflight_idempotency.py test/integration/test_process_ticket_v01.py test/integration/test_process_ticket_signing.py test/unit/test_snapshot_builder.py test/unit/test_templates_render.py test/integration/test_pdf_rendering.py test/integration/test_audit_sidecar.py test/integration/test_metrics.py test/unit/test_logger_config.py test/unit/test_logging_redaction.py test/unit/test_redaction.py`

### Newly found issues

- P0: 1
- P1: 0
- P2: 0

### Remaining open issues

- P0: 0
- P1: 0
- P2: 0

### Confidence Checklist (final)

- ✅ ruff clean
- ✅ pytest clean
- ✅ mypy clean
- ✅ adversarial checks covered by tests/scripts (A–H), mapped:
  - A Webhook/HMAC/body: `test/integration/test_hmac_verify.py`, `test/integration/test_body_size_limit.py`, `test/integration/test_middleware_order.py`, `test/unit/test_hmac_verify_middleware.py`, `test/integration/test_ingest.py`
  - B Idempotency: `test/unit/test_idempotency.py`, `test/integration/test_e2e_smoke.py`, `test/unit/test_process_ticket_concurrency.py`, `test/unit/test_process_ticket_inflight_idempotency.py`
  - C Path safety: `test/unit/test_path_policy.py`, `test/unit/test_layout.py`, `test/integration/test_storage_atomic.py`
  - D Tag state machine: `test/unit/test_state_machine.py`, `test/unit/test_process_ticket_cleanup.py`, `test/integration/test_process_ticket_v01.py`, `test/integration/test_process_ticket_signing.py`
  - E PDF pipeline: `test/unit/test_snapshot_builder.py`, `test/unit/test_templates_render.py`, `test/integration/test_pdf_rendering.py`
  - F Signing/TSA: `test/unit/test_sign_pdf.py`, `test/unit/test_tsa_integration.py`, `test/integration/test_process_ticket_signing.py`
  - G Audit sidecar: `test/integration/test_audit_sidecar.py`, `test/unit/test_audit.py`
  - H Observability: `test/integration/test_metrics.py`, `test/unit/test_logger_config.py`, `test/unit/test_logging_redaction.py`, `test/unit/test_redaction.py`
- ✅ no TODO/FIXME left that indicates broken behavior (only non-code TODO remains in `CODE_OF_CONDUCT.md`)
- ✅ buglog contains 0 open items and a final summary
- ✅ docs match implementation for all key behaviors
