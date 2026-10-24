# ADR 0010: Accept Zammad's native webhook signature

## Status

Accepted (2026-10-24). Supersedes the webhook-algorithm sentence of
[ADR 0004](0004-current-architecture.md).

## Context

ADR 0004 accepted SHA-256 HMAC signatures only. Zammad itself signs webhook
deliveries as `X-Hub-Signature: sha1=<hex>`, an HMAC-SHA1 over the raw request
body (`lib/user_agent.rb` in Zammad; the admin documentation describes the same
header). With SHA-256 only, every webhook sent by an unmodified Zammad was
rejected with `403 forbidden`, so the documented setup could not work without a
re-signing proxy.

## Decision

- Compatibility mode (`hardening.webhook.require_delivery_id=false`, the
  default) accepts `sha1=<hex>` and `sha256=<hex>` over the raw body.
- Strict delivery-ID mode stays SHA-256 only. Zammad cannot produce the
  canonical delivery-bound form, so a strict-mode sender is always a
  purpose-built signer or proxy and has no reason to use SHA-1.
- The secret requirements are unchanged.

## Consequences

- Native Zammad webhooks authenticate without an intermediary.
- HMAC-SHA1 remains a sound message authentication code; its security does not
  rest on SHA-1 collision resistance. The residual risk is the one compatibility
  mode already carries: the delivery ID is not authenticated, so replay
  protection is best-effort dedupe.
- Operators who need delivery-bound signatures enable strict mode and sign with
  SHA-256.
