# Administration screenshots

Documentation renders of the optional administration application. They come from the real
server-rendered templates with synthetic local data, so they contain no production endpoint,
credential, ticket content, or archive data.

These captures are documentation artifacts from an earlier presentation state. Their
process-local work labels do not supersede the current durable journal contract; see
[operations](../08-operations.md). They are not runtime acceptance evidence.

## Tour

**Overview (English)** — process health, capacity, recent failures, and configuration state, with
the volatility of process-local work called out at the top.

![English administration overview with a process-local warning and empty failure state](admin-overview.png)

**Jobs (English)** — a filterable record of recent processing events.

![English jobs list with a ticket filter](admin-jobs.png)

**Ticket history (English)** — one ticket's processing timeline and the acknowledged retry action.

![English ticket history timeline with a reprocess action](admin-ticket.png)

**Configuration (German)** — grouped, allowlisted non-secret values with inline provenance and a
review panel.

![German non-secret configuration editor showing value ownership](admin-configuration.png)

**Configuration revisions (German)** — active and staged revisions with a restore review.

![German configuration revisions with a staged revision and restore action](admin-revisions.png)

**Sign in (English)** — the access-token login page.

![English administration sign-in page](admin-login.png)

## What these are not

These images are documentation references only. They are not browser, accessibility, Zammad,
storage, signing, or PDF evidence, so full browser and assistive-technology evidence still has to
be captured separately before any publication claim. The interface remains disabled by default.

[manifest.json](manifest.json) records each image's checksum, locale, route, and dimensions, and
`make docs-check` validates it. The pages are rendered by
[`tests/browser/render_overview.py`](../../tests/browser/render_overview.py).
