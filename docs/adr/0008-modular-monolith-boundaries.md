# ADR 0008: Modular monolith boundaries

## Status

Accepted (2026-08-27)

## Context

The archive use case had been distributed across historical `app`, `domain`, `adapters`, and
`config` layers. Those labels did not express ownership: HTTP routes scheduled domain work,
administration imported an ingest route, archive orchestration carried the global settings object,
and storage and rendering lived partly under jobs and partly under adapters. Private aliases existed
to support tests tied to old modules.

A durable job journal and independent acknowledgement worker were considered. They were rejected
for this reconstruction because they change `202` admission, restart recovery, deduplication,
retention, retry, shutdown, and multi-instance semantics. Such a change requires an outbox, leases,
migrations, and a separate product decision.

## Decision

Use one modular process with explicit ownership packages: `archiving`, `zammad`, `documents`,
`storage`, `configuration`, `operations`, and `web`, assembled only by `composition.py`.

Archive policy does not depend on delivery or infrastructure. Snapshot models belong to document
production; process-local job metadata belongs to operations; and shared failure classification,
outbound HTTP/DNS policy, and timestamp formatting have explicit root modules. Configuration is
translated into narrow immutable runtime options at composition instead of being threaded through
archive and storage internals. Interfaces are limited to genuine side-effect boundaries. Private cross-package
imports and compatibility aliases for internal module paths are prohibited. Architecture tests
enforce an acyclic feature graph with `archiving` as the coordinator rather than a dependency of
its own boundary modules.

## Consequences

- The repository tree communicates ownership and common changes have one obvious home.
- HTTP and admin delivery share one scheduling service without importing each other's routes.
- Zammad, document, and storage details remain independently testable boundary code.
- Existing HTTP, CLI, configuration, PDF/sidecar, tag/note, security, and deployment behavior is
  preserved.
- Volatile single-process operation and post-storage reconciliation remain explicit limitations.
- A future durable workflow can be designed from the archive attempt/outcome contracts without
  pretending that durability already exists.
