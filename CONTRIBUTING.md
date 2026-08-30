# Contributing

## Scope

Keep each change focused on one behavior or documentation contract. Preserve the
single-process alpha model unless the change explicitly replaces it and includes migration,
failure, and deployment tests.

Changes that affect operation, configuration, storage, authentication, PDF output, or the
Zammad workflow must update the corresponding reference under `docs/`.

## Development setup

Requirements:

- Python 3.14 or newer
- Node.js 24 through 26
- WeasyPrint system libraries
- Docker for image and container checks

Create the development environment:

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
npm ci --ignore-scripts
```

CI and the container images currently use Python 3.14.6. CI uses Node.js 24.18.0.

## Workflow

1. Reproduce the behavior or identify the source contract.
2. Make the smallest change that fixes the underlying cause.
3. Add or update tests at the narrowest useful layer.
4. Run the focused test while editing.
5. Run `make PYTHON=.venv/bin/python verify-core`.
6. Run the additional checks required by the changed surface.
7. Update public documentation when a command, setting, endpoint, path, or operational
   behavior changes.

Use the Makefile targets rather than duplicating their command lines in new scripts.

## Code and documentation checks

| Check | Command |
| --- | --- |
| Ruff lint | `make PYTHON=.venv/bin/python lint` |
| Ruff formatting | `make PYTHON=.venv/bin/python format` |
| Mypy | `make PYTHON=.venv/bin/python typecheck` |
| Static and unit tests | `make PYTHON=.venv/bin/python test-fast` |
| All Python tests | `make PYTHON=.venv/bin/python test-all` |
| Complexity limits | `make complexity` |
| Duplication limits | `make duplication` |
| Authored source length | `make source-length-check` |
| Administration assets | `make frontend-check` |
| Documentation integrity | `make docs-check` |
| Maintained-code documentation | `make code-docs-check` |
| Non-container gate | `make PYTHON=.venv/bin/python verify-core` |
| Container gate | `make PYTHON=.venv/bin/python verify` |

`make format` changes Python files. Run it only when the resulting formatting diff belongs
to the change.

Maintained Python, TypeScript, JavaScript, CSS, HTML, shell, and MJS sources are limited to
600 physical lines per file. The assembled `src/chronikwerk/web/static/admin/admin.css` and
`admin.js` bundles are generated from `frontend/` and are the only source-length
exemptions.

PDF structure work requires representative signed and unsigned files:

```bash
make pdf-ua-check PDF_FILES="unsigned.pdf signed.pdf"
```

The PDF check requires veraPDF 1.30.1. Passing the repository checks does not replace manual
assistive-technology review or validation against the deployment filesystem and Zammad
instance.

## Pull requests

A pull request should state:

- the problem and the behavior being changed;
- the affected runtime, configuration, storage, or security boundary;
- tests and checks run, with exact results;
- checks not run and the reason;
- migration or rollback steps when persistent data or configuration changes;
- screenshots only when they come from the maintained capture command.

Do not include unrelated formatting or refactoring. Do not weaken validation, security
defaults, or failure handling to make a test pass.

## Sensitive and local files

Never commit:

- `.env` files or local YAML overrides;
- API, HMAC, bearer, PFX, or timestamp credentials;
- real ticket payloads, PDFs, sidecars, or production logs;
- administration state and configuration revisions;
- local reports, browser profiles, virtual environments, caches, or test output.

Use placeholders in examples. Keep operational evidence outside tracked public
documentation.

## Release changes

Release work follows [docs/release-checklist.md](docs/release-checklist.md). The required
local baseline is:

```bash
make verify
make code-docs-check
make pdf-ua-check PDF_FILES="unsigned.pdf signed.pdf"
```

The current workflows build Python distributions and a local container image. The Docker
workflow does not publish to a registry.

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
