# ADR 0004: Current product and deployment contract

## Status

Accepted (2026-07-10). Superseded in part by [ADR 0010](0010-zammad-native-webhook-signature.md):
the webhook signature algorithm.

## Decision

The supported runtime is one FastAPI process with process-local admission,
in-flight state, delivery-ID deduplication, and job history. Graceful shutdown
waits for admitted work, but a process crash or abrupt termination may lose
accepted background work. Deployments must therefore run a single service
instance unless a future durable queue contract is explicitly added.

The archive contains ticket and article metadata, including attachment
metadata, but does not archive attachment binaries. Article bodies are rendered
as sanitized rich HTML with a plain-text fallback when sanitization produces no
content.

Webhook authentication accepts SHA-256 HMAC signatures only. SHA-1 is not a
compatibility mode. `GET /jobs/history` is an authenticated operational
endpoint and remains process-local.

There is one production image, and it includes the optional signing runtime
(`pyHanko`, its validator, and direct signing imports). Signing is enabled by
configuration and does not require a separate unsigned image.

Production Compose and systemd use one external environment file, selected by
`CHRONIKWERK_ENV_FILE`. The same file is used for Compose interpolation and is
passed to the container. Environment names use the nested Pydantic form (for
example, `SERVER__PORT`, `ZAMMAD__API_TOKEN`, and `SIGNING__PFX_PATH`). Local
YAML overrides and signing key material stay outside Git and Docker build
contexts.

## Consequences

- A `202 Accepted` response means the work was admitted, not that a PDF was
  archived.
- Operators must inspect tags, notes, history, logs, and archive output when
  confirming completion.
- Scaling horizontally without a durable queue can duplicate work or race on
  ticket tags and is unsupported by this contract.
- Attachment metadata remains available to PDF templates without introducing
  binary storage and retention obligations.
