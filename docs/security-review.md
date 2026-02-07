# Security Review - zammad-pdf-archiver

Date: 2026-02-07

Scope: focused production review of webhook ingress, secret handling, filesystem safety, outbound transport, and runtime privilege.

## Summary

No new P0 issues were found. Existing controls for HMAC verification, body-size limiting, rate limiting, test-network isolation, and non-root container runtime are in place.

This pass closed remaining P1 hardening gaps around outbound URL safety, operator-visible error redaction, and stricter write-path invariants.

## Findings and actions

1. SSRF guardrails for configured upstreams
- Finding: upstream URLs were operator-controlled but did not block loopback/link-local targets by default.
- Action: added validation that rejects loopback/link-local upstream hosts unless `hardening.transport.allow_local_upstreams=true`.
- Coverage: new config tests for reject/allow behavior.

2. Secret leakage through error surfaces
- Finding: log redaction existed, but concise exception text used in internal ticket error notes was not scrubbed.
- Action: `_concise_exc_message()` now runs secret-pattern scrubbing before emitting operator-facing error text.
- Coverage: new unit test verifies token redaction.

3. Filesystem safety invariant
- Finding: storage helpers accepted optional `storage_root`, allowing future call sites to omit confinement accidentally.
- Action: `write_bytes()` and `write_atomic_bytes()` now require `storage_root` explicitly.
- Coverage: storage tests updated to pass explicit root and continue enforcing traversal/symlink rejection.

4. Docker runtime hardening
- Finding: runtime command used shell-form `CMD`.
- Action: switched to direct entrypoint (`zammad-pdf-archiver`) and kept non-root `USER app:app`; image copies now use `--chown=app:app`.

## Notes

- Request size limit and rate limit remain enabled by default.
- HMAC validation continues to use constant-time `compare_digest`.
- CIFS tamper resistance still depends on external storage ACL/snapshot policy; app-level controls provide path confinement and integrity metadata, not WORM guarantees.
