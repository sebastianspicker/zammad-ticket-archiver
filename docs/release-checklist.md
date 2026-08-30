# Release Checklist

This project uses PEP 440 for Python package versions, SemVer-like prerelease Git tags,
and a Keep-a-Changelog style `CHANGELOG.md`.

## Release Modes

| Mode | Package version | Git tag |
| --- | --- | --- |
| alpha | `0.3.0aN` | `v0.3.0-alpha.N` |
| beta | `0.3.0bN` | `v0.3.0-beta.N` |
| stable | `0.3.0` | `v0.3.0` |
| release candidate | `0.3.0rcN` | `v0.3.0-rc.N` |

## Preconditions

- You are on `main`.
- The working tree is clean.
- CI is green for the commit being released.
- `make verify` is the first local release validation command: it enforces
  Python and TypeScript checks, complexity and duplication gates, the 600-line authored-source limit,
  focused unit/integration checks, public and code documentation checks,
  build and clean-wheel import, and production-image unsigned-render smoke.
- On hosts without Docker, use `make verify-core` for non-container diagnostics;
  image verification remains a separate release gate.
- Security dependency auditing remains a separate required fail-closed workflow
  covering base and signing dependency environments.
- Prerelease packaging is tag-only (`v*-alpha.*`, `v*-beta.*`, or `v*-rc.*`):
  reusable CI and security workflows
  must pass for the tagged SHA, and the release job downloads their `dist`
  artifact rather than rebuilding. Tag/version normalization and the exact
  nonempty `CHANGELOG.md` section are mandatory checks. The workflow creates a draft
  GitHub prerelease; a release owner publishes it only after the external and manual
  gates below are complete for the exact tagged artifact.
- Security workflow is green.
- The repository Docker workflow is build-only. If an image is part of the release,
  publish it through a separately reviewed workflow after the image gates pass.
- Target version and tag format are decided.

## Version and Changelog

1. Update `project.version` in `pyproject.toml` and `__version__` in
   `src/chronikwerk/_version.py`; the contract suite enforces equality.
2. Move `CHANGELOG.md` entries from `[Unreleased]` into the release section.
3. Leave an empty `[Unreleased]` section for future work.

## Local Validation

Run the repository-owned aggregate gates first:

```bash
make verify-core
make verify
```

The commands below document the constituent non-container checks for diagnosis:

```bash
python -m ruff check .
python -m mypy . --config-file pyproject.toml
python -m pytest -q
make frontend-check
make complexity
make duplication
make source-length-check
make docs-check
make code-docs-check
python -m build
```

The source-length gate scans maintained code and tests. The shipped administration CSS
and JavaScript bundles under `src/chronikwerk/web/static/admin/` are generated artifacts and
are the only exemptions from the 600-physical-line authored-source limit.

## Wheel Smoke Test

```bash
python -m venv /tmp/chronikwerk-release-venv
. /tmp/chronikwerk-release-venv/bin/activate
python -m pip install -U pip
python -m pip install dist/*.whl
python - <<'PY'
from chronikwerk.web.app import create_app
from chronikwerk.configuration.models import Settings

settings = Settings.from_mapping({
    "zammad": {"base_url": "https://example.invalid", "api_token": "x"},
    "storage": {"root": "/tmp"},
})
app = create_app(settings)
assert app.title == "chronikwerk"
print("wheel-import-ok", app.version)
PY
```

## Docker Smoke Test

Release evidence uses the production `Dockerfile`, not the development image.
The production-image smoke imports the packaged rendering/signing dependencies,
renders and inspects an unsigned accessible PDF, and verifies that the admin
control plane can initialize its durable state when the container root is read-only:

```bash
make production-image-smoke
```

Live Zammad workflow, tag/note projection, storage, and signed-document evidence
remain separate integration lanes and must not be inferred from this image smoke.

## Production Safety Checks

- Verify `/metrics` is protected when enabled.
- Verify `STORAGE__ROOT` is writable by the service identity.
- Execute one real archive run and confirm PDF plus sidecar.
- Confirm signing material and TSA settings in the target environment.
- Confirm logs and internal ticket notes do not expose secrets.
- Confirm admin routes return 404 when `admin.enabled=false`.
- Run keyboard-only and axe checks in German and English across Chromium, Firefox, and
  WebKit at desktop, 768px, 390px, and 400% zoom.
- Regenerate `docs/screenshots/*` from the clean tagged candidate and record the exact
  tag, commit, browser version, viewport, locale, UTC timestamp, and checksums in the
  screenshot manifest.
  - The checked-in screenshots are static documentation references.
  - Replace or supplement them with real browser captures for the frozen candidate and
    update the manifest before publication.
- Verify secure-cookie behavior behind the production TLS proxy and confirm no external
  browser asset requests.
- Run veraPDF 1.30.1 with the `ua1` profile on unsigned and signed representative PDFs;
  confirm title, language, A4 size, tagging, bookmarks, and DejaVu font embedding.
  The pinned local gate is `make pdf-ua-check PDF_FILES="unsigned.pdf signed.pdf"`.
- Complete VoiceOver/Safari, NVDA/Firefox, PDF reading-order, outline, contrast, reflow,
  and non-color-state manual checks. Renderer tagging alone is not conformance proof.

## Tag

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

For prereleases:

```bash
git tag vX.Y.Z-alpha.N  # or beta.N / rc.N
git push origin vX.Y.Z-alpha.N
```

## GitHub Release

1. Verify CI artifacts and checksums for the tag.
2. Verify the draft GitHub prerelease and its release notes.
3. Complete the browser, PDF/UA, live integration, and manual approval gates against the
   exact tagged artifact.
4. Publish the draft prerelease.

## Post-Release

- Add a fresh `[Unreleased]` section if needed.
- Update deployment manifests or image tags maintained outside this repo.
