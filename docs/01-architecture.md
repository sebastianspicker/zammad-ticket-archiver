# 01 - Architecture

`zammad-pdf-archiver` is a single API service with two execution modes:
- in-process background worker (`workflow.execution_backend=inprocess`)
- Redis queue worker (`workflow.execution_backend=redis_queue`)

## Runtime Flow

```mermaid
sequenceDiagram
  autonumber
  participant Z as Zammad
  participant I as POST /ingest
  participant D as Dispatcher
  participant R as Redis Queue
  participant W as Queue Worker
  participant J as process_ticket
  participant ZA as Zammad Adapter
  participant SN as Snapshot Builder
  participant PDF as PDF Renderer
  participant SIG as Signing Adapter
  participant TSA as TSA (RFC3161)
  participant ST as Storage Adapter
  participant H as History Stream

  Z->>I: Webhook JSON (+ optional headers)
  I-->>Z: 202 Accepted
  I->>D: dispatch job

  alt workflow.execution_backend == inprocess
    D->>J: schedule background task
  else workflow.execution_backend == redis_queue
    D->>R: enqueue (XADD)
    W->>R: consume (XREADGROUP)
    W->>J: process queued ticket
  end

  J->>ZA: get_ticket + list_tags
  J->>ZA: apply_processing tags
  J->>ZA: list_articles
  J->>SN: build snapshot
  J->>PDF: render_pdf(snapshot)

  alt signing.enabled
    J->>SIG: sign_pdf(pdf_bytes)
    opt signing.timestamp.enabled
      SIG->>TSA: request timestamp token
      TSA-->>SIG: timestamp response
    end
  end

  J->>ST: write PDF
  J->>ST: write audit sidecar JSON
  J->>ZA: create internal note
  J->>ZA: apply_done/apply_error tags
  opt history enabled
    J->>H: record processed/failed/skipped event
  end
```

## Tag State Machine

```mermaid
stateDiagram-v2
  [*] --> Idle
  Idle --> SignRequested: Trigger tag added (default pdf:sign)
  SignRequested --> Processing: apply_processing()
  Processing --> Signed: Success -> apply_done()
  Processing --> ErrorTransient: Transient failure -> apply_error(keep_trigger=true)
  Processing --> ErrorPermanent: Permanent failure -> apply_error(keep_trigger=false)
  ErrorTransient --> SignRequested: Retry by ticket update/macro
  ErrorPermanent --> SignRequested: Operator fix plus re-add trigger
```

State details:
- `pdf:signed` is terminal for automatic processing (`should_process` returns false).
- `pdf:processing` is best-effort cleaned in both success and error paths.
- `pdf:error` is set on failures.

## Module Boundaries

### Ingress and app wiring

Code:
- `src/zammad_pdf_archiver/app/server.py`
- `src/zammad_pdf_archiver/app/routes/ingest.py`
- `src/zammad_pdf_archiver/app/middleware/`
- `src/zammad_pdf_archiver/app/jobs/process_ticket.py`

Responsibilities:
- expose HTTP endpoints
- enforce ingress hardening (HMAC, body size, rate limit, request ID)
- schedule asynchronous background processing

### Zammad adapter

Code:
- `src/zammad_pdf_archiver/adapters/zammad/client.py`
- `src/zammad_pdf_archiver/adapters/zammad/models.py`

Responsibilities:
- fetch ticket, tags, and articles
- create internal ticket notes
- add/remove tags

### Snapshot adapter

Code:
- `src/zammad_pdf_archiver/adapters/snapshot/build_snapshot.py`
- `src/zammad_pdf_archiver/domain/snapshot_models.py`

Responsibilities:
- normalize Zammad payloads into stable snapshot schema
- sanitize article HTML
- derive fallback text and sort articles deterministically

### PDF adapter

Code:
- `src/zammad_pdf_archiver/adapters/pdf/template_engine.py`
- `src/zammad_pdf_archiver/adapters/pdf/render_pdf.py`
- templates: `src/zammad_pdf_archiver/templates/`

Responsibilities:
- render HTML from snapshot
- collect template CSS
- generate PDF bytes via WeasyPrint

### Signing adapter

Code:
- `src/zammad_pdf_archiver/adapters/signing/sign_pdf.py`
- `src/zammad_pdf_archiver/adapters/signing/tsa_rfc3161.py`

Responsibilities:
- load PKCS#12/PFX signing material
- apply PAdES signature
- optionally call TSA and embed timestamp token

### Storage adapter

Code:
- `src/zammad_pdf_archiver/adapters/storage/layout.py`
- `src/zammad_pdf_archiver/adapters/storage/fs_storage.py`
- `src/zammad_pdf_archiver/domain/path_policy.py`

Responsibilities:
- build deterministic target paths/filenames
- enforce path policy and root containment
- write files atomically or direct (configurable)

### Domain layer

Code:
- `src/zammad_pdf_archiver/domain/state_machine.py`
- `src/zammad_pdf_archiver/domain/errors.py`
- `src/zammad_pdf_archiver/domain/idempotency.py`
- `src/zammad_pdf_archiver/domain/audit.py`

Responsibilities:
- tag transition policy
- transient vs permanent error semantics
- in-memory TTL dedupe by delivery ID
- audit sidecar checksum and metadata model

## Related ADRs

- [`adr/0001-tag-vs-fields.md`](adr/0001-tag-vs-fields.md)
- [`adr/0002-storage-approach.md`](adr/0002-storage-approach.md)
- [`adr/0003-signature-timestamp.md`](adr/0003-signature-timestamp.md)
