# Architecture

Chronikwerk is a modular monolith. One FastAPI process accepts authenticated Zammad webhooks and
operator retries, records every request in a durable journal, runs bounded background capture work,
writes one sealed, timestamped package per ticket revision into a write-once archive store, and
projects the outcome back to Zammad.

## Runtime flow

```mermaid
flowchart LR
  Z[Zammad webhook] --> W[web]
  O[operator retry] --> W
  W --> J[(request journal)]
  J --> Q[operations scheduler]
  Q --> A[archiving pipeline]
  A --> ZG[Zammad gateway]
  A --> D[documents]
  A --> P[preservation]
  A --> S[storage]
  P --> K[(catalog)]
  C[configuration] --> X[composition root]
  X --> W
  X --> Q
  X --> A
```

`POST /ingest` and the retry endpoints return `202` only after the request is committed to the
journal. A worker then leases the oldest due request and the pipeline runs these steps:

1. Check that the ticket still carries the trigger tag (a forced retry skips this check) and apply
   `pdf:processing`.
2. Capture: collect the ticket, the articles, each email article's original message and every
   attachment's bytes into a local build area, comparing an inventory taken before and after.
3. Compute the source digest and the policy ID. If the catalog already holds a complete revision
   with the same ticket, source digest and policy ID, the request resolves to that revision and
   the pipeline continues at step 8.
4. Render the reading copies (PDF/A-2u and PDF/UA-1) from one snapshot; the PDF/A copy is PAdES
   signed when signing is enabled.
5. Assemble the package: `capture.json` and `manifest.json` with every file's size and SHA-256.
6. Seal the manifest (detached CMS signature) and timestamp the seal (RFC 3161), then check both
   against the trust anchors.
7. Commit to the write-once store: write the objects, read everything back, publish the revision
   with its final retention date, and insert the catalog row.
8. Apply `pdf:signed` and post the internal success note. This step is best effort: a failure
   leaves the revision complete and marks the Zammad update for a later retry.

A failure before step 7 completes posts an error note, applies `pdf:error` and is journaled as
transient (retried with back-off) or permanent. See [08 - Operations](08-operations.md) for the
error codes.

## Modules

| Module | Responsibility |
| --- | --- |
| `composition.py` | Converts validated configuration into narrow runtime options and builds the runtime objects (journal, catalog, archive store, scheduler, Zammad client, pipeline). |
| `archiving/` | The pipeline and its stages: capture, package assembly, sealed commit and resume, Zammad status projection, Zammad-to-snapshot mapping, tag state machine, notes, failure policy, retry, and archive paths. |
| `zammad/` | DTOs, policy-checked bounded transport, and the resource gateway. |
| `documents/` | Snapshot models, HTML sanitization, templates, PDF rendering, PAdES signing, and RFC 3161 timestamping. |
| `storage/` | Path layout, root-confined filesystem operations, and the write-once archive store (protocol and filesystem implementation). |
| `preservation/` | The package format: manifest model and parsing, package builder, revision IDs and key prefixes, content digests, manifest seal and token checks, offline verification, and the catalog. |
| `configuration/` | Settings model, precedence, validation, settings redaction, retention policy file, and managed non-secret revisions. |
| `operations/` | `TicketJob` envelopes, the request journal, journaled scheduling and shutdown, job history, logging, and metrics. |
| `web/` | FastAPI app, public routes, middleware, administration UI/API, templates, and generated static assets. |

## Dependency direction

```mermaid
flowchart LR
  W[web] --> C[configuration]
  W --> O[operations]
  O --> C
  Z[zammad] --> C
  S[storage] --> D[documents]
  P[preservation] --> D
  P --> S
  A[archiving] --> D
  A --> O
  A --> P
  A --> S
  A --> Z
  X[composition.py] -. assembles .-> W
  X -. assembles .-> A
```

Arrows point from a module to the modules it may import; they are permissions, not a claim that
every edge is present. `tests/unit/configuration/test_architecture.py` holds the authoritative map
and fails on any other edge or cycle.

- `composition.py` may import any concrete module it needs to assemble the process.
- `web` depends only on `configuration` and `operations`; it does not implement archival work.
- `archiving` is the only coordinator. `zammad`, `documents`, `storage`, and `preservation` never
  import it, and `documents` does not know Zammad wire formats or process mechanics; it depends
  only on shared leaves. `storage` imports document models only for types. `preservation` knows
  the package format, sealing, verification, and the catalog, but not Zammad or the journal.
  `archiving` receives narrow options built by `composition.py` and imports no `configuration`
  module.
- Pure archive policy (`path`, `notes`, `tags`, `error_policy`) imports no feature package.
- Shared leaves at the package root import no other chronikwerk module: `failures.py`
  (error classification), `outbound.py` (HTTP/DNS trust and bounded response helpers),
  `timestamps.py`, `redaction.py` (the single secret scrubber), `concurrency.py`
  (cancellation-safe thread offloading), `i18n.py`, and `_version.py`.
  Add another root module only for a concept with several independent owners.
- Cross-package imports of underscore-prefixed implementation modules are forbidden. The old
  `app`, `adapters`, `domain`, `config`, and `observability` package families are gone.

## State and consistency

Three kinds of state exist, and they have different authority.

- **The package is the record.** A published revision in the archive store, with its manifest,
  seal and timestamp token, is what Chronikwerk preserves. Nothing else is authoritative for what
  was archived.
- **The journal and the catalog are indexes and bookkeeping.** Both live in one SQLite database
  (`journal.path`, WAL, `synchronous=FULL`) on local disk, never on the archive share. The journal
  holds every request and its state; the catalog holds one row per complete revision. The catalog
  can be rebuilt from the manifests in the store (`ArchiveCatalog.rebuild_from_store`; a command
  line entry for it is not built yet). The journal is not rebuilt from the store, so it needs
  backup like any other state file.
- **Managed non-secret configuration revisions** are durable and become active after an external
  restart.

`202` means the request is durably recorded, not archived. Request states are `waiting`,
`collecting`, `verifying`, `complete`, `failed` and `coalesced`. At most one request per ticket is
active (`waiting`, `collecting` or `verifying`), enforced by a partial unique index. A request that
arrives for a ticket with an active request is stored as `coalesced` and is later merged into, or
promoted behind, the active one; a repeated `X-Zammad-Delivery` ID adds no row.

Resume rules after a restart, applied by `JournaledSchedulingService.start` before workers begin:

- `collecting` requests return to `waiting`. The interrupted attempt counts as a failure; a request
  interrupted more often than `capture.retry.max_attempts` allows ends in `failed`
  (`interrupted_repeatedly`). The local build area does not survive, so collection starts again.
- `verifying` requests resume at their recorded commit step. The pipeline reads back what the
  store holds under the revision's prefix. If the manifest, seal and token are all there, it
  checks them again, publishes the revision and completes the request. If the seal files were not
  stored yet, the staged revision is discarded and the request fails transiently with
  `commit_interrupted`; the next attempt captures again under a new revision ID.
- Transient failures return to `waiting` with exponential back-off; permanent failures and
  exhausted retries end in `failed`.

Archive publication and Zammad finalization are not one distributed transaction. If the revision is
complete but `pdf:signed` or the note could not be written, the request stays `complete` with
`zammad_status` `failed`; the revision remains authoritative and the Zammad update is not repeated
automatically yet (see [08 - Operations](08-operations.md)).

Admission counters, running tasks and admin sessions are process-local. Chronikwerk stays a single
process: SQLite is not shared between instances, and multiple instances can race. Job history reads
the journal, so it survives a restart.

The composition root builds the journal, the catalog, the archive store, one scheduler and one
shared Zammad client, and passes the scheduler and history to
`create_app(settings, *, scheduler=None, history=None, cleanup=None)`; without a scheduler the app is
read-only. Request-serving state (admin sessions, the managed configuration store, rate-limit
buckets) is created per application by `create_app`. The remaining module-level state is deliberate:
process-wide Prometheus metrics, the PDF signer cache, and the WeasyPrint render lock. The pipeline
borrows the shared client and never opens or closes it.

On shutdown the scheduler's `aclose()` stops accepting and leasing work, drains in-flight requests
for `admission.shutdown_timeout_seconds`, and then cancels the remaining ones. A cancelled request
keeps its journal state and is recovered by the next start. The web lifespan finally calls the
injected cleanup callback, which closes the shared Zammad client. See
[ADR 0009](adr/0009-composition-owned-runtime-state.md),
[ADR 0011](adr/0011-durable-request-journal.md),
[ADR 0012](adr/0012-sealed-preservation-package.md) and
[ADR 0013](adr/0013-write-once-archive-store.md).

## External boundaries

- **HTTP:** webhook, batch, retry, health, metrics, history, and administration contracts in
  [api.md](api.md).
- **Zammad:** fixed `/api/v1` resource shapes, token authentication, deny-by-default outbound
  transport policy, and workflow tags/notes.
- **Archive store:** the configured archive root, sanitized key prefixes, descriptor-relative
  symlink-resistant writes, create-only objects, and the package format in
  [03 - Data model](03-data-model.md).
- **Documents:** packaged templates, PDF/A-2u and PDF/UA-1 rendering, and an optional PAdES
  signature on the archival copy. The manifest seal and its RFC 3161 token are described in
  [06 - Signing and timestamping](06-signing-and-timestamp.md).
- **Deployment:** console scripts, Docker/Compose, and systemd interfaces documented in
  [deploy.md](deploy.md).

## Where code belongs

Put product rules, Zammad-to-snapshot mapping, tag transitions, and archive outcomes in `archiving`;
Zammad wire formats and calls in `zammad`; rendering and signing in `documents`; the package format,
sealing, verification and catalog in `preservation`; write-once object storage in `storage`;
settings and managed revisions in `configuration`; the journal and process mechanics in
`operations`; and HTTP/admin delivery in `web`. Shared code belongs in the module that
owns the concept, not in a generic helper package; new volatile state is built in `composition.py`
and passed in, not held in a module global.
