# ADR 0012: Sealed preservation package

## Status

Accepted (2026-10-30); implementation in progress.

## Context

Each archive run produces one PDF and an unsealed JSON sidecar holding the PDF's SHA-256.
Attachments are kept as metadata only and original emails are not kept, so the archive cannot
answer questions about attachments or headers. Anyone who can replace the PDF can also replace
the sidecar's checksum. The HfMT design requires a per-ticket package that keeps originals,
binds every file to an inventory, and can be verified without Zammad or Chronikwerk.

## Decision

- Each capture produces a package (format `chronikwerk-package/1`) containing the verbatim ticket
  and article API responses, each email article's original message (`ticket_article_plain`), the
  attachment bytes, a capture description, and two reading copies rendered from one snapshot:
  `reading-copy.pdf` (PDF/A-2u) and `reading-copy-ua.pdf` (PDF/UA-1). WeasyPrint 69 writes one
  conformance identifier per file, so one file cannot carry both.
- `manifest.json` lists every file's path, role, size and SHA-256, plus coverage. It is sealed
  with a detached CMS signature (`manifest.json.p7s`, RFC 5652, CAdES-B attributes) made with an
  institutional sealing identity (`seal.*`). An RFC 3161 token over the seal bytes is stored as
  `manifest.json.p7s.tst`. Both verify with stock OpenSSL.
- Certificates and revocation data for the seal and the timestamp are stored under `evidence/`;
  renewal timestamps are added there later. Files under `evidence/` are self-authenticating and
  are not listed in the manifest.
- A revision is complete only when the seal and timestamp validate against
  `verify.trust_anchors_path` and every stored file has been read back. Seal and timestamp are
  mandatory; there is no unsealed mode.
- Each capture of changed content receives a new revision (UUIDv7 revision ID, per-ticket
  revision number). A request whose source digest and policy ID match an existing complete
  revision resolves to that revision.
- A catalog in the journal database indexes revisions and can be rebuilt from the stored
  manifests.
- `chronikwerk-admin verify` checks a package offline, from the store or from a copied directory.

## Consequences

- Package size grows with attachments; capture limits (`capture.max_*`) bound it.
- The PAdES signature on the archival copy becomes optional decoration of one file; the seal is
  what protects the package.
- Producing two PDFs per capture roughly doubles render time.
- The legacy PDF plus sidecar output is no longer produced (ADR 0013).
