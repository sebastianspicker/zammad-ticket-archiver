# Documentation

Start with the [root README](../README.md) for installation, configuration, usage, development,
testing, operation, troubleshooting, and security basics. Come back here when you need the full
technical or operator reference.

How the pieces fit together: the README sets public scope and entry-level usage, this page is the
map, the ADRs record why accepted tradeoffs were made, the architecture/API/configuration
references define technical contracts, and the deployment, operations, and security documents are
operator runbooks. Where two pages overlap, trust the more specific contract or runbook — but it
does not widen the scope stated in the README.

## Architecture and data

- [Architecture](01-architecture.md): runtime flow, state transitions, module boundaries, and
  process constraints.
- [Data model](03-data-model.md): the ticket snapshot, the preservation package layout, the
  manifest, and revision identity.
- [Path policy](04-path-policy.md): archive placement, validation, sanitization, and root
  confinement.
- [PDF rendering](05-pdf-rendering.md): the rendering pipeline, templates, sanitization, limits,
  and document structure.
- [Signing and timestamping](06-signing-and-timestamp.md): the manifest seal, RFC 3161 token,
  trust anchors, optional PAdES signature, and how to verify a package.
- [Storage](07-storage.md): key layout, write-once behavior, retention modes, and filesystem
  requirements.

## Integration and operation

- [Zammad setup](02-zammad-setup.md): custom fields, tags, webhook authentication, and smoke tests.
- [Configuration reference](config-reference.md): precedence, environment keys, YAML structure,
  defaults, and validation.
- [API reference](api.md): endpoints, authentication, payloads, responses, and errors.
- [Deployment](deploy.md): Docker Compose and the optional systemd wrapper.
- [Operations](08-operations.md): health, metrics, journal, restart recovery, retries, error
  codes, verification, and what is not built yet.
- [Security](09-security.md): trust boundaries, controls, hardening, and residual risks.
- [FAQ](faq.md): common runtime and deployment failures.

## Administration application

- [Administration application](admin-frontend.md): users, routes, state, configuration ownership,
  responsive behavior, and validation.
- [Screenshot tour](screenshots/README.md): the maintained renders and what they do and don't prove.

## Project and release references

- [Public-alpha candidate](alpha-release.md): what evaluators should expect, and the compatibility
  boundary.
- [Dependency locks](dependency-locks.md): hash-locked environments, image pins, regeneration, and
  the remaining operating-system package variability.
- [Release checklist](release-checklist.md): packaging, image, browser, PDF, security, and
  publication gates.
- [Migration to Chronikwerk](migration-to-chronikwerk.md): manual migration from the retired
  package layout.
- Architecture decisions:
  [0004 current architecture](adr/0004-current-architecture.md),
  [0005 admin config and accessible PDF](adr/0005-admin-config-and-accessible-pdf.md),
  [0006 Zammad outbound transport trust boundary](adr/0006-zammad-outbound-transport-trust-boundary.md),
  [0007 deterministic release-assurance scripts](adr/0007-deterministic-release-assurance-scripts.md),
  [0008 modular-monolith boundaries](adr/0008-modular-monolith-boundaries.md),
  [0009 composition-owned runtime state](adr/0009-composition-owned-runtime-state.md),
  [0010 Zammad native webhook signature](adr/0010-zammad-native-webhook-signature.md),
  [0011 durable request journal](adr/0011-durable-request-journal.md),
  [0012 sealed preservation package](adr/0012-sealed-preservation-package.md),
  [0013 write-once archive store](adr/0013-write-once-archive-store.md).
- [Product contract](../PRODUCT.md).
- [Design system](../DESIGN.md).
- [Release status](../RELEASE_STATUS.md).

## Contribution and policy

- [Contributing](../CONTRIBUTING.md).
- [Security policy](../SECURITY.md).
- [Code of Conduct](../CODE_OF_CONDUCT.md).
- [Brand assets](assets/brand/README.md).
