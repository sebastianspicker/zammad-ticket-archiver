# Operations

Runbook for starting, observing, troubleshooting, and recovering the service. Deployment
preparation lives in [deploy.md](deploy.md).

## Endpoint semantics

- `POST /ingest` returns `202` after recording one payload in the journal; processing runs in the
  background.
- `POST /ingest/batch` returns `202` after recording a batch in one transaction.
- `POST /retry/{ticket_id}` returns `202` after recording one forced retry.
- `GET /jobs/history` returns authenticated history read from the journal.
- `GET /healthz` reports liveness/status, with an optional `?deep=true` storage check.
- `GET /metrics` exposes Prometheus metrics when enabled.
- The admin application is an optional, session-authenticated surface for overview, journal
  history, retry, and staged non-secret configuration.

`202` means the request is durably recorded, not archived. Confirm completion from the final tags,
the internal ticket note (revision number, revision ID, manifest SHA-256, key prefix, coverage,
`retain_until`, `retention_enforced`), the history, and the package in the store.

When the journal already holds `admission.max_pending` waiting requests and the ticket has no
active request, ingest returns `503` with `code=job_capacity_exhausted` and `Retry-After: 1`, and
nothing was recorded. Batch requests are rejected as a whole when their items do not fit. The cap
counts every `waiting` row, including requests that wait out a retry back-off, but not requests a
worker is collecting or verifying (at most `admission.max_running`) and not `coalesced` ones. Size
`admission.max_pending` for the expected burst plus the retries that may be backing off at once.

For webhook requests the journal also records the Zammad login that triggered the delivery
(`trigger_login`): `user.login` from the payload, otherwise `ticket.updated_by.login`; `-` and
blank values are stored as absent, and the value is cut to 255 characters. Retries carry none.

## Request states

Each request in the journal has one state. The history endpoints show them as `accepted`,
`running`, `processed`, `failed`, `skipped` and `coalesced`.

| State | History status | Meaning |
| --- | --- | --- |
| `waiting` | `accepted` | Recorded and waiting for a worker, or waiting out a retry back-off. |
| `collecting` | `running` | A worker is capturing the ticket and building the package. |
| `verifying` | `running` | The package is sealed and being committed to the store. |
| `complete` | `processed` | A complete revision exists for the request. |
| `failed` | `failed` | The request ended with an error code and will not be retried automatically. |
| `failed` (`not_triggered`) | `skipped` | The ticket did not carry the trigger tag; nothing was archived. |
| `coalesced` | `coalesced` | Another request for the same ticket was active; this one waits behind it or was merged into it. |

At most one request per ticket is active (`waiting`, `collecting` or `verifying`). A request that
arrives meanwhile is stored as `coalesced`. When the active request finishes, a coalesced duplicate
that carries no extra data resolves to the same revision, and otherwise the oldest coalesced
request is promoted to `waiting`. At most 20 coalesced requests are kept per ticket; the active
capture sees the newest ticket state anyway. A repeated `X-Zammad-Delivery` ID answers `202` and
adds no row.

A `complete` request also carries a Zammad status: `pending`, `done` or `failed`. It describes the
`pdf:signed` tag and success note. A `failed` request has Zammad status `pending` until its error
note and `pdf:error` tag were posted, which matters for failures recorded outside the archive
pipeline (`internal_error` from the scheduler, `interrupted_repeatedly` after restarts).

## Start and stop

```bash
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env up -d --build
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env ps
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env logs -f
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env down
```

Shutdown stops accepting and leasing work before awaiting in-flight requests. After the configured
grace period, they receive cancellation; blocking normalization, rendering, signing, and filesystem
work still finishes before its request releases resources. A cancelled request keeps its journal
state and is recovered by the next start (see "Restart recovery"). The shared Zammad client closes
after those finalizers, so total shutdown can exceed the grace period.

Health check:

```bash
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env exec -T chronikwerk \
  python - <<'PY'
import os
import urllib.request

port = os.getenv("SERVER__PORT", "8080")
for path in ("/healthz", "/healthz?deep=true"):
    response = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2)
    print(path, response.status)
PY
```

## Update and rollback

Update:

```bash
cd /opt/chronikwerk
sudo git fetch --tags --prune
sudo git checkout <new-release-tag>
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env up -d --build
```

Update from a clean tag or a versioned image. Never sync a developer working tree into `/opt`:
ignored local configuration, credentials, archives, evidence, and tool state must stay outside the
deployment source tree.

Rollback:

```bash
cd /opt/chronikwerk
sudo git checkout <known-good-commit-or-tag>
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env up -d --build
```

For no-build rollbacks, publish versioned images and pin compose `image:` tags.

Managed configuration stays restart-only. The UI labels a newly staged revision as inactive until
the process is restarted externally. If a staged revision prevents web startup, use the offline CLI
against the same external configuration and state directory:

```bash
chronikwerk-admin list-config-revisions
chronikwerk-admin stage-config-rollback <full-revision-hash>
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env restart
```

## Optional systemd wrapper

```bash
sudo install -m 0644 infra/systemd/chronikwerk.service /etc/systemd/system/chronikwerk.service
sudo systemctl daemon-reload
sudo systemctl enable --now chronikwerk.service
```

The unit assumes `/opt/chronikwerk`; adjust `WorkingDirectory=` if your deployment path differs.

```bash
sudo systemctl status chronikwerk.service
sudo journalctl -u chronikwerk.service -f
```

## Observability

Primary signals:

- structured service logs (`request_id`, `ticket_id`, optional `delivery_id`)
- ticket internal notes
- ticket tags (`pdf:sign`, `pdf:processing`, `pdf:signed`, `pdf:error`)
- `GET /jobs/history`
- optional Prometheus metrics (see below)

Prometheus metrics, when enabled:

| Metric | Type | Meaning |
| --- | --- | --- |
| `processed_total` | counter | Requests the archive pipeline completed (new or resumed revisions). |
| `skipped_total{reason}` | counter | Skipped requests; the pipeline uses `reason="not_triggered"`. |
| `failed_total` | counter | Pipeline attempts that failed, transiently or permanently. |
| `render_seconds`, `sign_seconds` | histogram | PDF rendering and signing time. |
| `total_seconds` | histogram | Defined, but not updated by the journaled pipeline. |
| `admission_pending`, `admission_running` | gauge | Journal requests waiting for a worker and currently collecting or verifying. |
| `admission_rejected_total` | counter | Requests rejected because admission capacity was exhausted. |
| `chronikwerk_requests{state}` | gauge | Journal rows per state, read from the journal on every scrape. |
| `chronikwerk_oldest_waiting_seconds` | gauge | Age of the oldest `waiting` request (0 when none waits), read on every scrape. |

Capture-failure, seal, TSA and fixity metrics are not built yet.

Treat logs and internal ticket notes as sensitive operational data — they can contain delivery
identifiers and absolute archive paths.

## Journal and catalog

The request journal and the catalog share one SQLite database at `journal.path` (default
`/var/lib/chronikwerk/journal.sqlite3`), opened in WAL mode with `synchronous=FULL`. The file is
created with mode `0600`; its directory must exist and must not be world-writable without the sticky
bit.

- **Keep it on local disk.** Never place it on the archive share or any network file system:
  SQLite locking and durability are not reliable there.
- **Back it up** like any state file, together with its `-wal` and `-shm` companions, for example
  with the SQLite online backup API or by stopping the service first. The archive store is the
  authoritative record and the catalog can be rebuilt from it, but the journal (requests, error
  codes, Zammad status) cannot be rebuilt.
- **Single instance.** Do not run two Chronikwerk processes against one journal.

## Maintenance task

The scheduler runs a maintenance pass at start and then every 15 minutes. Each step is isolated (a
failing step is logged and the others still run), a pass never overlaps with itself, and rows a
worker is still handling are skipped:

- **Journal retention.** `complete` and `failed` rows (and the coalesced rows that point at them)
  not updated for 90 days are deleted.
- **Housekeeping.** Abandoned staging directories in the archive store and capture build areas
  (`chronikwerk-capture-*` directories directly in `capture.temp_dir`) untouched for one day are
  removed. Only real directories with that name pattern are removed, never symbolic links or other
  entries.
- **Zammad updates.** `complete` requests with Zammad status `pending` or `failed` get their
  `pdf:signed` tag and success note again.
- **Failure reports.** `failed` requests with Zammad status `pending` get their error note and
  `pdf:error` tag, then Zammad status `done`.

The local steps (orphan recovery, journal retention, housekeeping) run before the two Zammad
steps, so an unreachable Zammad does not delay them. A pending Zammad update or failure report is
skipped when a newer `complete` or `failed` request of the same ticket exists (a `not_triggered`
skip does not count): the newer request reports its own outcome.

## Restart recovery

On start the service recovers the journal in the background, before workers begin; the HTTP
endpoints already accept requests meanwhile:

- `collecting` requests return to `waiting`. The interrupted attempt counts toward
  `capture.retry.max_attempts`; a request interrupted more often than that ends in `failed` with
  `interrupted_repeatedly`.
- `verifying` requests resume at their recorded commit step. The interrupted attempt counts here
  too, with the same `interrupted_repeatedly` limit. If the sealed manifest, seal and token
  are in the store, they are checked again and the revision is published and cataloged. Otherwise
  the staged revision is discarded and the request fails transiently with `commit_interrupted`; the
  retry captures the ticket again under a new revision ID.
- A ticket that a restart left in `pdf:processing` is still eligible: the trigger tag is not
  required when `pdf:processing` is present.

Nothing is lost for a request that returned `202`. Work that was accepted but not yet recorded when
the process died was never acknowledged, so Zammad sees a failed delivery.

## Retries and back-off

A transient failure puts the request back to `waiting` with `next_attempt_at = now +
min(30 s * 2^attempts, capture.retry.max_delay_seconds)`. After `capture.retry.max_attempts`
automatic retries (default 5) the next failure ends in `failed`. A permanent failure ends in
`failed` at once. Failed requests are not retried automatically; use `POST /retry/{ticket_id}` or
re-apply the trigger tag after fixing the cause. A transient failure keeps the trigger tag on the
ticket and a permanent one removes it; both apply `pdf:error` and post an error note.

Internal success/error notes are a non-idempotent Zammad `POST`, attempted once per processing pass;
transport and 5xx failures are not retried automatically, to avoid duplicate notes.

## Error codes

Every failure carries a code in the journal and the Zammad error note. Transient failures are
retried automatically with back-off; permanent ones are not.

| Code | Class | Operator action |
| --- | --- | --- |
| `record_class_missing` | permanent | Set the record class field to a class in the retention policy file, then re-apply the trigger tag. |
| `inconsistent_source` | permanent | The ticket changed during every capture attempt. Wait until editing stops, then re-apply the trigger tag. |
| `attachment_too_large` | permanent | An attachment exceeds `capture.max_attachment_bytes`. Raise the limit or ask the custodian, then retry. |
| `package_too_large` | permanent | The package exceeds `capture.max_package_bytes`. Raise the limit or ask the custodian, then retry. |
| `temp_space_exceeded` | transient | The build area passed `capture.max_temp_bytes`. Check free space on `capture.temp_dir`. |
| `seal_failed` | permanent | The manifest could not be sealed or the seal failed its check. Check the seal PFX, its password and the trust anchors, then retry. |
| `timestamp_failed` | transient | The TSA was unreachable or its token failed the check. Check TSA reachability, credentials and trust anchors. An invalid token is permanent: fix the cause, then retry. |
| `validation_unavailable` | transient | Revocation data could not be fetched. Reserved: validation material is not collected yet. |
| `store_write_failed` | transient | The store refused or failed a write. Check availability, permissions and capacity of `storage.root`. |
| `store_conflict` | permanent | The store already holds different content under this revision key. Ask the custodian to investigate; do not overwrite it. |
| `verification_failed` | permanent | What the store returned differs from the sealed manifest. Check the store's integrity with the custodian, then retry. |
| `retention_not_confirmed` | permanent | The store did not confirm the final retention date. Reserved for stores that confirm retention; the filesystem store records but does not confirm it. |
| `commit_interrupted` | transient | A restart interrupted the commit before the seal files were stored. No action: the retry captures again under a new revision ID. |
| `not_triggered` | permanent | The ticket did not carry the trigger tag when the request ran. Apply the trigger tag to archive the ticket. |
| `internal_error` | permanent | An unexpected error stopped the capture. Check the service log for the request ID, then retry. |

Failures without a preservation code (Zammad, HTTP, filesystem, path policy) are recorded as
`transient_error` or `permanent_error` with the established hints in the error note.

`not_triggered` does not touch the ticket and no error note is posted. A request that the scheduler
finds left in flight by a processor that returned unexpectedly is recorded with `internal_error`
and retried with back-off.

## Consistency of archive and Zammad status

Archive storage commits before terminal Zammad updates. If a revision exists but the ticket is
`pdf:error` or stuck in `pdf:processing`, find the request in the history. A `complete` request with
Zammad status `failed` or `pending` means the revision is stored and only the `pdf:signed` tag or
the note is missing. Preserve the revision, clear a stale `pdf:processing` only after confirming no
request is still running, and re-apply the trigger tag or call `POST /retry/{ticket_id}`: when the
ticket content is unchanged, the new request resolves to the existing revision and repeats the
Zammad update without creating a new package. The maintenance task also repeats pending or
failed Zammad updates every 15 minutes. If Zammad answers that the ticket no longer exists, the
update ends as done (the revision stays stored). Other failing updates are repeated until the
journal purge removes the request, 90 days after its first failure.

## Verify a package

```bash
chronikwerk-admin verify --path <package directory> --trust-anchors <trust-anchors.pem>
```

The command checks the manifest, every file's size and hash, the seal, the timestamp token and a
PAdES signature if the archival copy has one. It needs no configuration or network access and runs
on a copied directory. Exit code `0` means every check passed. `--report <file>` writes a JSON
report; `--expected-signer <sha256>` pins the seal certificate. Details and the equivalent OpenSSL
commands are in [06 - Signing and timestamping](06-signing-and-timestamp.md).

## Catalog rebuild

The catalog is an index. `ArchiveCatalog.rebuild_from_store` recreates the `revisions` rows from the
sealed manifests under each record class prefix, keeps rows whose revisions are absent from the
store and reports them as skipped, and skips manifests that conflict with an existing row. A
`chronikwerk-admin` command for it is not built yet. Holds, dispositions and access events are not
recreated by a rebuild.

## Not built yet

These parts of the preservation design do not exist in this build. Plan around their absence.

- **S3 Object Lock store.** Only the filesystem store exists; `storage.backend=s3` has settings but
  no implementation.
- **Holds, expiry and disposal.** There is no `chronikwerk-admin` command for legal holds, expiry
  listing or disposal, and nothing deletes expired revisions. The catalog has tables for them.
- **Reconciliation and fixity jobs.** Nothing searches Zammad for tagged tickets without a request,
  alerts on stuck requests or orphaned prefixes, extends retention after a ticket closes, or
  re-hashes stored revisions. Only the maintenance task above runs on a schedule.
- **Alerts webhook.** `alerts.webhook_url` is a setting only; no alert is sent.
- **Mail evidence.** `attach-mail-evidence`, the `events/` files and the `mail_evidence` request
  source are not built.
- **Evidence renewal.** No renewal timestamps are requested; `renewal.lead_days` has no effect.
- **Validation material.** Revocation data and certificates are not collected under
  `evidence/validation/`; B-LT embedding in the PDF is not implemented.
- **Other `chronikwerk-admin` commands.** Only `verify` (by `--path`) exists beyond the
  configuration commands: no `catalog`, `fetch`, `verify --revision`, `reconcile` or `fixity`.

## Reprocessing workflow

1. Read the latest Chronikwerk ticket note and the request's error code.
2. Fix the root cause: storage, credentials, network, signing, TSA, record class, or payload.
3. Remove a stale `pdf:processing` if present.
4. Ensure the trigger tag is present, unless you are using `POST /retry/{ticket_id}`.
5. `pdf:signed` does not block a new request. A capture of an unchanged ticket resolves to its
   existing revision; a changed ticket gets a new revision.
6. Trigger a fresh Zammad update or call `POST /retry/{ticket_id}`.
7. Confirm `pdf:signed` or a new `pdf:error` note.

## Troubleshooting

### `403 forbidden` on `/ingest`

Check that `ZAMMAD__WEBHOOK_HMAC_SECRET` matches, that the `X-Hub-Signature` header is present, and
that no proxy transforms the request body after signing.

### `503 webhook_auth_not_configured`

Normal startup rejects a missing webhook secret. Set `ZAMMAD__WEBHOOK_HMAC_SECRET` to a random,
non-placeholder value of at least 32 characters.

### `400 missing_delivery_id`

`hardening.webhook.require_delivery_id=true` is enabled and the request lacks `X-Zammad-Delivery`.

### Ticket ends in `pdf:error`

Read the error code in the note or in `/jobs/history` and follow the error code table above. Also
check:

- storage mount path, permissions, free space, and quota
- free space on `capture.temp_dir` and the journal directory
- Zammad API token permissions
- seal and signing PFX paths and passwords, and the trust anchors
- TSA URL, credentials, and trust
- the ticket's record class against the retention policy file
- `pdf.max_articles` and `pdf.article_limit_mode` for large tickets, and `capture.max_*` for large
  attachments

### Ticket remains `pdf:processing`

The request may be waiting for a retry or still running. Inspect `/jobs/history` and the logs: a
`waiting` request with a back-off, or a `collecting` or `verifying` one, resolves on its own, also
after a restart. If the request is `complete`, treat it as a post-commit finalization failure and
follow "Consistency of archive and Zammad status" before running the reprocessing workflow.

## On-call fast triage

1. Did `/ingest` return `202`?
2. What is the request's state and error code in the history?
3. What is the current ticket tag state, and what does the latest internal note say?
4. Is the archive store writable, and is `capture.temp_dir` not full?
5. Could the replay have been a duplicate delivery ID or coalesced into an active request?
6. Does `GET /healthz?deep=true` report writable storage?

## Release safety reminders

- Protect `/metrics` with a bearer token or network policy when enabled.
- Treat CIFS/SMB durability as a storage-system contract, not an app guarantee.
- Validate signing and timestamp trust in the target environment.
- Run the [release checklist](release-checklist.md) before publication.
