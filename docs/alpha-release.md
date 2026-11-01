# Public alpha candidate

The current candidate is `0.3.0a1`, intended for the tag `v0.3.0-alpha.1`. It is for evaluation,
integration testing, and early operator feedback — not production use, and not a
compliance-certified archive solution.

After candidate review and publication approval, manually dispatch the Prerelease workflow
from main with an existing prerelease tag. It runs CI and security on that exact tag commit
and prepares a draft GitHub prerelease; pushing a tag alone does not publish anything. The release owner publishes that draft only after the remaining external and manual
[release-checklist](release-checklist.md) gates pass against the tagged artifacts. The candidate
is still unfrozen and unpublished; see [RELEASE_STATUS.md](../RELEASE_STATUS.md).

## What to expect

- One FastAPI process records requests in a durable journal and runs bounded background work.
- Ticket source records, original email where available and attachment bytes are captured with
  coverage checks, together with PDF/A and PDF/UA reading copies.
- The manifest is sealed and timestamped; optional PAdES signing applies to the archival copy.
  Completion requires external signing/trust material, a TSA and a retention policy.
- The disabled-by-default administration application exposes authenticated journal history,
  retries and staged non-secret configuration; admin sessions remain process-local.
- Compose binds to loopback by default; external access needs a trusted TLS reverse proxy.

## Alpha limitations

- `202 Accepted` confirms durable admission, not completed preservation. Protect and back up the
  local journal; do not run multiple service instances against it.
- Compatibility-mode HMAC authenticates the body, not its delivery ID. Strict ID signing needs
  sender support for the documented canonical format; restrict ingest to trusted sources.
- Deep health checks write to storage without authentication, and the schema/documentation
  routes are unauthenticated. Restrict these surfaces at the network edge.
- The filesystem store is implemented; S3 Object Lock, hold/disposal CLI, scheduled fixity and
  reconciliation, evidence renewal and validation-material collection remain open. See
  [operations](08-operations.md#not-built-yet) for the current list.
- Admin sessions are volatile; journal history is persistent. Managed configuration requires
  an external restart before taking effect. RBAC, SSO and secret management are not provided.
- PDF tagging and browser checks alone do not establish PDF/UA or WCAG conformance. Independent
  validation and assistive-technology checks remain release gates.
- Live Zammad, SMB/CIFS, signing, TSA, proxy and recovery behavior need target qualification.

## Administration preview

The [screenshot tour](screenshots/README.md) shows the administration application in both locales.
These are deterministic documentation renders, not browser or accessibility evidence.

The repository also includes a
[mock-only static administration demo](https://sebastianspicker.github.io/zammad-ticket-archiver/). It uses
synthetic browser-local state and is not a deployed service, integration test, or release artifact.
See [admin-frontend.md](admin-frontend.md) for the demonstration boundary.

## Evaluate safely

1. Use a disposable Zammad project and a non-production archive path.
2. Start with signing and the administration application disabled.
3. Follow the [deployment guide](deploy.md), [Zammad setup](02-zammad-setup.md), and
   [security checklist](09-security.md).
4. Confirm completion from the stored revision and manifest, journal history, and Zammad note;
   do not treat `202` as success.
5. Report ordinary defects with the GitHub issue template. Do not disclose vulnerabilities
   publicly; [SECURITY.md](../SECURITY.md) records the unresolved private-reporting gate.

## Compatibility

The alpha removes the former Redis queue/DLQ, the decorative dashboard, alternate PDF
templates, the broad pre-0.3 flat-variable compatibility layer, and the old operational demo stack.
The replacement static Pages demo has no backend capability, and the version 1 portable Zammad
aliases in the [configuration reference](config-reference.md) remain supported. Review
[CHANGELOG.md](../CHANGELOG.md) before upgrading from a `0.2.0` release candidate.

The supported contract is documented in the [architecture](01-architecture.md),
[configuration reference](config-reference.md), [API reference](api.md), and
[product contract](../PRODUCT.md).
