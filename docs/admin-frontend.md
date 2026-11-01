# Administration frontend

Product, interaction, accessibility, and source contracts for the optional Chronikwerk
administration application.

## Purpose and runtime model

Chronikwerk records authenticated webhook events durably and builds sealed, timestamped
preservation packages with reading copies; optional PAdES signing applies to the archival copy. The administration application is a
feature-flagged, single-user operations surface for one running process. It does not replace Zammad
or archive storage.

Admin sessions and process counters are volatile; accepted work and request history are stored
in the durable journal. Managed non-secret configuration persists separately and becomes active
only after an external restart. Keep journal, session, staged and external state distinct.
Historical screenshots may retain earlier volatile-history labels; the API and operations
contracts define the current persistence behavior.

The principal workflows are:

- sign in and end the administration session;
- inspect process, capacity, storage, and configuration state;
- review journal request history and request an acknowledged retry;
- review, edit, and stage allowlisted non-secret configuration; and
- inspect and restore managed configuration revisions.

RBAC, SSO, archive browsing, secret management, live reload, and UI-controlled
restarts are out of scope.

## Who it is for

| Audience | Primary needs |
| --- | --- |
| Operations and DevOps administrators | Compact identifiers, keyboard access, current capacity and storage state, and explicit restart boundaries |
| Zammad administrators and support leads | Recognizable ticket IDs, failure classifications, retry consequences, and clear journal and session persistence wording |
| Compliance and security reviewers | Semantic tables, readable identifiers, non-color status, revision history, and precise claims |
| Release maintainers and support personnel | Deterministic validation, stable terminology, error recovery, reflow, and browser-independent controls |

Help-desk staff and requesters stay in Zammad; they are not control-plane users.

## Information architecture

The shell exposes three stable work areas:

1. **Overview** — current process, capacity, storage, revision, and failure state.
2. **Jobs** — journal history, ticket detail, and acknowledged retry.
3. **Configuration** — allowlisted values, staged changes, and revision history.

Revision history stays within the Configuration context. Every page has one primary heading, clear
route context, and at most one contextual action.

## Interaction and state contract

- A `202 Accepted` response and admission counters do not imply archival completion.
- Journal history is persistent; process-local sessions are identified as volatile.
- Storage state distinguishes unchecked, writable, and unavailable conditions.
- Staged values stay visible as the current managed overlay until activation.
- Configuration editing moves through review, acknowledgement for security-sensitive values, then
  staging.
- Editing a value or an acknowledgement invalidates the review. Late validation responses cannot
  approve newer edits. Staging freezes editable controls until the request finishes and consumes the
  successful review; environment-owned fields stay locked.
- Reset restores the displayed configuration baseline and clears its review.
- Retry and revision restore keep their consequence acknowledgement.
- Validation, transport, capacity, and persistence failures stay visible in context.
- Session expiry uses inline reauthentication and preserves only allowlisted non-secret drafts.
- Status text never relies on color alone.

Facts use definition lists, event and revision data use tables, and ticket history uses an ordered
timeline. Forms use visible labels, adjacent errors, a focused error summary, and specific action
names.

## Visual system

The interface is set as an annal: a narrow margin track carries section names, times, counts,
filters, and notes, while the body track carries the record. One continuous hairline divides the
two tracks; on narrow screens it becomes the left edge rule. See [`DESIGN.md`](../DESIGN.md).

- Atkinson Hyperlegible Next for headings, controls, and text, and Atkinson Hyperlegible Mono only
  for paths, revisions, request IDs, timestamps, and metadata. Both are OFL-licensed variable
  latin subsets served from the packaged asset routes; no font is loaded from another origin;
- a warm-neutral paper ground, white sheets only for things the operator edits or compares
  (inputs, the changes and review column, dialogs), and fine rules instead of card boxes;
- copying ink (`#4B3A8A`, the brand mark's colour) only for current navigation, focus, links,
  primary actions, and edited-field marks;
- green, amber, and red only as semantic colours, always as notations that pair a glyph shape
  (square, triangle, diamond, circle, ring, dash) with text and an underline;
- the ticket timeline puts event times in the margin and a state mark on the rule;
- controls at least 44 pixels high with 2-pixel corners;
- light and dark schemes from the same tokens (`prefers-color-scheme`); and
- bounded desktop content (80rem), with records reflowing into labelled entries on narrow screens.

The interface excludes gradients, textures, glass effects, glow, ornamental shadows,
decorative motion, third-party font services, icon libraries, fake metrics, generic card grids,
marketing headings, onboarding tours, and decorative archive imagery.

## Responsive and accessibility contract

The configuration review occupies a separate desktop column and flows after the fields on smaller
screens (below 75rem), so it never covers an input. Below 52rem the margin track folds into the
left rule and the running head wraps its routes onto a second row. Below 40rem record tables
become labelled entries. At 320 pixels the document must not overflow; any remaining wide
content scrolls inside labelled regions.

WCAG 2.2 AA is the release target. The maintained interface requires:

- semantic landmarks, headings, tables, lists, labels, and native controls;
- a working skip link and visible keyboard focus;
- non-color status cues and persistent error feedback;
- correct `lang` metadata for German and English;
- field errors connected with `aria-describedby` and `aria-invalid`;
- reduced-motion handling;
- keyboard operation at all supported widths; and
- safe reflow at 400 percent zoom.

Automated axe checks support this contract but do not replace manual assistive-technology, zoom,
contrast, and populated-data review.

## Source and build contract

| Path | Role |
| --- | --- |
| `src/chronikwerk/web/templates/admin/` | Server-rendered Jinja templates |
| `frontend/admin/css/*.css` | Modular administration styles |
| `frontend/admin.ts` and `frontend/admin/*.ts` | Dependency-free TypeScript behavior |
| `src/chronikwerk/web/static/admin/admin.css` | Assembled packaged stylesheet |
| `src/chronikwerk/web/static/admin/admin.js` | Bundled packaged JavaScript |
| `src/chronikwerk/web/admin/` | HTML forms, session handling, and JSON endpoints |

Run `make frontend-check` to type-check the TypeScript, rebuild temporary assets, and compare them
with the packaged CSS and JavaScript. Use `make frontend-update` only when the checked-in package
assets need to change.

Overview polling permits one status request at a time and refreshes immediately when a hidden tab
becomes visible. Session-expiry and network errors keep their existing feedback. The process caches
only the packaged CSS, JavaScript, brand-mark, and two font files' bytes; asset responses keep
`Cache-Control: no-store`.

Focused Chromium regressions run with `npm run test:browser` after `npx playwright install chromium`.
They use synthetic data and packaged product assets, alongside the isolated static demo. Checks
cover review invalidation, acknowledgement changes, delayed requests, visibility restoration, and
session/network errors at desktop and narrow widths.

## Static demonstration boundary

`demo/site/` is the maintained source for the GitHub Pages administration demo.
`make pages-demo-check` assembles `build/pages-demo/` with the packaged administration stylesheet and
brand mark, validates relative project-site links, and rejects backend, credential, and
external-network access from the artifact.

The demo is kept separate from the FastAPI templates and administration TypeScript. It
contains synthetic tickets, jobs, configuration values, and revisions; its interactions update
browser-local mock state only. Configuration review records a snapshot of the proposed changes, and
any later field edit invalidates that review. Staging requires a fresh, unchanged review and an
acknowledgement; removing the acknowledgement disables staging. The demo does not authenticate,
archive, generate documents, write configuration, call Zammad, or provide operational or release
evidence.

## Validation requirements

Changes to this surface require, as applicable:

- focused Python tests for route, state, and persistence behavior;
- TypeScript type checking and packaged-asset comparison;
- browser checks for both locales, keyboard entry, error recovery, and external-request boundaries;
- serious and critical axe checks;
- 390-pixel and 320-pixel reflow checks;
- screenshot manifest and source-hash validation for documentation previews; and
- manual Firefox, WebKit, screen-reader, 400 percent zoom, contrast, and populated-data review
  before any publication claim.

Documentation previews are current-template examples, not release or conformance evidence. Release
evidence must be produced from, and verified against, the same reviewed tag.
