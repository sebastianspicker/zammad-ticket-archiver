# NFR Implementation Order – Engineering Review

This document reviews the PRD non-functional requirements (NFRs) against the codebase and defines a **recommended implementation order** for engineers (e.g. when building a similar service from scratch or when prioritizing hardening work).

---

## 1. Current implementation status

| NFR | Requirement | Status | Location / notes |
|-----|-------------|--------|------------------|
| **NFR1** | HMAC webhook verification; fail closed when secret configured | ✅ Done | `app/middleware/hmac_verify.py`: `compare_digest`, accepts `sha1=` and `sha256=` in `X-Hub-Signature`; 503 when no secret and not allow_unsigned, 403 on invalid/missing signature |
| **NFR2** | Body size limit + token-bucket rate limiting on ingest | ✅ Done | `app/middleware/body_size_limit.py`, `app/middleware/rate_limit.py`; applied to `/ingest` |
| **NFR3** | Path validation and confinement under `storage.root`; no traversal | ✅ Done | `domain/path_policy.py` (`ensure_within_root`, `sanitize_segment`, `validate_segments`); `adapters/storage/fs_storage.py` (`_reject_symlinks_under_root`, `O_NOFOLLOW` on write) |
| **NFR4** | Scrub secrets in logs and ticket notes | ✅ Done | `config/redact.py` (`scrub_secrets_in_text`, `redact_settings_dict`); `observability/logger.py` (redacted processor/exception formatter); `process_ticket.py` uses scrub for error message |
| **NFR5** | Disallow plaintext HTTP, disabled TLS, loopback/link-local by default; explicit overrides | ✅ Done | `config/validate.py`: `validate_settings()` checks base_url scheme, `verify_tls`, `allow_local_upstreams`; TSA URL validated in same way |
| **NFR6** | Config: env + optional YAML; precedence env > YAML > defaults | ✅ Done | `config/load.py`: dotenv, YAML via `CONFIG_PATH` or `config/config.yaml`, `Settings(**yaml_data)`. Precedence env > YAML > defaults enforced via `settings_customise_sources` (env, flat env, init). Verified by `test/unit/test_config.py::test_env_overrides_yaml`. |
| **NFR7** | Single process; Docker and systemd deployment | ✅ Done | `runtime.py` (uvicorn); `Dockerfile`, `docker-compose.yml`; `infra/systemd/` |
| **NFR8** | Document setup, path policy, signing, storage, operations, security | ✅ Done | Key docs: 00–09, api, config-reference, faq (see `test_nfr8_docs.py`) |
| **NFR9** | Python 3.12+; declared dependencies | ✅ Done | `pyproject.toml` |
| **NFR10** | No mandatory external queue; in-memory dedupe/guard | ✅ By design | Constraint, not an implementation task |

**Conclusion:** All NFRs are implemented. No missing NFR work is required for the current PRD. The order below is for **greenfield implementation** or **hardening/refactor priorities**.

### NFR verification tests

Each NFR is covered by dedicated tests in **`test/nfr/`**:

| NFR | Test module | What is verified |
|-----|-------------|------------------|
| NFR1 | `test_nfr1_hmac.py` | Invalid signature → 403; no secret → 503 (fail closed); valid signature → 202 |
| NFR2 | `test_nfr2_body_rate_limit.py` | Body over limit → 413; rate limit exceeded → 429 |
| NFR3 | `test_nfr3_path_policy.py` | Path outside root rejected; symlink traversal rejected |
| NFR4 | `test_nfr4_redaction.py` | Settings dict redaction; free-text scrub of token-like patterns |
| NFR5 | `test_nfr5_transport.py` | `validate_settings` rejects `http://` base_url by default |
| NFR6 | `test_nfr6_config.py` | Load from YAML; `validate_settings` called on load |
| NFR7 | `test_nfr7_deploy.py` | App creates with settings; Dockerfile exists |
| NFR8 | `test_nfr8_docs.py` | Key doc files exist (00–09, api, config-reference, faq) |
| NFR9 | `test_nfr9_python.py` | `pyproject.toml` requires Python 3.12+ |
| NFR10 | `test_nfr10_design.py` | No Redis/Celery/RabbitMQ in dependencies |

Run all NFR verification tests: `pytest test/nfr/ -v`.

---

## 2. Priority by importance and implementation per NFR

Priority: **P0 = critical (production not safe without)**, **P1 = important (security/compliance)**, **P2 = operational**.

For each NFR: what is concretely needed to implement it.

### P0 – Critical (secure operation depends on these)

| NFR | Importance | What is needed to implement |
|-----|------------|-----------------------------|
| **NFR1** (HMAC) | Highest priority. Without HMAC any client can trigger webhooks and process tickets. | **Implementation:** 1) Read secret from config (`webhook_hmac_secret` or legacy `webhook_shared_secret`). 2) Middleware for `POST /ingest` only: read body fully, compute HMAC with secret (SHA-1 or SHA-256 per header), parse header `X-Hub-Signature: sha1=<hex>` or `sha256=<hex>` and compare with `hmac.compare_digest`. 3) When no secret: either 503 (fail closed) or allow when `allow_unsigned=true`. 4) Replay body to app after verification (read once, feed again to downstream handlers). **Dependencies:** Config (NFR6) for secret and `allow_unsigned`. |
| **NFR3** (Path confinement) | Critical. Path traversal or writes outside `storage.root` can overwrite arbitrary files or leak secrets. | **Implementation:** 1) **Segment validation:** validate inputs (e.g. `archive_path`, username) as segments: no `.`/`..`, no `/`/`\`/NUL, max length (e.g. 64), max depth (e.g. 10). 2) **Sanitization:** deterministic (e.g. NFKD, allowed chars `[A-Za-z0-9._-]`, rest replaced by `_`). 3) **Root containment:** resolve final path with `Path.resolve()` and ensure it is under `storage.root` (`is_relative_to` or equivalent). 4) **Symlinks:** before writing, check each component under root for symlinks; reject if any. 5) **Write:** use `O_NOFOLLOW` when opening the target file. **Dependencies:** Config for `storage.root` and optional `allow_prefixes`. |
| **NFR6** (Config) | Foundation for all other NFRs. Without central config no secure secrets, limits, or storage root. | **Implementation:** 1) Load `.env` (e.g. `python-dotenv`, optional). 2) Load YAML via `CONFIG_PATH` or default `config/config.yaml`. 3) Pydantic settings model for all sections (server, zammad, workflow, storage, pdf, signing, observability, hardening). 4) Define clear precedence (e.g. env overrides YAML) and implement: either explicit merge (env first, then YAML for missing) or document that YAML takes precedence. 5) Call validation at startup (e.g. `validate_settings()`) and fail cleanly on errors. **Dependencies:** none. |
| **NFR4** (Secret scrubbing) | P0 for production. Leaks in logs or ticket notes are compliance and security incidents. | **Implementation:** 1) **Structured data:** recursive redaction of keys that hold secrets (e.g. `api_token`, `webhook_hmac_secret`, `pfx_password`, `tsa_pass`); replace values with placeholder. 2) **Free text (logs/exceptions):** regex or filter for token-/password-like patterns in exception messages and log strings; call before writing to log or ticket note. 3) Logger: attach processor/formatter that runs all event dicts and exception strings through the redaction function. **Dependencies:** none (can be added early). |
| **NFR2** (Body + rate limit) | P0 for publicly reachable endpoint. Prevents DoS from large bodies or request flood. | **Implementation:** 1) **Body limit:** middleware for `POST /ingest`: check `Content-Length` (if present, against `max_bytes`); track byte count while reading body, abort and return 413 on exceed. Do not pass body to handler if aborted. 2) **Rate limit:** token bucket (or sliding window) per time unit; return 429 on exceed. Configurable: e.g. `rps`, `burst`. Apply only to `/ingest` (optionally exclude `/metrics`). **Dependencies:** Config (NFR6) for `max_bytes`, `rps`, `burst`. |

### P1 – Important (security and compliance)

| NFR | Importance | What is needed to implement |
|-----|------------|-----------------------------|
| **NFR5** (Transport safety) | Prevents accidental HTTP, disabled TLS, or upstreams to localhost/link-local. | **Implementation:** 1) At startup (after config load) validate all outbound URLs: Zammad `base_url`, and TSA URL if used. 2) If URL is `http://` and not `allow_insecure_http`: validation error. 3) If `verify_tls=False` and not `allow_insecure_tls`: validation error. 4) Parse URL host (hostname or IP); if loopback/link-local/unspecified and not `allow_local_upstreams`: validation error. 5) HTTP client (e.g. httpx) with `verify=verify_tls`, `trust_env` only when explicitly enabled. **Dependencies:** Config (NFR6) for URLs and hardening flags. |
| **NFR9** (Python + dependencies) | Reproducible, secure build; clear runtime environment. | **Implementation:** 1) `pyproject.toml` (or `requirements.txt`): `requires-python>=3.12`, pinned or ranged versions for FastAPI, uvicorn, httpx, pydantic, weasyprint, pyhanko, cryptography, etc. 2) No unnecessary privileges; optional `pip-audit` / security check in CI. **Dependencies:** none. |

### P2 – Operationally required (run and clarity)

| NFR | Importance | What is needed to implement |
|-----|------------|-----------------------------|
| **NFR7** (Single process + deploy) | Without deployment artefacts the service cannot be run reliably. | **Implementation:** 1) Entry point: e.g. `uvicorn.run(app, host=..., port=...)` with host/port from config. 2) `Dockerfile`: base image (e.g. Python 3.12), install package, USER non-root, CMD/ENTRYPOINT for uvicorn. 3) Optional `docker-compose.yml` with env_file, ports, volumes for config/storage. 4) Optional systemd unit + env file for server deployment. **Dependencies:** NFR6 (config for host/port). |
| **NFR8** (Documentation) | Without docs admins and operators cannot set up and harden the service correctly. | **Implementation:** 1) Zammad: custom fields, core workflow, macro (trigger tag), webhook URL and HMAC. 2) Archiver: path policy (archive_path, archive_user_mode, allow_prefixes), storage root, atomic write. 3) Signing/TSA: PFX, TSA URL, optional auth. 4) Operations: start/stop, logs, metrics, common failures and retry. 5) Security: trust boundaries, HMAC, rate limit, path confinement, secret handling. **Dependencies:** none (in parallel with code). |
| **NFR10** (No external queue) | Architecture decision; limits scaling, simplifies operations. | **Implementation:** No code. **Required:** 1) Record decision (PRD/ADR). 2) Document in-memory dedupe (e.g. delivery ID with TTL) and per-ticket in-flight guard. 3) Do not add dependency on Redis/RabbitMQ/etc.; background jobs in-process (e.g. FastAPI BackgroundTasks). **Dependencies:** none. |

### Order by importance (summary)

1. **NFR6** → **NFR9** (foundation: config + runtime)
2. **NFR3** (paths before any write)
3. **NFR1** → **NFR2** (ingress: HMAC, then body/rate)
4. **NFR4** → **NFR5** (data/transport: scrubbing, then URL validation)
5. **NFR7** → **NFR8** (deploy + docs)
6. **NFR10** (design only, no code)

---

## 3. Recommended implementation order (greenfield or rewrite)

If implementing the same NFRs from scratch, this order minimizes rework and risk:

### Phase 1 – Foundation (do first)

| Order | NFR | Rationale |
|-------|-----|-----------|
| 1 | **NFR6** – Config (env + YAML, precedence) | Everything else depends on settings (Zammad URL, storage root, hardening flags). Get loading and validation right first. |
| 2 | **NFR9** – Python version and dependencies | Lock runtime and dependencies so security and path logic use a consistent environment. |
| 3 | **NFR3** – Path validation and confinement | Storage writes are high-impact. Path policy and root confinement must be in place before any write path is used. Implement `path_policy` (sanitize, validate, `ensure_within_root`) and use it in layout + fs_storage; add symlink rejection and `O_NOFOLLOW` when adding file I/O. |

### Phase 2 – Ingress security (before processing)

| Order | NFR | Rationale |
|-------|-----|-----------|
| 4 | **NFR1** – HMAC verification, fail closed | Protects the only user-facing trigger. Must be in place before accepting production webhooks. |
| 5 | **NFR2** – Body size + rate limiting | Prevents DoS and resource exhaustion; depends on config (NFR6) for limits. |

### Phase 3 – Data and transport safety

| Order | NFR | Rationale |
|-------|-----|-----------|
| 6 | **NFR4** – Secret scrubbing | Prevents leakage in logs and ticket notes; implement before going to production. |
| 7 | **NFR5** – Transport safety checks | Validates Zammad/TSA URLs at startup; blocks unsafe defaults. Implement with config validation (after NFR6). |

### Phase 4 – Operability and constraints

| Order | NFR | Rationale |
|-------|-----|-----------|
| 8 | **NFR7** – Single process and deployment | Packaging and run scripts (Docker, systemd) so the service can be deployed and restarted cleanly. |
| 9 | **NFR8** – Documentation | Document setup, path policy, signing, storage, operations, and security so operators and admins can run and harden the service. |
| 10 | **NFR10** | Design decision: no external queue; in-memory dedupe/guard. No implementation task; document and enforce in design. |

---

## 4. Dependency graph (summary)

```
NFR6 (config) ──────────────────────────────────────────────────────────┐
NFR9 (python/deps) ─────────────────────────────────────────────────────┤
                                                                         ▼
NFR3 (path policy) ──► NFR1 (HMAC) ──► NFR2 (body + rate limit) ──► NFR4 (scrub) ──► NFR5 (transport)
                                                                         │
                                                                         ▼
NFR7 (deploy) ◄──────────────────────────────────────────────────── NFR8 (docs)
NFR10: design constraint (no queue)
```

- **NFR6 and NFR9** have no NFR dependencies; do first.
- **NFR3** should be done before any code that builds or writes storage paths.
- **NFR1 and NFR2** depend on config (NFR6) for secret and limits.
- **NFR4** should be in place before production (logs + ticket notes).
- **NFR5** depends on config and fits with validation (NFR6).
- **NFR7 and NFR8** can follow once the app and config are stable.

---

## 5. Optional hardening (beyond current NFRs)

If you want to tighten beyond the PRD:

| Priority | Improvement | Depends on |
|----------|-------------|------------|
| 1 | Done: env > YAML precedence verified and documented (see NFR6 row). | NFR6 |
| 2 | NFR verification tests in `test/nfr/` already cover HMAC 403/503/202, path escape, body/rate limit, redaction, transport, config, deploy, docs, Python version, and no-queue design. | — |
| 3 | Consider rate limiting per Zammad instance or per API key if multiple tenants share one archiver. | NFR2 |
| 4 | L1 (security-review): lockfile + pip-audit fail on HIGH | See [security-review.md](security-review.md) §4 Step 3.1. |

Security review remediation (see [security-review.md](security-review.md)) has been implemented: optional Bearer auth for `/metrics` (`METRICS_BEARER_TOKEN`), file mode `0o640` for written files, HMAC-SHA-256 support, rate-limit key from header (`RATE_LIMIT_CLIENT_KEY_HEADER`), ingest body schema validation, optional healthz omit version (`HEALTHZ_OMIT_VERSION`), and documentation of redaction/TOCTOU/delivery-ID/TEMPLATES_ROOT.

---

## 6. References

- PRD: [docs/PRD.md](PRD.md) §6 Non-functional requirements
- NFR verification tests: `test/nfr/` (run with `pytest test/nfr/ -v`)
- Security: [docs/09-security.md](09-security.md)
- Config: [docs/config-reference.md](config-reference.md)
