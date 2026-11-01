# Public alpha status

## Candidate

| Field | Current value |
| --- | --- |
| Version | `0.3.0a1` |
| Proposed tag | `v0.3.0-alpha.1` |
| Publication state | Unreleased |
| Readiness | Not ready for publication |
| Status reviewed | 2026-11-01 (local source; publication gates remain open) |

The source is an unfrozen development candidate intended for local evaluation with
non-production data. Local commits and passing checks do not themselves constitute a release.
A source checkout, package, image, screenshot, or Pages artifact counts as release evidence only
when it is produced from — and verified against — the same reviewed tag.

## What works today

The candidate records authenticated requests durably, captures original records and attachment
bytes, renders reading copies, seals and timestamps a manifest, and commits verified revisions to
the filesystem store and catalog. Optional PAdES signing applies to the archival reading copy.
The authenticated history comes from the journal; interrupted work is recovered after restart.

The supported runtime remains one process per journal. Admin sessions and process counters are
volatile. `202 Accepted` means durable admission, not completed preservation. The static Pages
demo remains synthetic and is not operational deployment or release-validation evidence. Current
contracts are in [architecture](docs/01-architecture.md) and [operations](docs/08-operations.md).

## Repository checks

These aggregate gates are defined in the repository:

- `make verify-core`: Python and frontend checks, tests with coverage, documentation and source
  policy checks, package build, clean-wheel import, and repository smoke checks.
- `make verify`: `verify-core` plus the production-image smoke test.
- The security workflow: separate fail-closed dependency audits for the base and signing
  environments.
- `make pdf-ua-check PDF_FILES="..."`: pinned veraPDF validation for representative output.

No earlier result proves anything about the current dirty worktree. When the candidate is frozen,
record automated results, external evidence, artifact checksums, and the exact commit or tag
together.

## Open publication gates

Publication still requires all of the following against the exact proposed tag:

- a complete `make verify` run and security-workflow results;
- live Zammad workflow verification, including terminal tags and internal notes;
- production storage, signing, TSA, TLS-proxy, restart, and recovery checks;
- representative signed and unsigned PDF/UA validation;
- Chromium, Firefox, WebKit, narrow-layout, keyboard, and automated accessibility checks;
- manual screen-reader, reading-order, contrast, 400 percent zoom, and populated-data review; and
- review of packages, image, checksums, release notes, screenshots, and the static demo artifact.

## Known release risks

- Supported base, signing, development and tool environments use checked hash-pinned locks;
  installing arbitrary versions outside those locks does not reproduce the qualified candidate.
- The Docker Python base is digest-pinned; operating-system package resolution still depends on
  the upstream repositories.
- Container registry publication is not configured.
- Delivery-ID signing is opt-in; durable ID deduplication does not authenticate an unsigned
  delivery ID or prevent a valid body from being replayed under a changed ID.
- Deep storage health and FastAPI documentation routes are unauthenticated and need a trusted
  network boundary.
- Private vulnerability reporting has not been verified, and no fallback private channel is
  published.
- The image SBOM and an independent attribution review are not established in repository
  evidence. The copyright holder is Sebastian Spicker (`LICENSE`); the runtime dependency
  licences are listed in `THIRD_PARTY_NOTICES.md`.

## Maintained artifacts

The packaged admin files `src/chronikwerk/web/static/admin/admin.js` and `admin.css` are generated
from `frontend/` and intentionally versioned. The administration screenshots and their manifest are
maintained documentation assets. The Pages workflow builds its static artifact from `demo/site/`;
`build/` and `dist/` stay generated output.

Do not publish the candidate until the source is frozen, every required automated, manual, and
external gate passes against that state, and a release owner reviews the resulting artifacts.
