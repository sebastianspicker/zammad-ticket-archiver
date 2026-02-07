# ADR 0003: PDF signing + RFC3161 timestamping

## Status

Accepted (implemented)

## Context

“Archive-grade” PDFs typically need:
- integrity protection (detect tampering)
- a trustworthy notion of time (prove the content existed at/before a certain time)

A plain PDF signature can embed a “signing time”, but that value is asserted by the signer and can be
incorrect. A “trustworthy timestamp” requires an independent third party: an RFC3161 Time Stamping Authority
(TSA).

## Decision

- Sign PDFs using **PAdES** via **pyHanko**.
- Use a local **PKCS#12/PFX** bundle for signing material.
- Optionally embed an **RFC3161** timestamp token from a configured TSA (PAdES-T style):
  - enabled via `TSA_ENABLED=true` / YAML `signing.timestamp.enabled=true`
  - configured via `TSA_URL` / YAML `signing.timestamp.rfc3161.tsa_url`
  - optional HTTP basic auth via `TSA_USER` / `TSA_PASS`

Provide operational verification scripts:
- `scripts/ops/verify-pdf.sh` (wrapper)
- `scripts/ops/verify-pdf.py` (Python validation fallback)

## Consequences

- Signing material (PFX and password) becomes a high-value secret:
  - store it in a secret store
  - rotate before certificate expiry
- Timestamping adds an external dependency (TSA availability, TLS trust, credentials).
  - TSA network failures are treated as transient and can be retried.
- Verification of signed/timestamped PDFs is part of operations:
  - maintain trust anchors (root/intermediate CAs, TSA CA bundles)
  - restrict `/metrics` when enabled to avoid leaking operational details

See also:
- [`../06-signing-and-timestamp.md`](../06-signing-and-timestamp.md) (config + explanation)
- [`../08-operations.md`](../08-operations.md) (verification commands + troubleshooting)
