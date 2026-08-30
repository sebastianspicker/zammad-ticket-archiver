# `src/`

The Python package is a modular monolith assembled by `chronikwerk/composition.py`.

- `archiving/`: archive attempts, outcomes, workflow ordering, and product policy.
- `zammad/`: Zammad transport, DTOs, ticket gateway, and tag/note projection.
- `documents/`: snapshot mapping, sanitization, PDF templates, rendering, signing, and TSA.
- `storage/`: safe paths, root-confined filesystem access, audit records, and transactions.
- `configuration/`: validated settings, loading, redaction, and managed revisions.
- `operations/`: process-local scheduling, admission, dedupe, history, shutdown, and telemetry.
- `web/`: FastAPI routes, middleware, administration UI, templates, and generated assets.
- `composition.py`: converts configuration into narrow runtime options and wires dependencies.
- `runtime.py` and `asgi.py`: command-line and ASGI entry points.

The dependency rules are documented in `docs/01-architecture.md` and enforced by
`tests/unit/configuration/test_architecture.py`. Do not recreate the removed historical package
families or import underscore-prefixed implementation modules across package boundaries.
