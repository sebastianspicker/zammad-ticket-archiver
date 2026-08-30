# Administration frontend

This document defines the maintained product, interaction, accessibility, and source
contracts for the optional Chronikwerk administration application.

## Purpose and runtime model

Chronikwerk turns authenticated Zammad webhook events into archival PDFs and JSON audit
sidecars, with optional PAdES signatures and RFC3161 timestamps. The administration
application is a feature-flagged, single-user operations surface for one running process.
It does not replace Zammad or archive storage.

Sessions, job history, admission counters, and accepted work are process-local. Managed
non-secret configuration persists separately and becomes active only after an external
restart. The interface must distinguish volatile, staged, active, and external state.

Principal workflows are:

- sign in and end the administration session;
- inspect process, capacity, storage, and configuration state;
- review volatile job history and request an acknowledged retry;
- review, edit, and stage allowlisted non-secret configuration; and
- inspect and restore managed configuration revisions.

RBAC, SSO, durable queues, archive browsing, secret management, live reload, and
UI-controlled restarts are outside this application.

## Users

| Audience | Primary needs |
| --- | --- |
| Operations and DevOps administrators | Compact identifiers, keyboard access, current capacity and storage state, and explicit restart boundaries |
| Zammad administrators and support leads | Recognizable ticket IDs, failure classifications, retry consequences, and volatile-history wording |
| Compliance and security reviewers | Semantic tables, readable identifiers, non-color status, revision history, and precise claims |
| Release maintainers and support personnel | Deterministic validation, stable terminology, error recovery, reflow, and browser-independent controls |

Help-desk staff and requesters remain in Zammad and are not control-plane users.

## Information architecture

The shell exposes three stable work areas:

1. `Overview` for current process, capacity, storage, revision, and failure state.
2. `Jobs` for volatile history, ticket detail, and acknowledged retry.
3. `Configuration` for allowlisted values, staged changes, and revision history.

Revision history stays within Configuration context. Every page has one primary heading,
clear route context, and at most one contextual action.

## Interaction and state contract

- A `202 Accepted` response and admission counters do not imply archival completion.
- Process-local history and sessions must be identified as volatile.
- Storage state must distinguish unchecked, writable, and unavailable conditions.
- Staged values remain visible as the current managed overlay until activation.
- Configuration editing uses review, explicit acknowledgement, then staging.
- Retry and revision restore retain their consequence acknowledgement.
- Validation, transport, capacity, and persistence failures remain visible in context.
- Session expiry uses inline reauthentication and preserves only allowlisted non-secret
  drafts.
- Status text never relies on color alone.

Facts use definition lists, event and revision data use tables, and ticket history uses
an ordered timeline. Forms use visible labels, adjacent errors, a focused error summary,
and specific action names.

## Visual system

The interface is a compact, desktop-first evidence workstation. It uses:

- a local sans-serif stack for headings, controls, and working text;
- monospace text only for paths, revisions, request IDs, timestamps, and metadata;
- white surfaces on a cool neutral canvas;
- archive ink for primary text and process blue for current navigation, focus, primary
  actions, and the live process field;
- green, amber, and red only as semantic colors paired with text;
- rectangular controls at least 42 pixels high; and
- bounded desktop content with local table scrolling.

The interface excludes gradients, textures, glass effects, glow, ornamental shadows,
decorative motion, external fonts, icon libraries, fake metrics, generic card grids,
marketing headings, onboarding tours, and decorative archive imagery.

## Responsive and accessibility contract

At 899 pixels, multi-column workspaces stack. At 639 pixels, filters, facts, forms, and
actions reflow. At 320 pixels, the document must not overflow; wide tables scroll inside
labeled regions.

WCAG 2.2 AA is the release target. The maintained interface requires:

- semantic landmarks, headings, tables, lists, labels, and native controls;
- a working skip link and visible keyboard focus;
- non-color status cues and persistent error feedback;
- correct `lang` metadata for German and English;
- field errors connected with `aria-describedby` and `aria-invalid`;
- reduced-motion handling;
- keyboard operation at all supported widths; and
- safe reflow at 400 percent zoom.

Automated axe checks support this contract but do not replace manual assistive-technology,
zoom, contrast, and populated-data review.

## Source and build contract

| Path | Role |
| --- | --- |
| `src/chronikwerk/web/templates/admin/` | Server-rendered Jinja templates |
| `frontend/admin/css/*.css` | Modular administration styles |
| `frontend/admin.ts` and `frontend/admin/*.ts` | Dependency-free TypeScript behavior |
| `src/chronikwerk/web/static/admin/admin.css` | Assembled packaged stylesheet |
| `src/chronikwerk/web/static/admin/admin.js` | Bundled packaged JavaScript |
| `src/chronikwerk/web/admin/` | HTML forms, session handling, and JSON endpoints |

Run `make frontend-check` to type-check the TypeScript, rebuild temporary assets, and
compare them with the packaged CSS and JavaScript. Use `make frontend-update` only when
the checked-in package assets intentionally need to change.

## Validation requirements

Changes to this surface require, as applicable:

- focused Python tests for route, state, and persistence behavior;
- TypeScript type checking and packaged-asset comparison;
- browser checks for both locales, keyboard entry, error recovery, and external-request
  boundaries;
- serious and critical axe checks;
- 390-pixel and 320-pixel reflow checks;
- screenshot manifest and source-hash validation for documentation previews; and
- manual Firefox, WebKit, screen-reader, 400 percent zoom, contrast, and populated-data
  review before publication claims.

Documentation previews are current-template examples, not release or conformance
evidence. Release evidence must be produced from and verified against the same reviewed
tag.
