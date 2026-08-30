# Chronikwerk

<p>
  <img src="docs/assets/brand/chronikwerk-lockup.svg" alt="Chronikwerk" width="360">
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/downloads/)
[![Release stage: alpha candidate](https://img.shields.io/badge/release-alpha%20candidate-orange.svg)](RELEASE_STATUS.md)

Chronikwerk archives Zammad tickets as PDFs with adjacent JSON audit records. It accepts
authenticated webhook requests, fetches the current ticket from Zammad, renders a stable
snapshot, writes the archive files, and records the outcome in Zammad.

Chronikwerk is an independent open-source project. It is not affiliated with or endorsed by
Zammad GmbH.

> [!IMPORTANT]
> Version `0.3.0a1` is an unfrozen public-alpha candidate. It has not been tagged or
> published. Interfaces, configuration, and storage behavior may change. Evaluate it with
> non-production data and review the [candidate status](RELEASE_STATUS.md) before deployment.

## Purpose and scope

The service implements this processing path:

```text
authenticated webhook
  -> fetch ticket, articles, and tags
  -> build an immutable snapshot
  -> render PDF
  -> optionally apply a PAdES signature and RFC3161 timestamp
  -> write PDF and JSON sidecar
  -> update Zammad tags and add an internal note
```

The archive is filesystem-based. A successful run creates a PDF and a sidecar with the PDF
checksum, archive path, article coverage, and signing status.

## Current capabilities

- Single-ticket ingestion at `POST /ingest`.
- Atomic batch admission for up to 100 items at `POST /ingest/batch`.
- Zammad ticket, article, and tag retrieval through the REST API.
- HTML sanitization and PDF rendering with Jinja2 and WeasyPrint.
- Optional PAdES signing and RFC3161 timestamping.
- Root-confined filesystem storage with symlink rejection and atomic replacement.
- Optional process-local job history and Prometheus metrics.
- Optional administration application for status, retries, and allowlisted non-secret
  configuration.
- JSON logging, request IDs, rate limiting, request-size limits, and shallow or deep health
  checks.

`202 Accepted` means that work entered the process-local admission queue. It does not mean
that the ticket was archived successfully.

## Limitations

- The supported alpha topology is one process and one service instance.
- Accepted background work, job history, replay detection, and administration sessions are
  not durable across process restarts.
- Abrupt termination can lose accepted work. A graceful stop uses the configured interval
  before async cancellation, then waits for cancellation-safe rendering, signing, and
  filesystem work to finish.
- Attachment metadata is recorded, but attachment binaries are not downloaded or archived.
- There is no durable queue, dead-letter queue, archive search, PDF preview, full-text index,
  retention engine, WORM policy engine, or encryption-at-rest manager.
- The administration application has no RBAC, SSO, secret editor, live reload, or restart
  control. It is disabled by default.
- Filesystem ACLs, backups, retention, encryption, and CIFS/SMB behavior remain operator
  responsibilities.
- The checked-in browser previews and tagged-PDF renderer are not accessibility or PDF/UA
  conformance evidence.

## Requirements

For Docker Compose operation:

- Linux host
- Docker Engine
- Docker Compose 2.24.0 or newer
- Zammad URL, API token, and webhook HMAC secret
- Archive directory writable by container user `10001`

For local development:

- Python 3.14 or newer
- Node.js 24 through 26
- System libraries required by WeasyPrint
- Docker for container checks

PDF/UA validation requires veraPDF 1.30.1.

## Installation

### Docker Compose

Create the local environment file from the checked-in template:

```bash
cp .env.example .env
```

Set the required values described in [Configuration](#configuration), then start the service:

```bash
docker compose up -d --build
docker compose ps
```

The default Compose mapping publishes the service on `127.0.0.1:8080`.

### Local development checkout

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
npm ci --ignore-scripts
```

Run the service with:

```bash
chronikwerk
```

The project does not currently publish an installation artifact. Install from a reviewed
source checkout or build a wheel with `make build`.

## Configuration

The minimum environment configuration is:

```bash
ZAMMAD__BASE_URL=https://zammad.example.com
ZAMMAD__API_TOKEN=replace-with-zammad-api-token
ZAMMAD__WEBHOOK_HMAC_SECRET=replace-with-at-least-32-random-characters
STORAGE__ROOT=/mnt/archive
```

The Zammad URL must be an HTTPS origin by default and cannot contain credentials, a path,
query parameters, or a fragment. Set
`HARDENING__TRANSPORT__ALLOW_INSECURE_HTTP=true` only to permit an HTTP Zammad origin in
a reviewed, isolated internal or test deployment. TLS certificate verification remains
mandatory whenever HTTPS is used. Private or loopback Zammad origins require the separate
transport-policy override documented in the [configuration reference](docs/config-reference.md).

Configuration is loaded in this order, from highest to lowest precedence:

1. Process environment.
2. Managed non-secret overlay in `admin.state_dir`.
3. YAML values.
4. `.env`.
5. File secrets.
6. Model defaults.

Nested environment keys use `__`. `ZAMMAD_ORIGIN` and `ZAMMAD_API_TOKEN` are supported
aliases for the corresponding nested keys. If both forms are present, their values must
agree.

YAML selection uses an explicit `--config` argument where the command supports one, then
`CONFIG_PATH`, then `config/config.yaml` if that file exists. Selecting a missing file is an
error. The complete example is [config/config.example.yaml](config/config.example.yaml).

Validate the effective configuration before startup:

```bash
chronikwerk-admin validate-config
chronikwerk-admin dump-config
```

`dump-config` redacts secret fields. See [docs/config-reference.md](docs/config-reference.md)
for all settings, defaults, aliases, feature dependencies, and validation rules.

## Usage

### Verify service health

The shallow check confirms that the application is serving requests:

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/healthz
```

The deep check creates and removes a temporary file under `storage.root`:

```bash
curl --fail --silent --show-error 'http://127.0.0.1:8080/healthz?deep=true'
```

The deep endpoint is unauthenticated. Expose it only on a trusted operator network.

### Configure Zammad

The default trigger tag is `pdf:sign`. The integration uses these ticket fields:

| Field | Purpose |
| --- | --- |
| `archive_path` | Relative directory segments under `storage.root`. |
| `archive_user_mode` | Placement mode: `owner`, `current_agent`, or `fixed`. |
| `archive_user` | Required when `archive_user_mode=fixed`. |

The webhook request must carry `X-Hub-Signature: sha256=<hex>`. The signature covers the raw
request body in compatibility mode. Strict delivery-ID mode also binds
`X-Zammad-Delivery`.

Follow [docs/02-zammad-setup.md](docs/02-zammad-setup.md) for the Zammad rule, webhook, and
smoke-test procedure. Request and response contracts are in [docs/api.md](docs/api.md).

### Interpret ticket state

The default tag transitions are:

- Start: remove `pdf:error` and the trigger tag, then add `pdf:processing`.
- Success: remove `pdf:processing`, `pdf:error`, and the trigger tag, then add `pdf:signed`.
- Failure: remove `pdf:processing` and `pdf:signed`, then add `pdf:error`.

Transient failures keep or restore the trigger tag. Permanent failures remove it.
`pdf:signed` records workflow success even when optional cryptographic signing is disabled.
Check the PDF and sidecar to determine whether a PAdES signature was applied.

## HTTP and command surface

| Method | Path | Authentication | Purpose |
| --- | --- | --- | --- |
| `POST` | `/ingest` | HMAC | Admit one webhook payload. |
| `POST` | `/ingest/batch` | HMAC | Admit a batch of up to 100 payloads. |
| `POST` | `/retry/{ticket_id}` | Bearer token | Force one reprocessing attempt. |
| `GET` | `/jobs/history` | History bearer token | Read optional process-local history. |
| `GET` | `/healthz` | None | Run a shallow or deep health check. |
| `GET` | `/metrics` | Metrics bearer token | Read optional Prometheus metrics. |
| Mixed | `/admin/*` | Session and CSRF token | Use the optional administration application. |
| `GET` | `/docs`, `/redoc`, `/openapi.json` | None | Read the FastAPI schema and interactive reference. |

Optional routes are registered only when their feature is enabled. FastAPI documentation
routes remain enabled in this candidate and load browser assets from external content
delivery networks.

Installed commands:

| Command | Purpose |
| --- | --- |
| `chronikwerk` | Validate configuration and run Uvicorn. |
| `chronikwerk-admin validate-config [--config PATH]` | Validate startup configuration. |
| `chronikwerk-admin dump-config` | Print redacted effective configuration. |
| `chronikwerk-admin list-config-revisions [--config PATH]` | List managed configuration revisions. |
| `chronikwerk-admin stage-config-rollback REVISION [--config PATH]` | Stage a prior non-secret revision for the next restart. |
| `uvicorn chronikwerk.asgi:app` | Import the ASGI application into an external server. |

## Repository structure

```text
config/              Example YAML configuration
docs/                Architecture, API, configuration, operation, and security references
examples/            Example webhook and ticket snapshot data
frontend/            Administration TypeScript and CSS sources
infra/systemd/       Optional Docker Compose systemd wrapper
scripts/ci/          Repository validation and image-smoke scripts
src/chronikwerk/     Python package, templates, and compiled administration assets
tests/               Focused unit and integration tests
```

The administration source files are under `frontend/`. The browser-served CSS and
JavaScript under `src/chronikwerk/web/static/admin/` are compiled project artifacts and are
checked against their sources by `make frontend-check`.

## Development workflow

Use the Makefile as the command contract:

```bash
make PYTHON=.venv/bin/python lint
make PYTHON=.venv/bin/python test-fast
make PYTHON=.venv/bin/python verify-core
```

Useful targets:

| Target | Purpose |
| --- | --- |
| `make lint` | Run Ruff checks. |
| `make format` | Rewrite Python files with Ruff format. |
| `make typecheck` | Run mypy over `src` and `tests`. |
| `make complexity` | Enforce the configured lizard limits. |
| `make duplication` | Run source and full-tree duplication checks. |
| `make source-length-check` | Enforce the 600-line limit on authored source files. |
| `make frontend-check` | Type-check frontend sources and compare compiled assets. |
| `make docs-check` | Validate required Markdown, local links, and screenshot metadata. |
| `make code-docs-check` | Validate maintained-code purpose text and public docstrings. |
| `make build` | Build the Python source distribution and wheel. |

`make format` changes files. Review its diff before including formatting changes in a pull
request.

## Testing

The Python test suites are divided by scope:

- `tests/unit`: isolated policy, configuration, document, operation, and storage behavior.
- `tests/contract`: HTTP and Zammad boundary contracts.
- `tests/integration`: composed archive and artifact behavior.

Run the narrow suite while editing:

```bash
make PYTHON=.venv/bin/python test-fast
```

Run the complete non-container gate:

```bash
make PYTHON=.venv/bin/python verify-core
```

Run container validation:

```bash
make PYTHON=.venv/bin/python verify
```

`make verify` adds the production-image smoke test. It does not include the separate
PDF/UA, dependency-security workflow, or manual release gates.
Those checks use:

```bash
make pdf-ua-check PDF_FILES="unsigned.pdf signed.pdf"
```

## Local demonstration and GitHub Pages

`make dev` starts the real FastAPI service in a hot-reload container. It still
requires reviewed local configuration, an archive path, and any Zammad or
signing integrations that the selected workflow enables; it is not a fixture
server.

For a non-operational visual preview, the maintained administration screenshots
are rendered from the real templates and CSS with synthetic local configuration:

```bash
make PYTHON=.venv/bin/python docs-check
```

Those images do not exercise authentication, JavaScript interaction, Zammad,
storage, signing, or PDF generation. GitHub Pages is not a product deployment
target because Chronikwerk requires a Python server, authenticated routes,
filesystem state, and external service integrations. The repository has no
Pages workflow or browser-only operational artifact.

## Deployment and operation

The maintained deployment path is [docker-compose.yml](docker-compose.yml). It:

- binds the service to loopback by default;
- mounts `./config` read-only;
- mounts archive storage read-write;
- persists administration state in the `admin-state` volume;
- uses a read-only container root filesystem and a `/tmp` tmpfs;
- drops Linux capabilities and enables `no-new-privileges`.

Place an authenticated reverse proxy or private ingress in front of the loopback binding.
Do not expose administration, deep health, FastAPI documentation, or metrics routes to
untrusted networks.

An optional systemd unit at
[infra/systemd/chronikwerk.service](infra/systemd/chronikwerk.service) wraps Docker Compose.
It expects the checkout at `/opt/chronikwerk` and an environment file at
`/etc/chronikwerk/chronikwerk.env`. It is not a native Uvicorn service.

See [docs/deploy.md](docs/deploy.md) for installation layout, signing mounts, start, update,
and rollback procedures. See [docs/08-operations.md](docs/08-operations.md) for monitoring,
shutdown behavior, retry handling, and on-call checks.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `403 forbidden` on ingestion | Recompute HMAC over the exact raw body and verify the configured secret. |
| `503 webhook_auth_not_configured` | Configure a non-placeholder HMAC secret of at least 32 characters and restart. |
| `400 missing_delivery_id` | Supply `X-Zammad-Delivery` or disable strict delivery-ID mode. |
| Ticket remains `pdf:processing` | Inspect logs by request ID, then verify Zammad access and archive writes. A restart may have interrupted process-local work. |
| Ticket has `pdf:error` | Inspect the internal note and logs, correct the permanent cause, then retrigger or use the authenticated retry endpoint. |
| Deep health fails | Verify the mounted storage path, container UID `10001`, free space, and filesystem behavior. |
| CIFS/SMB writes fail | Test atomic replacement, flush behavior, ownership, and permissions on the actual mount. |
| Optional route returns `404` | Enable the corresponding history, metrics, or administration feature and restart. |

More cases are documented in [docs/faq.md](docs/faq.md) and
[docs/08-operations.md](docs/08-operations.md).

## Security considerations

- Keep Zammad, bearer, signing, and timestamp credentials outside tracked files.
- Use HTTPS for Zammad and keep TLS verification enabled.
- Keep the default loopback bind unless a trusted ingress requires a different address.
- Restrict archive and administration-state filesystem permissions.
- Treat PDFs, sidecars, logs, configuration revisions, and screenshots as potentially
  sensitive operational data.
- Back up the PDF and its adjacent sidecar together.
- Verify signatures and timestamps independently before relying on them for legal or
  compliance workflows.
- Do not depend on in-memory replay detection or history as an audit ledger.

The threat model, trust boundaries, controls, and residual risks are documented in
[docs/09-security.md](docs/09-security.md). Report vulnerabilities according to
[SECURITY.md](SECURITY.md).

## Contributing

Install the development dependencies, add focused tests for behavior changes, and run the
narrowest relevant check followed by `make verify-core`. Deployment changes also require
`make verify`. Browser, PDF, and release changes have additional gates listed in
[CONTRIBUTING.md](CONTRIBUTING.md) and
[docs/release-checklist.md](docs/release-checklist.md).

Do not commit credentials, real ticket content, archive output, signing material,
administration revision state, local reports, or tool caches. Participation is governed by
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Documentation

- [Documentation index](docs/README.md)
- [Architecture](docs/01-architecture.md)
- [Zammad setup](docs/02-zammad-setup.md)
- [API reference](docs/api.md)
- [Configuration reference](docs/config-reference.md)
- [Deployment](docs/deploy.md)
- [Operations](docs/08-operations.md)
- [Security](docs/09-security.md)
- [Administration application](docs/admin-frontend.md)
- [Release status](RELEASE_STATUS.md)
- [Release checklist](docs/release-checklist.md)
