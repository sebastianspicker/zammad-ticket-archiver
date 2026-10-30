# Product contract

## Users

Chronikwerk supports operations and DevOps administrators, Zammad administrators,
support leads, compliance reviewers, and security or release maintainers. They work
mainly on desktop and laptop systems during deployment, routine operations, or
incident response, and they need dense, keyboard-accessible access to truthful service
state without exposing secrets. Help-desk staff continue to work in Zammad.

## Product purpose

Chronikwerk turns authenticated Zammad webhook events into durably journaled requests and,
for each ticket revision, one sealed package: the original ticket data, emails and
attachments, two reading copies, and a manifest with a detached seal and an RFC 3161
timestamp. Packages go into a write-once store and the outcome returns to Zammad as tags
and a note. Its administration application exposes health, journal history, safe retries,
and staged non-secret configuration. Success means operators can distinguish recorded
requests from completed archiving, see what a restart recovers and what is not built yet,
verify a package without Zammad or Chronikwerk, and produce complete German or English
reading copies. Chronikwerk makes no claim of legal or standards conformance.

## Brand personality

Dependable, institutional, precise. Chronikwerk is calm and utilitarian. It earns trust
through direct operational language, explicit boundaries, and evidence rather than
decoration or unsupported compliance claims.

## Anti-references

Do not imitate decorative SaaS dashboards. Avoid
gradients, glass panels, glowing status indicators, hover-lift cards, fake metrics,
decorative charts, raw JSON as the primary presentation, transient toasts as the only
feedback, placeholder-only labels, parchment nostalgia, institutional seals that imply
certification, and ornamental motion. Do not duplicate archive browsing, durable queues,
secret management, live reload, or infrastructure restarts in the UI.

## Design principles

1. State operational truth, including volatility, staleness, and restart boundaries.
2. Keep expert data visible and scannable without exposing secrets.
3. Make consequential actions explicit, specific, and recoverable.
4. Prefer native semantics and familiar controls over custom interaction.
5. Use clear navigation, readable field labels, and the folio mark for orientation.
6. Preserve the primary Zammad workflow rather than duplicating it.

## Accessibility and inclusion

WCAG 2.2 AA is a release target for the administration application and PDF/UA-1 is a
release target for archive PDFs. German (`de-DE`) and English (`en-GB`) are supported.
Keyboard operation, visible focus, 400% zoom and reflow, reduced motion, non-color state
cues, correct language metadata, semantic structure, and human screen-reader checks are
release requirements rather than current conformance claims.
