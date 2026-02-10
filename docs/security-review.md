# Security Review – Zammad PDF Archiver

**Reviewer perspective:** Security engineer  
**Scope:** Application and deployment security; trust boundaries; data handling  
**Reference:** [09-security.md](09-security.md) (threat model), [PRD](PRD.md), codebase as of review date  

---

## 1. Executive summary

The service implements solid baseline controls: HMAC webhook authentication (fail-closed), path confinement under storage root, symlink rejection, secret redaction in logs and ticket notes, body size and rate limiting, and transport validation for upstream URLs. No critical code-level vulnerabilities were identified. Remaining issues are mostly **hardening**, **operational clarity**, and **residual risk documentation**. The highest-priority improvements are: **authenticating or restricting `/metrics`**, **strengthening webhook crypto (SHA-256 option)**, **fixing world-writable file creation**, and **documenting rate-limit and proxy behaviour**.

### Implementation status (as of review remediation)

| ID | Status | Notes |
|----|--------|--------|
| H1 | Done | Optional Bearer auth for `/metrics` via `METRICS_BEARER_TOKEN` |
| H2 | Done | Written files use mode `0o640` |
| H3 | Done | HMAC accepts both SHA-1 and SHA-256 in `X-Hub-Signature` |
| M1 | Done | Rate limit key from header via `RATE_LIMIT_CLIENT_KEY_HEADER` |
| M2 | Done | Ingest body validated with `IngestBody` schema (require ticket id) |
| M3 | Done | Redaction documented as best-effort in 09-security.md |
| M4 | Done | TOCTOU on symlink check documented in 09-security.md and 07-storage.md |
| L1 | Open | Lockfile / fail on HIGH in pip-audit not yet done |
| L2 | Done | Optional omit version from `/healthz` via `HEALTHZ_OMIT_VERSION` |
| L3 | Done | Delivery ID in-memory only documented in 09-security.md |
| L4 | Done | TEMPLATES_ROOT operational control documented in 09-security.md |

---

## 2. Problems (described clearly)

### 2.1 High priority

| ID | Problem | Location / cause | Impact |
|----|---------|------------------|--------|
| **H1** | **`/metrics` is unauthenticated** | `app/routes/metrics.py` – no auth; middleware does not require HMAC or any token for `/metrics`. When `observability.metrics_enabled=true`, anyone who can reach the endpoint can read Prometheus metrics. | Information disclosure (request counts, latency, success/failure rates). Enables reconnaissance and traffic analysis. Docs state that access control is deployment-layer (proxy/firewall); app does not enforce it. |
| **H2** | **Newly written files use mode `0o666`** | `adapters/storage/fs_storage.py`: `os.open(..., 0o666)` for PDF and sidecar files. | On multi-user systems or shared mounts, any local user can read or modify archived PDFs and sidecar JSON. Undermines confidentiality and integrity of archives. |
| **H3** | **Webhook signature uses HMAC-SHA1 only** | `app/middleware/hmac_verify.py`: `_EXPECTED_ALGORITHM = "sha1"`; no support for SHA-256. | SHA-1 is weak for signatures; NIST deprecates it for new applications. For HMAC it is still considered acceptable, but offering only SHA-1 limits future compliance and does not align with “prefer strong crypto” guidance. |

### 2.2 Medium priority

| ID | Problem | Location / cause | Impact |
|----|---------|------------------|--------|
| **M1** | **Rate limit key is direct client IP only** | `app/middleware/rate_limit.py`: `_client_key(scope)` uses `scope["client"][0]` (direct peer). | Behind a reverse proxy, all traffic appears from the proxy’s IP. Rate limiting is effectively global or per-proxy, not per end-client. Abuse from many clients behind one proxy is not throttled per client. |
| **M2** | **No validation that ingest body is a Zammad-like payload** | `app/routes/ingest.py`: `IngestPayload = Annotated[dict[str, Any], Body(...)]` – any JSON object is accepted. | Reliance is entirely on HMAC. If HMAC were ever bypassed or misconfigured, arbitrary JSON (including very deep or large structures within body limits) would be passed to the background job. Schema validation would limit blast radius and catch misdirected traffic. |
| **M3** | **Redaction is best-effort and key-based** | `config/redact.py`: Sensitive keys are a fixed set + fragments (e.g. `password`, `token`). Free-text scrub uses regex for known patterns. | Custom secret keys (e.g. `custom_api_key`, `third_party_secret`) may not be redacted. New credential formats in exception messages could leak. Risk is limited to logs and ticket notes (not returned to arbitrary clients). |
| **M4** | **TOCTOU on symlink check** | `adapters/storage/fs_storage.py`: `_reject_symlinks_under_root` checks path components, then write happens later. | A symlink could be created between the check and the write. Documented in code. Mitigation requires OS/filesystem controls or kernel-level solutions; app cannot fully remove the risk. |

### 2.3 Lower priority / documentation

| ID | Problem | Location / cause | Impact |
|----|---------|------------------|--------|
| **L1** | **Dependencies use version ranges** | `pyproject.toml`: e.g. `fastapi>=0.110`, `cryptography>=42,<45`. | New minor/patch versions can be pulled; could introduce regressions or new CVEs. Pip-audit in CI only fails on CRITICAL; HIGH may remain. Pinning exact versions (e.g. in lockfile) and failing on HIGH would tighten supply-chain security. |
| **L2** | **`/healthz` exposes version and service name** | `app/routes/healthz.py`: returns `version`, `service`. | Low-severity information disclosure. Useful for ops; can help attackers in fingerprinting. |
| **L3** | **Delivery ID dedupe is in-memory only** | `app/jobs/process_ticket.py`: in-memory TTL set keyed by `X-Zammad-Delivery`. | Restart clears state; duplicate deliveries can be processed twice. Documented; acceptable for current design but should remain explicit in security docs. |
| **L4** | **`TEMPLATES_ROOT` from environment** | `adapters/pdf/template_engine.py`: `os.environ.get("TEMPLATES_ROOT")` – loader uses this path. | If process env is controlled by an attacker, they could point to a malicious template directory. Typically env is set by the process owner (ops); document as operational control. |

---

## 3. What is already in good shape

- **HMAC verification:** Constant-time compare (`hmac.compare_digest`); fail-closed when no secret; body replayed only after verification.
- **Path safety:** Segments validated and sanitized; root confinement; symlink rejection; `O_NOFOLLOW` on file open.
- **Request limits:** Body size enforced with streaming check (not only `Content-Length`); token-bucket rate limiting on `/ingest` (and optionally `/metrics`).
- **Transport:** Startup validation rejects plain HTTP, disabled TLS verification, and loopback/link-local upstreams unless explicitly overridden.
- **Secrets:** No eval/exec/pickle; YAML loaded with `safe_load`; Jinja autoescape enabled; redaction applied to settings and exception text.
- **CI:** `pip-audit` runs; policy fails on CRITICAL and unknown severity.

---

## 4. Detailed remediation plan (prioritized)

### Phase 1 – High priority (do first)

| Step | Action | Owner | Verification |
|------|--------|-------|--------------|
| 1.1 | **H1 – Restrict or protect `/metrics`** | Dev | (a) Add config option (e.g. `observability.metrics_require_auth`) and, when enabled, require a shared secret (e.g. `Authorization: Bearer <token>` or query param) that is compared in constant time; **or** (b) Document that `/metrics` must be reachable only from a trusted network (e.g. scrape from monitoring VLAN) and add a deployment checklist item. Prefer (a) if metrics may be exposed on a shared network. | Test: With auth enabled, unauthenticated request to `GET /metrics` returns 401/403. |
| 1.2 | **H2 – Restrict file creation mode** | Dev | In `fs_storage.py`, do not use `0o666`. Use `0o640` (owner rw, group r, others none) or make mode configurable (e.g. `storage.file_mode`) with default `0o640`. Ensure the process umask does not widen permissions. | Test: Newly written file has mode 640 (or configured value); no world read/write. |
| 1.3 | **H3 – Support HMAC-SHA-256** | Dev | In `hmac_verify.py`: (a) Accept both `sha1=` and `sha256=` in `X-Hub-Signature` (or a single configurable algorithm, default `sha256`). (b) Compute HMAC with the same algorithm as in the header. (c) Document that Zammad (or the webhook sender) must send the chosen algorithm. (d) Prefer SHA-256 for new deployments; keep SHA-1 as legacy option if needed. | Test: Request with `X-Hub-Signature: sha256=<hex>` and correct HMAC-SHA256 passes; invalid fails with 403. |

### Phase 2 – Medium priority

| Step | Action | Owner | Verification |
|------|--------|-------|--------------|
| 2.1 | **M1 – Rate limit key when behind proxy** | Dev | Add config (e.g. `hardening.rate_limit.client_key_from_header`) and, when set, derive the rate-limit key from a header (e.g. `X-Forwarded-For` or `X-Real-IP`) instead of `scope["client"]`. Document that the reverse proxy must set this header from a trusted source and that the app trusts it. Optionally support a list of proxy IPs to skip when parsing the header. | Test: With header set, rate limit is applied per forwarded client IP. |
| 2.2 | **M2 – Ingest payload schema validation** | Dev | Define a minimal Pydantic model for the webhook body (e.g. require `ticket` or `ticket_id`; allow additional fields). Use it in the ingest route so that invalid structure returns 422 before scheduling background work. Keep HMAC as the authentication mechanism; schema is a sanity check and blast-radius reduction. | Test: Valid payload 202; payload missing ticket id 422; oversized or deeply nested payload rejected by body limit or schema. |
| 2.3 | **M3 – Redaction coverage** | Dev | (a) Add a small set of additional key fragments or explicit keys if new secret types appear in config. (b) Document in [09-security.md](09-security.md) that redaction is best-effort and that operators must avoid logging full config or raw exceptions in production. (c) Optionally add a test that asserts all keys in `Settings` that are `SecretStr` or match a pattern are in the redaction allowlist. | Test: New SecretStr config keys are redacted (or test/doc updated). |
| 2.4 | **M4 – TOCTOU symlink** | Docs | In [09-security.md](09-security.md) and [07-storage.md](07-storage.md), state explicitly that symlink checks are best-effort and that TOCTOU is a residual risk; recommend dedicated mount or filesystem controls for high-assurance environments. | No code change; doc update. |

### Phase 3 – Lower priority and hardening

| Step | Action | Owner | Verification |
|------|--------|-------|--------------|
| 3.1 | **L1 – Dependency pinning** | Dev | Introduce a lockfile (e.g. `pip compile` or `uv lock`) and use it in CI and in the Docker image. Consider failing `pip-audit` on HIGH as well as CRITICAL after reviewing. | CI and image build use locked versions; policy documented. |
| 3.2 | **L2 – Healthz information** | Dev/Ops | Optionally add a config flag to omit `version` from `/healthz` in sensitive deployments, or document that healthz may be exposed only to the orchestrator. | Config or doc. |
| 3.3 | **L3 – Delivery ID** | Docs | Keep [09-security.md](09-security.md) and PRD explicit that in-memory dedupe is a known limitation and that duplicate processing is possible after restart. | Doc. |
| 3.4 | **L4 – TEMPLATES_ROOT** | Docs | In operations or security doc, state that `TEMPLATES_ROOT` must be set only by the process owner and must point to a controlled directory. | Doc. |

### Phase 4 – Ongoing

| Step | Action | Owner |
|------|--------|-------|
| 4.1 | Re-run this review after major features or at least annually. | Security / Dev |
| 4.2 | Keep [09-security.md](09-security.md) and this document in sync when new threats or mitigations are added. | Dev |

---

## 5. Priority order summary

1. **H1** – Metrics access control (auth or strict network restriction).  
2. **H2** – File mode (0o640 or configurable).  
3. **H3** – HMAC-SHA-256 support.  
4. **M1** – Rate limit key from proxy header (optional, configurable).  
5. **M2** – Ingest payload schema validation.  
6. **M3** – Redaction coverage and documentation.  
7. **M4** – Document TOCTOU residual risk.  
8. **L1–L4** – Lockfile, healthz, delivery-ID and TEMPLATES_ROOT documentation.

---

## 6. References

- [09 - Security](09-security.md)
- [PRD](PRD.md) §6 Non-functional requirements, §7 Success criteria
- [NFR implementation order](NFR-implementation-order.md)
- [Config reference](config-reference.md)
