# Contributing

Thanks for contributing to **zammad-pdf-archiver**.

## Development workflow

1. Create a fork and a feature branch.
2. Implement the change (keep PRs small and focused).
3. Run local checks:
   - `make lint`
   - `make test`
4. Open a pull request with:
   - a clear problem statement / intent
   - any operational impact documented in `docs/08-operations.md` and/or `docs/09-security.md`

## Code style

- Python: `>=3.12` (see `pyproject.toml`)
- Linting: `ruff`
- Typing: `mypy` (optional but recommended for non-trivial changes)

## Releases

Goal: reproducible releases (sdist/wheel) and optionally Docker images.

See `docs/release-checklist.md` for the step-by-step release procedure.

1. Local checks:
   - `make lint`
   - `make test`
   - optional: `mypy src`
2. Version + changelog:
   - update `CHANGELOG.md`
   - update version in `pyproject.toml`
3. Tag:
   - `git tag vX.Y.Z`
   - `git push origin vX.Y.Z`
4. CI artifacts:
   - CI builds `sdist` + `wheel` as workflow artifacts.
   - Docker builds an image on pushes to `main` and tags `v*`.
     - Pushing to GHCR is optional and only happens if secrets are configured.
