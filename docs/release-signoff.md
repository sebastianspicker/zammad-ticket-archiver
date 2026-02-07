# Release Readiness Sign-off

Date: 2026-02-07  
Scope: `zammad-pdf-archiver` implementation vs README/docs promises for end-to-end pipeline:
`/ingest -> snapshot -> PDF -> optional sign+RFC3161 -> storage -> ticket note/tags -> audit sidecar`

## Decision

Conditional GO.

All in-repo P0/P1 functional gaps identified in this audit cycle are closed and validated.  
Release is approved pending completion of external deployment checks listed in
`docs/release-checklist.md` section `2.1) Deployment safety checks`.

## What Was Verified

- Promise coverage documented in `docs/promise-matrix.md` (statuses updated).
- Gap closure tracked in `docs/gap-plan.md`:
  - P0: TSA `trust_env` hardening behavior fixed and tested.
  - P1: fail-fast signing config now requires `signing.pfx_path` when signing is enabled.
  - P2 (feasible): deployment gates for `/metrics` network protection and CIFS runtime safety added.
- End-to-end behavior checks (dry-run/integration):
  - ingest scheduling and request/delivery ID propagation
  - idempotency with `X-Zammad-Delivery`
  - tag transitions and transient/permanent error behavior
  - archive path parsing and normalization
  - atomic write behavior for PDF + audit sidecar
  - PFX signing path and TSA transient/permanent classification
  - required fields in internal note and audit sidecar
  - metrics endpoint config gating

## Evidence

- Focused critical-path suite:
  - `pytest -q test/integration/test_e2e_smoke.py test/integration/test_ingest.py test/integration/test_process_ticket_v01.py test/integration/test_process_ticket_signing.py test/integration/test_storage_atomic.py test/integration/test_metrics.py`
  - Result: `20 passed`
- Full quality gate:
  - `ruff check`
  - `pytest -q`
  - Result: `156 passed`, `0 failed`

## Residual Risks (External to repo code)

- CIFS/SMB durability semantics depend on mount/server behavior and must be validated in target environment.
- `/metrics` access control is deployment-layer responsibility (proxy/firewall/ACL), not app-layer auth.
- Signing/TSA trust depends on operational trust anchors, cert lifecycle, and network policy.

## Release Gating Checklist

Before production release, confirm:

1. `docs/release-checklist.md` section `2.1) Deployment safety checks` completed with evidence.
2. Real environment smoke run proves expected outputs:
   - archived PDF file
   - `...pdf.json` audit sidecar
   - ticket internal note with required fields
   - correct final tags (`pdf:signed` on success; `pdf:error` behavior on failures).
