# Changelog

## Repository naming migration

The repository moves from `sebastianspicker/chronikwerk` to
`sebastianspicker/zammad-ticket-archiver`. Product commands, data formats, and runtime
identifiers remain unchanged. The demo moves to
https://sebastianspicker.github.io/zammad-ticket-archiver/.

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Python package versions use PEP 440 (for example, `0.3.0a1`), while Git prerelease
tags use the public form `v0.3.0-alpha.1`.

## [Unreleased]

### Fixed
- `POST /ingest/batch` rejects an empty array with `422 batch_empty` instead of accepting a
  zero-item batch.
- The PDF metadata box shows the archive path and mode when `fields.*` renames the Zammad
  attributes; the snapshot keys them by their canonical names.
- Show attachment MIME types in the PDF and snapshot: Zammad keeps them in the attachment's
  `preferences["Content-Type"]`, not a top-level field.
- Fetch tickets with `GET /api/v1/tickets/{id}?expand=true` and map the response Zammad actually
  returns: owner, last editor and customer come from the expanded login strings (an unassigned
  `-` owner counts as no owner), and the `archive_path`, `archive_user_mode` and `archive_user`
  fields are read as top-level custom ticket attributes instead of `preferences.custom_fields`.
  Every archive attempt previously failed with `ticket.owner.login must be a string`.
- `archive_user_mode=current_agent` reads the triggering agent from the webhook's
  `ticket.updated_by.login` (Zammad sends no top-level `user`), then falls back to the fetched
  ticket's last editor, which also covers `/retry`. It never resolves to Chronikwerk's own API
  account (looked up once via `GET /api/v1/users/me`), which can be the last editor after a
  failed run; such a retry fails with a note asking for the archive to be triggered from Zammad.
- Accept Zammad's native webhook signature (`X-Hub-Signature: sha1=<hex>`, HMAC-SHA1) in
  compatibility mode; every unmodified Zammad webhook was rejected with `403`. Strict
  delivery-ID mode remains SHA-256 only (ADR 0010).
- Remove Zammad tags with `DELETE /api/v1/tags/remove`, the only verb Zammad routes; the
  previous `POST` failed with 404 on the first tag transition of every run.

### Changed
- Zammad error-note hints now name the configured trigger tag, and the permission and field
  hints apply to the failures they describe. Error notes redact more secret shapes through one
  shared scrubber.
- Remove the `settings_not_configured` 503 and the `skipped_no_ticket_id` history status; jobs
  always carry a ticket id. Delivery dedupe no longer pauses while shutting down.
- Job history, ticket guards, and shutdown are owned by the composition root instead of module
  globals (ADR 0009); `documents` no longer depends on `zammad`.
- Remove the `smoke`, `qa`, `test-unit`, `test-all`, `ci`, `docker-smoke`, and `typecheck-all`
  make targets; use `test`, `test-fast`, `test-int`, `verify-core`, and `verify`.

### Documentation
- Add `THIRD_PARTY_NOTICES.md` with the declared licence of every runtime dependency in the
  production lock, and ship it in the sdist and wheel.
- Tell operators to keep the archive fields agent-only in Zammad, because they choose the
  archive directory.
- Document that the process variable `LOG_LEVEL` overrides `observability.log_level` outside the
  normal configuration precedence.
- Name Sebastian Spicker as the copyright holder in `LICENSE` and as the package author.
- Describe the webhook and ticket shapes Zammad actually sends: the sample webhook payload now
  uses Zammad's default trigger body (top-level `ticket` and `article`, nested user objects,
  custom attributes on `ticket`), the data model and Zammad setup pages document the top-level
  custom attributes and how each `archive_user_mode` resolves its login, and error-note hints
  no longer refer to `custom_fields.*`.
- Correct the default archive filename example to the date-only `Ticket-123_2026-02-07.pdf`
  and document the Prometheus metric names.

## [0.3.0-alpha.1]

### Changed
- Rebrand public documentation, GitHub links, deployment identifiers, and container
  operator commands as Chronikwerk. Existing installations require the documented
  stopped-service manual state copy; no automatic migration runs.
- Raise the supported Python baseline to 3.14 and pin CI and container builds to
  Python 3.14.6.
- Move the administration browser source to TypeScript 7.0.2 with a complete npm lock
  while continuing to ship a compiled JavaScript asset.
- Replace the Python-only C901 release check with repository-owned complexity and
  duplication gates spanning shipped Python, TypeScript, and CI helpers.
- Align the public alpha documentation, GitHub community files, ignore policy, and
  administration screenshot provenance with the current implementation.
- Rename the systemd environment template to `chronikwerk.env.example`, keep the
  operator-edited form ignored, and declare the validated Linux package target.
- Freeze the supported deployment/runtime contract: one process-local service,
  attachment metadata only, sanitized rich HTML, SHA-256-only webhook HMAC,
  authenticated job history, and one production image with signing support.
- Document that graceful shutdown drains admitted work while a process crash may
  lose accepted background work.
- Replace the historical dashboard with a disabled-by-default German/English admin
  application for operational status, volatile job history, acknowledged retries,
  and staged non-secret configuration revisions.
- Render one localized archival PDF layout with semantic headings, explicit article
  coverage, A4-safe long-content handling, DejaVu Sans, bookmarks, and tagged
  WeasyPrint `pdf/ua-1` output.

### Added
- Public alpha documentation, current administration screenshots, and a tag-gated
  prerelease workflow that validates alpha, beta, and release-candidate versions.
- Process-local admin sessions with idle and absolute expiry, strict cookies, CSRF,
  CSP, no-store responses, and inline reauthentication that preserves non-secret
  configuration drafts.
- Atomic managed-configuration overlays with optimistic concurrency, bounded revision
  history, external-restart truthfulness, and offline list/rollback commands.
- Production-image PDF and pinned veraPDF validation gates.

### Removed
- Redis queue and DLQ behavior, the historical decorative dashboard, alternate PDF
  template variants, demo assets, and obsolete local operations helpers.

### Security
- Require `cryptography>=48.0.1` so signing installations do not use wheels affected
  by GHSA-537c-gmf6-5ccf.
- Bind strict-mode webhook signatures to the normalized delivery ID, bound the
  process-local replay set, and keep legacy body-only signatures in non-strict mode.
- Default production Compose publishing to loopback, make deep health probes
  single-flight, and fail closed when metrics authentication is misconfigured.
- Reject unsafe managed-state directory ownership/permissions, symlinked ancestors,
  and post-initialization path substitution; fully redact escaped quoted and compound
  Python/JSON credential representations.
- Keep the GitHub Docker workflow build-only, without registry credentials,
  package-write permission, or image publication.

### Fixed
- Reduce high-complexity request, ingest, administration, and managed-configuration
  control flow without changing their public contracts.
- Consolidate stable runtime and test helpers and remove code proven unreachable
  after the modernization.
- Reject batch admission once shutdown begins, honor the effective private-network
  policy for injected Zammad test runtimes, and keep retained managed-revision
  chains valid after pruning.
- Keep runtime status, admin pages, audit sidecars, and ticket notes on the same version
  as Python package metadata instead of reading stale editable-install metadata.
- Resolve macOS temporary paths before managed-state validation.
- Accept dated Keep-a-Changelog headings in prerelease validation and preserve local
  virtual environments during maintenance.
- Keep synchronous filesystem/signing work attached across repeated task
  cancellation until its side effects finish.
- Reload preserved-mtime PFX rotations and record audit fingerprints from the
  exact signer used for the stored PDF; reject cached certificates immediately
  after their validity window ends.
- Disambiguate lossy username, archive-segment, and ticket-number sanitization
  so distinct raw values cannot overwrite the same archive location.
- Serialize native WeasyPrint rendering across worker threads to prevent
  process-level crashes during concurrent batch jobs.
- Abort revision pruning on unsafe or failed history reads, preserve primary
  transaction errors across cleanup failures, and report committed stages as
  successful when only retention cleanup fails.

## [0.2.0-rc.2] - 2026-04-09

### Added
- Coverage threshold enforcement in CI (76% minimum with branch coverage)
- Batch size limit (100 items) on POST /ingest/batch endpoint
- Redis URL scheme validation (redis://, rediss://, unix://)
- Redis URL credential redaction in logs and config dumps
- Async retry helper utility for exponential backoff
- 82+ new tests across CLI, adapters, input validation, and async retry

### Changed
- Tightened exception handling in CLI, sanitizer, and adapter modules
- Deduplicated CLI error handling with shared decorator
- Extracted async_retry helper from process_ticket
- Improved type annotations across server, settings, and middleware
- Rate limit header fallback prevents bypass when header is missing

### Fixed
- Pre-existing mypy arg-type error in tsa_rfc3161.py (auth parameter placement)
- Line-too-long lint violation in render_pdf.py

### Security
- Positive integer validation on all ticket_id parameters
- Rate limit `rps` and `burst` upper bounds (le=10000)
- `--no-cache-dir` on all CI pip install commands
- Input validation hardening across webhook and admin endpoints

### Documentation
- Added 34 missing docstrings across core source modules
- Updated config-reference.md with missing fields (pdf.templates_root, TSA user/password)
- Updated api.md with 2 missing admin endpoints and 3 undocumented query parameters
- Fixed template_variant comment to include all variants

## [0.2.0-rc.1] - 2026-02-26

### Added
- Redis-backed job history stream with API and CLI access (`/jobs/history`, `queue-history`).
- Dead-letter queue drain operations for jobs and admin APIs.
- Optional admin dashboard and admin API surface (`/admin`, `/admin/api/*`) protected by bearer token.
- Additive configuration keys for admin and workflow history (`admin.*`, `workflow.history_*`).
- Additional regression tests for cancellation flow, template-root rendering, and history redaction.

### Changed
- Refactored ticket processing and queue modules to reduce complexity and improve failure isolation.
- Hardened job/admin routes with clearer `401`/`503` behavior on auth/backend failures.
- Improved PDF template styling consistency across default, compact, and minimal variants.
- Updated CI/QA gates with docs check, complexity check (`C901`), and Dockerfile.dev smoke validation.

## [0.1.0] - 2026-02-07

### Added
- FastAPI ingress endpoint (`POST /ingest`) with optional HMAC verification.
- Zammad API client integration for reading tickets and writing internal notes/tags.
- Snapshot model + template-based HTML rendering + PDF generation (WeasyPrint).
- Optional PAdES signing (pyHanko) and RFC3161 timestamping (TSA).
- Atomic storage writes for PDFs and audit sidecar JSON.
- Ops scripts for signature verification and CIFS mount helpers.
- Unit and integration test suite.
- Initial English operator and architecture documentation in `docs/`.
