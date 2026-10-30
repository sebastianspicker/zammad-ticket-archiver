# Signing and timestamping

Chronikwerk protects an archived revision with two separate mechanisms:

- The **manifest seal** and its **RFC 3161 timestamp** are mandatory. They bind every file of the
  package to one signed inventory. A revision is not complete without them.
- The **PAdES signature** on the archival reading copy is optional. It decorates one PDF; it does
  not protect the package.

The decision is recorded in [ADR 0012](adr/0012-sealed-preservation-package.md).

## Manifest seal and timestamp

When a package is assembled, the service:

1. Writes `manifest.json`, which lists every packaged file with its size and SHA-256.
2. Seals it: `manifest.json.p7s` is a detached CMS SignedData (RFC 5652) in DER form, made with
   SHA-256 and CAdES-B signed attributes. The signer's full certificate chain from the PFX is
   embedded.
3. Timestamps the seal: `manifest.json.p7s.tst` is an RFC 3161 TimeStampToken in DER form over the
   SHA-256 of the exact `.p7s` bytes.
4. Checks both against the trust anchors before anything reaches the store: the seal must be
   intact and chain to an anchor, the token must be intact, chain to an anchor and cover the seal,
   and the token's `genTime` must lie within the collection window plus a five-minute tolerance.
   A failed check ends in `seal_failed` or `timestamp_failed`.

Both files verify with stock OpenSSL (see "Verify a package"). Files under `evidence/` are not
listed in the manifest; that location is reserved for validation material and renewal timestamps,
which are not produced yet.

### Required configuration

Seal and timestamp are required for every revision. Startup validation reports each missing item.

```bash
SEAL__PFX_PATH=/run/secrets/seal.pfx
SEAL__PFX_PASSWORD=CHANGE-ME
VERIFY__TRUST_ANCHORS_PATH=/etc/chronikwerk/trust-anchors.pem
SIGNING__TIMESTAMP__ENABLED=true
SIGNING__TIMESTAMP__RFC3161__TSA_URL=https://tsa.example.com
```

The seal identity may be the same PFX as the PDF signature (`signing.*`) or a different one;
keeping them separate lets the PDF signature rotate without touching the seal. Optional TSA basic
auth and a TLS CA bundle for the TSA connection use the existing keys:

```bash
SIGNING__TIMESTAMP__RFC3161__USER=tsa-user
SIGNING__TIMESTAMP__RFC3161__PASSWORD=CHANGE-ME
```

Timestamping does not require PDF signing: `signing.timestamp.enabled=true` with a TSA URL is
enough for the manifest seal, and `signing.enabled` only controls the optional PAdES signature on
the archival copy.

### Trust anchors

`verify.trust_anchors_path` is a PEM bundle with the certificates a verifier trusts for the seal
and for the TSA. Treat it as a short list, not a general trust store: use a dedicated CA for the
seal identity, and include the TSA's CA, so that only certificates issued for these purposes
validate. A public trust store would accept any certificate that chains to it.

### Signer pinning

The service pins the seal signer. Before completing a revision it requires that the seal was made
by the certificate in `seal.pfx_path`, identified by its SHA-256 fingerprint, in addition to
chaining to an anchor. `chronikwerk-admin verify` can apply the same check to a package with
`--expected-signer <sha256 fingerprint>`, which may be repeated for several accepted signers.

### What validation does not cover

Validation runs offline with the supplied trust anchors only. It does not fetch OCSP responses or
CRLs and does not check revocation; every report states "revocation not checked (offline
validation)". Validation material for the seal and TSA certificates is not collected or stored
under `evidence/validation/` yet. A TSA certificate expires eventually, and renewal timestamps that
would extend the evidence are not built yet. See [08 - Operations](08-operations.md).

## PDF signature (PAdES)

Signing the PDF is optional. When `signing.enabled` is true, the service applies a PAdES signature
to `reading-copy.pdf` (the PDF/A-2u archival copy) before it is hashed into the manifest. The
accessible copy `reading-copy-ua.pdf` is never signed.

```bash
SIGNING__ENABLED=true
SIGNING__PFX_PATH=/run/secrets/signing.pfx
SIGNING__PFX_PASSWORD=CHANGE-ME
```

Status of the signature level: `signing.pades.level` accepts `B-T` and `B-LT`, but embedding
validation material into the PDF (B-LT) is not implemented yet. The signature carries the RFC 3161
signature timestamp when timestamping is enabled; it does not carry embedded revocation data.
Whether the signed PDF still satisfies PDF/A-2u is not validated by Chronikwerk and needs veraPDF.

### Runtime behavior

- If signing is disabled, the unsigned archival copy is stored; the seal still protects it.
- If signing is enabled but the PFX is missing or invalid, processing fails.
- If the TSA request fails, processing fails. A TSA that cannot be reached is a transient failure
  (`timestamp_failed`) and is retried with back-off.
- The signing certificate fingerprint comes from the exact in-memory signer used for the PDF, so
  mutable PFX material is never reread for provenance.
- Cached signers are keyed by the loaded PFX bytes and password, so a rotation is detected even
  when a deployment preserves the file's modification time. The cached certificate's validity
  window is checked on every signature, so hourly PFX parsing cannot sign past certificate expiry.

## Secret handling

Keep PFX files and passwords outside the repository. Use deployment secret storage, protected
environment files, or read-only mounted files. Provision each PFX as a bounded regular file owned by
the service account. Do not use a symlink or a group- or world-writable key file; this candidate
does not enforce those checks itself.

## Verify a package

`chronikwerk-admin verify --path <package directory>` checks a package offline. It needs no service
configuration, no Zammad and no network, so it works on a copied directory. It needs the `signing`
extra (included in the production image) and the trust anchors:

```bash
chronikwerk-admin verify --path ./r0002_0192f3a4-7c1e-7b52-9d0e-3f6a1c2b4d5e \
  --trust-anchors ./trust-anchors.pem
```

Options: `--expected-signer <sha256 fingerprint>` (repeatable) pins the seal signer, and
`--report <file>` writes a JSON report to a new file. The command prints one line per check and
ends with `Result: PASSED` or `Result: FAILED`. Exit code `0` means every check passed, `1` means a
check failed, and `2` means the command could not run (missing directory, unreadable trust anchors,
existing report file).

The checks are:

- `manifest`: `manifest.json` exists and parses as `chronikwerk-package/1`.
- `files`: every listed file exists with the listed size and SHA-256, and no unlisted files are
  present outside `evidence/`.
- `seal`: the CMS seal covers `manifest.json` and chains to a trust anchor.
- `timestamp`: the token covers the seal and chains to a trust anchor.
- `pdf_signature`: when `reading-copy.pdf` carries a PAdES signature, it validates.

Checking a revision straight from the store (`verify --revision`) and the store's retention status
are not built yet.

The seal, the token and the file hashes can also be checked without Chronikwerk. Run these in the
package directory, with `hfmt-seal-ca.pem` holding the seal CA and `tsa-ca.pem` the TSA CA:

```bash
openssl cms -verify -binary -inform DER -purpose any -in manifest.json.p7s \
    -content manifest.json -CAfile hfmt-seal-ca.pem -out /dev/null
openssl ts -verify -token_in -in manifest.json.p7s.tst -data manifest.json.p7s -CAfile tsa-ca.pem
jq -r '.files[] | "\(.sha256)  \(.path)"' manifest.json | sha256sum -c
```

The first command verifies the seal, the second the timestamp token over the seal, and the third
every file hash listed in the manifest. These OpenSSL commands cover cryptographic integrity and
chain building to the CA you supply; what counts as a trusted CA, and whether a signature or
timestamp is acceptable for a given purpose, is the operator's policy decision.
