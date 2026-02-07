# Gap Plan (prioritized)

Last updated: 2026-02-07

## P0 (end-to-end/security/data-integrity blockers)

- [x] **Honor `hardening.transport.trust_env` for TSA HTTP traffic**  
  Impact: In proxy-restricted environments, timestamping can fail even when config explicitly enables proxy/env usage, breaking signed+timestamped end-to-end flow.  
  Promise source: `docs/09-security.md` (egress hardening applies to HTTP clients).  
  Code: `src/zammad_pdf_archiver/adapters/signing/tsa_rfc3161.py`  
  Tests: `test/unit/test_tsa_rfc3161.py::test_tsa_http_client_respects_transport_trust_env`

## P1 (major promised features)

- [x] **Fail-fast config validation now requires PFX path when `signing.enabled=true`**  
  Impact addressed: prevents cert/key-only misconfiguration from reaching runtime signing path and failing per ticket.  
  Promise source: `docs/06-signing-and-timestamp.md` (“current implementation requires PKCS#12/PFX”).  
  Code: `src/zammad_pdf_archiver/config/settings.py`  
  Tests: `test/unit/test_config.py::test_load_settings_rejects_signing_enabled_without_pfx_path`, `test/unit/test_config.py::test_load_settings_accepts_signing_enabled_with_pfx_path`

## P2 (hardening/doc/runtime-environment alignment)

- [x] **CIFS durability semantics captured as release-time operational verification**  
  Impact: remains environment-dependent, but now explicitly gated in release steps.  
  Promise source: `docs/07-storage.md`  
  Documentation update: `docs/release-checklist.md` section `2.1) Deployment safety checks`.

- [x] **`/metrics` network protection captured as explicit deployment gate**  
  Impact: app remains config-gated only; network restriction is now mandatory in release checklist.  
  Promise source: `docs/08-operations.md`, `docs/09-security.md`  
  Documentation update: `docs/release-checklist.md` section `2.1) Deployment safety checks`.

## Iteration order

1. Ship P0 TSA `trust_env` fix with tests.
2. Implement P1 signing fail-fast validation with tests.
3. Capture P2 deployment verifications in release checklist/runbook.
