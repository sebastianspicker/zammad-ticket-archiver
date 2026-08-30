# Documentation

Use [../README.md](../README.md) for installation, configuration, usage, development,
testing, operation, troubleshooting, and security basics.

The documentation hierarchy is intentional: the root README defines public scope and
entry-level usage; this index provides navigation; ADRs record accepted rationale and
tradeoffs; architecture, API, and configuration references define technical contracts;
and deployment, operations, and security documents are operator runbooks. When two
documents overlap, use the more specific contract or runbook without widening the public
scope stated in the README.

## Architecture and data

- [Architecture](01-architecture.md): runtime flow, state transitions, module boundaries,
  and process constraints.
- [Data model](03-data-model.md): ticket snapshot and audit-sidecar fields.
- [Path policy](04-path-policy.md): archive placement, validation, sanitization, and root
  confinement.
- [PDF rendering](05-pdf-rendering.md): rendering pipeline, templates, sanitization, limits,
  and document structure.
- [Signing and timestamping](06-signing-and-timestamp.md): PAdES and RFC3161 configuration,
  runtime behavior, and verification.
- [Storage](07-storage.md): output layout, atomic-write behavior, and filesystem
  requirements.

## Integration and operation

- [Zammad setup](02-zammad-setup.md): fields, tags, webhook authentication, and smoke tests.
- [Configuration reference](config-reference.md): precedence, environment keys, YAML
  structure, defaults, and validation.
- [API reference](api.md): endpoints, authentication, payloads, responses, and errors.
- [Deployment](deploy.md): Docker Compose and optional systemd-wrapper setup.
- [Operations](08-operations.md): health, metrics, update, rollback, retries, and incident
  checks.
- [Security](09-security.md): trust boundaries, controls, hardening, and residual risks.
- [FAQ](faq.md): common runtime and deployment failures.

## Administration application

- [Administration application](admin-frontend.md): users, routes, state, configuration
  ownership, responsive behavior, and validation.
- [Screenshot evidence](screenshots/README.md): capture inputs and evidence limits.

## Project and release references

- [Public-alpha candidate](alpha-release.md): current evaluator expectations and
  compatibility boundary.
- [Release checklist](release-checklist.md): packaging, image, browser, PDF, security, and
  publication gates.
- [Migration to Chronikwerk](migration-to-chronikwerk.md): manual migration from the retired
  package layout.
- [Current architecture decision](adr/0004-current-architecture.md).
- [Administration and accessible-PDF decision](adr/0005-admin-config-and-accessible-pdf.md).
- [Zammad outbound transport trust-boundary decision](adr/0006-zammad-outbound-transport-trust-boundary.md).
- [Deterministic release-assurance decision](adr/0007-deterministic-release-assurance-scripts.md).
- [Modular-monolith boundary decision](adr/0008-modular-monolith-boundaries.md).
- [Product contract](../PRODUCT.md).
- [Design system](../DESIGN.md).
- [Release status](../RELEASE_STATUS.md).

## Contribution and policy

- [Contributing](../CONTRIBUTING.md).
- [Security policy](../SECURITY.md).
- [Code of Conduct](../CODE_OF_CONDUCT.md).
- [Brand assets](assets/brand/README.md).
