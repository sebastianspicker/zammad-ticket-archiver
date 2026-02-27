# Release checklist

This project uses SemVer and a Keep-a-Changelog style `CHANGELOG.md`.

## Release modes

- Stable release example:
  - version: `0.2.0`
  - tag: `v0.2.0`
- RC release example:
  - version: `0.2.0rc1` (PEP440)
  - tag: `v0.2.0-rc.1`

## 0) Preconditions

- You are on the default branch (`main`) with a clean working tree.
- CI is green for the commit you intend to release.
- Security workflow (`.github/workflows/security.yml`) is green on `main`.
- You know the target version and tag format for the selected release mode.

## 1) Version + changelog

1. Pick the new version:
   - stable: `X.Y.Z`
   - RC: `X.Y.ZrcN`
2. Update `pyproject.toml`:
   - stable: `project.version = "X.Y.Z"`
   - RC: `project.version = "X.Y.ZrcN"`
3. Update `CHANGELOG.md`:
   - Move entries from `[Unreleased]` into:
     - stable: `## [X.Y.Z] - YYYY-MM-DD`
     - RC: `## [X.Y.Z-rc.N] - YYYY-MM-DD`
   - Leave an empty `[Unreleased]` section at the top for future changes

## 2) Local validation (before tagging)

Run:

```bash
python -m ruff check .
python -m ruff check src --select C901
python -m mypy . --config-file pyproject.toml
python -m pytest -q
make docs-check
python -m build
```

### Wheel install smoke test (clean venv)

```bash
python -m venv /tmp/zpa-release-venv
source /tmp/zpa-release-venv/bin/activate
python -m pip install -U pip
python -m pip install dist/*.whl
python - <<'PY'
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings

settings = Settings.from_mapping(
    {
        "zammad": {"base_url": "https://example.invalid", "api_token": "x"},
        "storage": {"root": "/tmp"},
        "hardening": {"webhook": {"allow_unsigned": True}},
    }
)
app = create_app(settings)
assert app.title == "zammad-pdf-archiver"
print("wheel-import-ok", app.version)
PY
```

### Docker smoke test

```bash
docker build -t zammad-pdf-archiver:local .
docker run --rm -p 8080:8080 \
  -e ZAMMAD_BASE_URL=https://example.invalid \
  -e ZAMMAD_API_TOKEN=x \
  -e STORAGE_ROOT=/tmp \
  -e HARDENING_WEBHOOK_ALLOW_UNSIGNED=true \
  zammad-pdf-archiver:local
```

In another terminal:

```bash
python - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=2).read().decode())
PY
```

### Docker Compose healthcheck smoke test

```bash
ARCHIVER_ENV_FILE=.env.example STORAGE_ROOT=/tmp/zammad-archive docker compose up -d --build
docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q zammad-pdf-archiver)"
ARCHIVER_ENV_FILE=.env.example STORAGE_ROOT=/tmp/zammad-archive docker compose down --remove-orphans
```

## 2.1) Deployment safety checks (production environment)

Perform these checks in the target runtime environment before promoting the release:

- `/metrics` exposure:
  - If metrics are enabled (`METRICS_ENABLED=true`), verify `GET /metrics` is reachable only from approved internal sources (reverse proxy ACL, firewall, or private network).
  - Verify unauthorised external access is blocked.
- CIFS/SMB write safety:
  - Confirm the service identity (UID/GID `10001` by default in container image) can create directories and files under `STORAGE_ROOT`.
  - Confirm `STORAGE_ROOT` is mounted read-write and has sufficient free space/quota.
  - Execute one real archive run and verify both files exist in target directory:
    - `.../<filename>.pdf`
    - `.../<filename>.pdf.json`
  - Simulate interruption during write (or repeat writes under load) and verify no partial files are left behind.
- Signing material sanity (when signing enabled):
  - Confirm `SIGNING_PFX_PATH` points to the expected mounted file and the password is correct.
  - Confirm a signed output validates with `scripts/ops/verify-pdf.sh`.

## 3) Tag

1. Create the tag locally:

```bash
git tag vX.Y.Z        # stable
git tag vX.Y.Z-rc.N   # RC
```

2. Push the tag:

```bash
git push origin vX.Y.Z
git push origin vX.Y.Z-rc.N
```

Expected:
- `.github/workflows/ci.yml` uploads `dist/` artifacts for the tag build.
- `.github/workflows/rc-release.yml` creates a GitHub prerelease for RC tags and attaches:
  - `dist/*.whl`
  - `dist/*.tar.gz`
  - `dist/SHA256SUMS`
- `.github/workflows/docker.yml` does not push GHCR images for RC tags.
- `.github/workflows/security.yml` continues running on PRs/`main` and on schedule (`pip-audit` policy).

## 4) GitHub Release

1. For RC tags (`vX.Y.Z-rc.N`), verify the prerelease was created by CI.
2. For stable tags (`vX.Y.Z`), create/verify GitHub Release as usual.
3. Use the matching `CHANGELOG.md` section as release notes.

## 5) Publish (optional)

For RC mode in this repo, keep publishing disabled by default:
- no PyPI upload
- no GHCR push

### PyPI

If publishing to PyPI, ensure credentials are configured and then:

```bash
python -m pip install -U twine
twine check dist/*
twine upload dist/*
```

### GHCR

If pushing Docker images to GHCR, configure repo secrets used by `.github/workflows/docker.yml`:
- `GHCR_TOKEN` (with `packages:write`)
- optional: `GHCR_USERNAME` (defaults to the GitHub actor)

## 6) Post-release

- Open a PR that adds any new items under `[Unreleased]`.
- If you maintain deployment manifests, bump the image tag to `vX.Y.Z`.
