# Bugs & Required Fixes

List derived from documentation, known limitations, and operations runbooks. Each item can be turned into a separate issue.

---

## Known Limitations / Bugs

### 1. [Bug] Field name `archive_user` is hardcoded

**Description:** In `archive_user_mode=fixed`, the Zammad custom field is **only** expected under the name `archive_user` (`custom_fields.archive_user`). The field name is fixed in code and not configurable.

**Impact:** Zammad must create a field with the exact internal name `archive_user`. Other names (e.g. `archive_user_id` or localized labels) are not supported.

**Fix:** Introduce a configurable field name (e.g. in `fields.archive_user` analogous to `fields.archive_path` / `fields.archive_user_mode`) and use it in path computation and validation.

**Sources:** README, `docs/02-zammad-setup.md`, `docs/03-data-model.md`

---

### 2. [Bug/Operational] Delivery ID deduplication is in-memory only

**Description:** Deduplication by `X-Zammad-Delivery` is in-memory only. After a process restart the history is lost; the same delivery ID can be processed again.

**Impact:** No consistent deduplication across restarts or multiple instances. An optional Redis backend exists (`idempotency_backend=redis`, `redis_url`) but is not the default.

**Fix (optional):**  
- Document and recommend Redis as the default when avoiding duplicate processing matters.  
- Or: Clearly document as "best-effort, process-local" and document behaviour on restart/scaling (partially already in README/PRD).

**Sources:** README, `docs/08-operations.md`, PRD NFR10

---

### 3. [Bug/Operational] No guaranteed exactly-once after 202

**Description:** After `202 Accepted`, processing is best-effort. There is no guarantee of exactly-once execution across restarts or multiple pods.

**Impact:** In rare cases a ticket may be archived twice or left incomplete (e.g. stuck in `pdf:processing`).

**Fix:** Describe more clearly in FAQ/operations when "stuck" `pdf:processing` occurs and how to recover manually (re-add trigger tag, remove `pdf:processing` if needed). No code change strictly required, but better visibility.

**Sources:** PRD 9.2, `docs/faq.md`, `docs/08-operations.md`

---

### 4. [Bug] Large tickets fail when article limit is exceeded

**Description:** If a ticket has more articles than `pdf.max_articles` (default 250), processing fails. The message may appear as "Rendering article limit exceeded" in the ticket note.

**Impact:** Tickets with very many articles end up in `pdf:error` even though they could be archived in principle.

**Fix:**  
- Increase the limit (`PDF_MAX_ARTICLES`) or set to 0 (unlimited).  
- Optional: Instead of a hard limit, log a warning and continue with a capped article count (config option "cap and continue").

**Sources:** `docs/faq.md`, `docs/08-operations.md` (Rendering article limit exceeded)

---

### 5. [Bug/Config] 403/503/400 on webhook – common configuration errors

**Description:** Common errors when calling `POST /ingest`:  
- **403:** HMAC validation failed (secret, header `X-Hub-Signature`, body unchanged).  
- **503:** No webhook secret set with unsigned mode disabled.  
- **400:** `X-Zammad-Delivery` missing while `hardening.webhook.require_delivery_id=true`.

**Fix:** Not a code bug; add a checklist or link to FAQ/operations in the API docs or bug report template so support issues can be triaged faster.

**Sources:** `docs/faq.md`, `docs/08-operations.md`, `docs/02-zammad-setup.md`

---

## Required Fixes / Improvements (derived from docs)

### 6. [Enhancement] Configurable custom field name for `archive_user`

Same as (1): Make the `archive_user` field name configurable so Zammad field names can stay flexible.

---

### 7. [Enhancement] Clearer error messages for path/field validation

**Description:** On permanent failures (e.g. missing `archive_path`, `allow_prefixes` violation, missing `archive_user` in `fixed` mode) the ticket is marked `pdf:error`. The exact cause should be clearly identifiable in the internal note and logs.

**Fix:** Ensure all failure reasons (missing field, prefix policy, path sanitization) are reported with distinct codes/messages; optionally add short hints for typical fixes (e.g. "set archive_path" / "check allow_prefixes").

**Sources:** `docs/faq.md` (Permanent classification), `docs/04-path-policy.md`

---

### 8. [Enhancement] Documentation: residual risks and deployment checklist

**Description:** External risks (CIFS durability, `/metrics` access control, TSA certificate trust) should be visible to operators.

**Fix:** Add a short section in `docs/08-operations.md` or `docs/09-security.md`: "Residual risks" plus reference to `docs/release-checklist.md` (deployment safety checks) so operators can find these points easily.

**Sources:** `docs/release-checklist.md`

---

### 9. [Operational] Stuck `pdf:processing` – document recovery

**Description:** If the process is interrupted during processing, a ticket can remain stuck with `pdf:processing`.

**Fix:** Recovery steps are already in FAQ/08-operations (clean tags, re-add trigger). Optional: Short "Stuck in pdf:processing" section in the README or a pointer to `docs/faq.md` under "Operational Notes".

**Sources:** `docs/faq.md`, `docs/08-operations.md`

---

### 10. [Enhancement] Optional: article limit as "cap and continue"

Same as (4): Instead of failing immediately when `max_articles` is exceeded, add an option to "cap and continue" (with a log warning) so large tickets can still be archived.

---

## Critical

### 11. [Bug] Ingest: `_resolved_ticket_id()` ignores top-level `ticket_id` when `ticket` is a dict

**Description:** When the payload has a `ticket` object (even empty or with missing `ticket.id`), the handler uses only `ticket.id` and never falls back to top-level `ticket_id`, contradicting the validator message and causing 422 for valid shapes.  
**Fix:** Unify precedence (e.g. prefer top-level `ticket_id` when present, or document and enforce "ticket.id or ticket_id" consistently).  
### 12. [Bug] HMAC: Allow unsigned with no secret fully disables webhook auth

**Description:** If no webhook secret is set and `hardening.webhook.allow_unsigned` is true, all requests pass without signature verification. Easy to enable by mistake or via config/secret loading failure.  
**Fix:** Require explicit opt-in (e.g. separate "allow unsigned only when secret is set" vs "allow unsigned when no secret"); document and harden default.  
### 13. [Bug] Path/storage: Symlink confinement is best-effort and TOCTOU

**Description:** Symlink checks run after `ensure_dir(parent)`; a symlink can be introduced between check and write, and `mkdir` can create directories outside `storage_root` before the later check.

**Fix:** Reorder to validate path/symlinks before any directory creation; or document as best-effort and scope to trusted storage.

---

### 14. [Bug] Path/storage: `O_NOFOLLOW` is optional and silently 0 on some platforms

**Description:** Final-file open uses `getattr(os, "O_NOFOLLOW", 0)`; on platforms without it, open can follow a symlink at the target.  
**Fix:** Fail closed on unsupported platforms or document platform limits; ensure symlink rejection covers the final path component.  
---

### 15. [Bug] Workflow: `CancelledError` bypasses error handling and leaves tags stuck

**Description:** On Python 3.13+, `asyncio.CancelledError` is not caught by `except Exception`, so error tagging and processing-tag cleanup do not run; ticket can stay in `pdf:processing` or without trigger.  
**Fix:** Catch `BaseException` (or `CancelledError` explicitly), run cleanup, then re-raise.  
---

### 16. [Bug] Workflow: In-flight lock release not cancellation-safe; can leak in-flight ticket set

**Description:** If the task is cancelled during `await _release_ticket()`, the ticket ID may never be removed from `_IN_FLIGHT_TICKETS`, causing permanent skip for that ticket in this process.  
**Fix:** Ensure release runs in a `finally` that re-raises `CancelledError` after release; or use a cancellation-safe guard pattern.  
### 17. [Bug] Idempotency: Redis "claim" is GET then SET, not atomic

**Description:** With Redis backend, `_claim_delivery_id` does `seen()` then `add()`; under concurrency across workers both can see "not seen" and both add, allowing duplicate processing.  
**Fix:** Use Redis SETNX or a single Lua/transaction so claim is atomic.  
---

### 18. [Bug] PDF: WeasyPrint used without URL-fetch restrictions (file + SSRF)

**Description:** No custom `url_fetcher`; WeasyPrint can follow `file://` and network URLs from HTML/CSS. With untrusted content (see #19), this is local file read + SSRF.  
**Fix:** Provide a safe `url_fetcher` (e.g. block `file://`, restrict hosts) or disable URL resolution.  
---

### 19. [Bug] PDF: Article HTML rendered with `|safe` (trusted)

**Description:** Templates render `article.body_html` with `|safe`, so any upstream HTML is trusted; combined with #18 this enables resource fetching and injection.  
**Fix:** Sanitize/strip HTML before rendering, or render in a sandboxed context; avoid `|safe` on untrusted content.  
---

### 20. [Bug] Storage: Symlink rejection runs after directory creation

**Description:** `ensure_dir(parent)` is called before `_reject_symlinks_under_root()`, so directory creation can happen outside `storage_root` before the check.  
**Fix:** Validate path and symlinks before creating any directory.  
---

### 21. [Bug] Storage: `chmod` after `replace` follows symlinks and is race-prone

**Description:** `os.chmod(target, 0o640)` after `os.replace` can follow a symlink if `target` is swapped; can change permissions on the wrong file.  
**Fix:** Use `os.fchmod` on the open fd before closing, or avoid chmod when not needed; do not chmod by path after replace.  
---

### 22. [Bug] Redaction: `scrub_secrets_in_text` misses JSON/dict-style secrets

**Description:** Quoted key/value patterns (e.g. `{"api_token": "..."}`) are not redacted; exception/log text containing serialized headers or config can leak secrets.  
**Fix:** Extend scrubber to match JSON and dict-style key/value secret patterns.  
---

### 23. [Bug] Redaction: Env-var style secret lines not redacted

**Description:** Word-boundary in regex prevents matching subkeys like `API_TOKEN` after underscore (e.g. `ZAMMAD_API_TOKEN=...`, `SIGNING_PFX_PASSWORD=...`).  
**Fix:** Add patterns for env-var style lines (e.g. `NAME_TOKEN=...`, `NAME_PASSWORD=...`) or relax word-boundary for known secret key names.

---

## High

### 24. [Bug] Ingest: Delivery ID not stripped (whitespace preserved)

**Description:** Non-empty delivery ID values keep leading/trailing whitespace, so idempotency/dedup can treat "abc" and " abc " as different and allow duplicate processing or inconsistent log correlation.

**Fix:** Strip delivery_id when normalizing (e.g. use stripped value for cache and logging).

---

### 25. [Bug] Ingest: Missing settings returns 202 but skips processing (silent drop)

**Description:** When request.app.state.settings is missing, the handler returns 202 with accepted: true but does not schedule any background work. Caller sees success while no processing occurs.

**Fix:** Return 503 or 500 when settings are missing and processing would be skipped; or document clearly and log prominently.

---

### 26. [Bug] Ingest: Background scheduling is non-durable (no retry)

**Description:** BackgroundTasks runs in-process after response; scheduled work is lost on restart and there is no retry/backoff.

**Fix:** Document as best-effort; optionally support a durable queue for critical deployments.

---

### 27. [Bug] HMAC: 403 response without consuming request body

**Description:** When signature is missing or invalid, the middleware returns 403 without reading the body. Can cause connection hygiene issues or allow large-body DoS before rejection.

**Fix:** Drain the request body before returning 403 (or document server behaviour).

---

### 28. [Bug] HMAC: Client disconnect during body read treated as normal

**Description:** On http.disconnect, body read returns accumulated chunks and HMAC is computed on that partial body; auth decision can be made on truncated payload.

**Fix:** Treat disconnect during body read as auth failure (reject) and do not invoke downstream with partial body.

---

### 29. [Bug] Path/storage: Confinement is path-prefix only; hardlinks can escape

**Description:** Storage confinement checks path prefix only. A hardlink inside storage_root to an inode outside it can be overwritten via normal write, bypassing confinement.

**Fix:** Document limitation; or enforce no-hardlink policy / inode checks where feasible.

---

### 30. [Bug] Path/storage: allow_prefixes=[] disables allowlist (all paths allowed)

**Description:** Empty list is falsy, so the allow-prefix check is skipped and all segment paths are permitted. "No prefixes allowed" behaves like "no restriction".

**Fix:** Treat allow_prefixes=[] as "no path allowed" (reject unless explicitly documented otherwise).

---

### 31. [Bug] Path/storage: build_filename() can produce empty or .. segments

**Description:** Sanitized segments can be empty or dot-segments; result can be empty or contribute to path traversal if used as a path component.

**Fix:** Validate and reject empty/dot-segment filenames; enforce max length.

---

### 32. [Bug] Workflow: In-flight lock is process-local only

**Description:** Per-ticket concurrency guard is in-memory; multiple workers or replicas can process the same ticket concurrently and cause tag races.

**Fix:** Document; optionally use Redis or similar for cross-process locking.

---

### 33. [Bug] Workflow: should_process ignores pdf:processing and pdf:error

**Description:** Gate only blocks when done tag is present. Tickets already in pdf:processing or pdf:error can be considered eligible when require_trigger_tag=False, or a second worker can start despite processing tag.

**Fix:** Treat pdf:processing as in-flight (skip) and optionally treat pdf:error as blocked until trigger is re-added.

---

### 34. [Bug] Workflow: TOCTOU – apply_processing can demote pdf:signed back to processing

**Description:** Two workers can both pass should_process; the slower one can then call apply_processing, which removes pdf:signed and sets processing, undoing the first worker's completion.

**Fix:** Use conditional tag updates or single atomic transition where API allows; or accept and document race.

---

### 35. [Bug] Workflow: Error path can orphan tickets (no trigger, no processing, no done/error)

**Description:** If apply_error fails after apply_processing removed the trigger, and cleanup removes pdf:processing, the ticket can end with no state tags and be skipped forever under require_trigger_tag=True.

**Fix:** On apply_error failure, re-add trigger or ensure at least one tag reflects "needs attention"; log and optionally alert.

---

### 36. [Bug] Idempotency: Delivery ID claimed before should_process (no-op can block retries)

**Description:** If should_process returns false, the function returns but the delivery ID stays "seen" for the TTL, so a later valid replay with the same ID can be skipped.

**Fix:** Claim delivery ID only after should_process is true; or document and accept.

---

### 37. [Bug] Idempotency: Claim before durable success (failures can suppress retries within TTL)

**Description:** Delivery ID is marked seen before processing completes. A failure after claim but before success prevents same delivery ID from retrying until TTL expires.

**Fix:** Record delivery ID as seen only after successful completion (e.g. after apply_done); or document and accept.

---

### 38. [Bug] PDF: template_name can escape TEMPLATES_ROOT via absolute/traversal

**Description:** template_name is joined to TEMPLATES_ROOT without sanitization; absolute or .. segments can point to an arbitrary directory.

**Fix:** Restrict template_name to a known allowlist (e.g. default, minimal, compact) or sanitize and reject path separators and traversal.

---

### 39. [Bug] PDF: No sandbox for template context

**Description:** Full object graph is passed into Jinja templates; if template or config is influenced by untrusted input, template code can access more than intended.

**Fix:** Restrict template variables to a minimal whitelist; load templates only from trusted paths.

---

### 40. [Bug] Storage: write_bytes does not enforce permissions on overwrite

**Description:** When overwriting an existing file, os.open(..., 0o640) ignores the mode; existing (e.g. world-readable) permissions are preserved.

**Fix:** After open or after write, call os.fchmod(fd, 0o640) so permissions are always set.

---

### 41. [Bug] Storage: O_NOFOLLOW missing or platform-conditional in atomic write path

**Description:** Atomic write path may not use O_NOFOLLOW at the final target, or it is 0 on some platforms; symlink at target can be followed.

**Fix:** Use O_NOFOLLOW where available; document platform behaviour; ensure symlink checks cover final component.

---

### 42. [Bug] Redaction: api_key / apikey redacted in dicts but not in free-form text

**Description:** Dict redaction treats these as sensitive; scrub_secrets_in_text does not, so api_key=... in log/exception text can leak.

**Fix:** Add api_key/apikey to the free-form text scrubber patterns.


---

## Quick reference: common failure causes

| Symptom | Typical cause | Fix / see |
|--------|----------------|-----------|
| `403` on `/ingest` | HMAC invalid / wrong secret | `WEBHOOK_HMAC_SECRET`, `X-Hub-Signature`, body unchanged |
| `503 webhook_auth_not_configured` | No secret, unsigned disabled | Set `WEBHOOK_HMAC_SECRET` or (test only) `HARDENING_WEBHOOK_ALLOW_UNSIGNED=true` |
| `400 missing_delivery_id` | Header missing, require enabled | Send `X-Zammad-Delivery` or disable require |
| `pdf:error` (Permanent) | Path/fields/policy/signing/TSA | Ticket note + `docs/faq.md`, `08-operations.md` |
| `pdf:processing` stuck | Process interrupted | Recovery: `docs/faq.md` (Stuck) |
| Storage permission | Path/mount/UID/GID/ACLs | `docs/07-storage.md`, `docs/faq.md` |
| Signing/TSA errors | PFX/password/TSA URL/CA/auth | `docs/06-signing-and-timestamp.md`, `docs/faq.md` |
| Article limit | Too many articles | Increase `PDF_MAX_ARTICLES` or set 0; consider `minimal` template |

---

## Using this list for issues

- **Labels:** `bug`, `enhancement`, `documentation`, `operational` as appropriate.
- **Title:** Use the **[Bug]** / **[Enhancement]** part as a prefix or label.
- **Body:** Copy the relevant section (description, impact, fix, sources) into the issue.
- The **quick reference** table can be added as a comment to a meta-issue "Common issues / Troubleshooting" or linked from the README.
