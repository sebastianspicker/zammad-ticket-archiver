---
name: Chronikwerk
description: Auditable ticket archiving for Zammad.
colors:
  archive-ink: "#0B1A2E"
  archive-ink-deep: "#06101F"
  working-canvas: "#F7F9FC"
  surface: "#FFFFFF"
  quiet-surface: "#F2F5F9"
  process-blue: "#0B57D0"
  process-deep: "#0845A8"
  success-green: "#0A6B3A"
  warning-amber: "#8A5A0A"
  fault-red: "#A61F2C"
typography:
  working: "Avenir Next, Segoe UI, system-ui, sans-serif"
  utility: "SFMono-Regular, Consolas, Liberation Mono, monospace"
layout:
  content-maximum: "1280px"
  tablet-collapse: "899px"
  mobile-collapse: "639px"
  reflow-minimum: "320px"
rounded:
  control: "6px"
  panel: "10px"
accessibility:
  target: "WCAG 2.2 AA"
  minimum-control-height: "42px"
  focus-outline: "3px"
---

# Design system: Instrument Ledger

Chronikwerk is an operations product for people maintaining auditable ticket archives.
Its administration interface is a precise working surface, not a marketing site, consumer
dashboard, decorative archive, or certification surface.

The visual signature is an Instrument Ledger: a saturated process-blue field holds live
in-memory process and capacity state, while a cool white dossier holds archival evidence.
A full-width failure ledger records volatile faults. The contrast expresses two different
kinds of truth without manufacturing metrics.

## Principles

1. State operational truth, including volatility, staleness, staged state, and restart
   boundaries.
2. Keep expert identifiers and data visible without exposing secrets.
3. Make consequential actions explicit, specific, and recoverable.
4. Prefer native semantics and familiar controls over custom interaction.
5. Use product-scale typography and alignment instead of display marketing headings.
6. Preserve Zammad as the primary ticket workflow.

## Color and type

The canvas is a cool ice (`#F7F9FC`). White surfaces hold evidence and forms. Archive Ink
provides text and structural boundaries. Process Blue is reserved for the live instrument
field, current navigation marker, primary actions, and focus. Green, amber, and red are
semantic and always appear with text. Gradients, textures, glass, glow, and ornamental
shadows are not part of the system (tight 1–2px elevation is allowed).

One local sans-serif stack serves headings, controls, and data at a fixed product scale
(page titles ≈1.75rem). Compact uppercase utility text labels instrument and dossier panels.
The monospace stack is limited to paths, revisions, request IDs, timestamps, and metadata.

## Shell, layout, and density

The frosted sticky header contains product identity, an optional control-plane meta label,
three primary routes, locale, and sign out. Revision history remains inside Configuration.

Desktop content is bounded at 1280px. Overview pairs the live instrument and archive
dossier, followed by the failure ledger. Jobs uses one filter band and a true data table.
Ticket history uses a chronological event surface with a visibly separate destructive
action. Configuration uses grouped technical rows and a sticky review bar. Authentication
uses a two-part workspace that separates product context from credentials.

At 899px multi-column workspaces stack. At 639px filter controls, facts, and actions reflow.
At 320px the document never overflows; tables scroll inside labeled regions.

## Components and states

- Buttons are rectangular, at least 42px high, and use direct verb labels.
- Inputs have persistent labels, neutral boundaries, blue focus, and explicit disabled,
  invalid, and pending states.
- State pills distinguish Active, Staged (restart required), and Idle revisions.
- Classification tags distinguish Transient and Permanent failures with text + color.
- Status never relies on color alone.
- Capacity uses tabular figures and optional utilization bars.
- Empty states are factual and remain in the component where data would appear.
- Native dialog semantics provide reauthentication, with a restrained backdrop.
- Motion is limited to short control-state transitions; reduced motion removes them.

## Source layout

| Path | Role |
| --- | --- |
| `frontend/admin/css/*.css` | Modular Instrument Ledger styles (assembled) |
| `frontend/admin.ts` + `frontend/admin/*.ts` | Modular admin TypeScript (bundled) |
| `src/chronikwerk/web/static/admin/admin.css` | Assembled shipped CSS |
| `src/chronikwerk/web/static/admin/admin.js` | Bundled shipped JS |
| `src/chronikwerk/web/templates/admin/` | Server-rendered Jinja templates |

Build with `make frontend-update` (assembles CSS, bundles JS, copies into static).

## Content and boundaries

Use operator vocabulary and active verbs: Check storage now, Filter, Review changes, Stage
revision, Request reprocessing. Success repeats the action name. Errors state what failed and
the next recoverable step. Volatile, staged, active, and external are not interchangeable.
Do not imply legal certification, durable processing, archive completeness, or storage
health that the current evidence does not establish.

Do not add archive browsing, durable-queue controls, secret management, live reload, service
restart controls, fake metrics, charts, onboarding tours, external fonts, icon libraries,
animation dependencies, generic cards, or ornamental archive imagery.

The maintained product, audience, interaction, accessibility, and validation contract is in
[`docs/admin-frontend.md`](docs/admin-frontend.md).
