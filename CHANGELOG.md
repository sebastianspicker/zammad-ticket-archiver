# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- Complete English documentation in `docs/`.
