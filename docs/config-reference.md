# Configuration Reference

Source of truth:
- `src/zammad_pdf_archiver/config/settings.py`
- `src/zammad_pdf_archiver/config/load.py`
- `src/zammad_pdf_archiver/config/validate.py`

## 1. Load and Precedence

Effective precedence (highest first):
1. environment variables (including `.env` values loaded into process env)
2. flat env aliases (backward compatibility keys)
3. YAML mapping (`CONFIG_PATH` or `config/config.yaml` when present)
4. defaults in settings model

Notes:
- nested env keys use `__`, example: `ZAMMAD__BASE_URL`
- `.env` is loaded with `override=false`
- if `CONFIG_PATH` is set and file is missing, startup fails

## 2. Minimum Required Configuration

Required unless overridden by explicit unsafe/test options:
- `zammad.base_url`
- `zammad.api_token`
- `storage.root`
- webhook auth secret (`zammad.webhook_hmac_secret` or legacy `server.webhook_shared_secret`), unless `hardening.webhook.allow_unsigned=true`

## 3. Key Reference

### `server`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `server.host` | `0.0.0.0` | `SERVER_HOST` | bind host |
| `server.port` | `8080` | `SERVER_PORT` | bind port |
| `server.webhook_shared_secret` | `null` | `WEBHOOK_SHARED_SECRET` | legacy webhook secret fallback |

### `zammad`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `zammad.base_url` | required | `ZAMMAD_BASE_URL`, `ZAMMAD_URL` | Zammad base URL |
| `zammad.api_token` | required | `ZAMMAD_API_TOKEN` | API token |
| `zammad.webhook_hmac_secret` | `null` | `WEBHOOK_HMAC_SECRET` | webhook HMAC secret |
| `zammad.timeout_seconds` | `10.0` | `ZAMMAD_TIMEOUT_SECONDS` | outbound timeout |
| `zammad.verify_tls` | `true` | `ZAMMAD_VERIFY_TLS` | verify upstream TLS certs |

### `workflow`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `workflow.trigger_tag` | `pdf:sign` | `WORKFLOW_TRIGGER_TAG` | trigger tag |
| `workflow.require_tag` | `true` | `WORKFLOW_REQUIRE_TAG` | require trigger tag for processing |
| `workflow.acknowledge_on_success` | `true` | none | create success note on ticket |
| `workflow.delivery_id_ttl_seconds` | `3600` | `WORKFLOW_DELIVERY_ID_TTL_SECONDS` | in-memory dedupe TTL |

### `fields`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `fields.archive_path` | `archive_path` | `FIELDS_ARCHIVE_PATH` | ticket custom field name |
| `fields.archive_user_mode` | `archive_user_mode` | `FIELDS_ARCHIVE_USER_MODE` | ticket custom field name |

### `storage`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `storage.root` | required | `STORAGE_ROOT` | storage root path |
| `storage.atomic_write` | `true` | `STORAGE_ATOMIC_WRITE` | atomic temp-file replace mode |
| `storage.fsync` | `true` | `STORAGE_FSYNC` | file/dir fsync behavior |

#### `storage.path_policy`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `storage.path_policy.allow_prefixes` | `[]` | none | optional allowed path prefixes |
| `storage.path_policy.filename_pattern` | `Ticket-{ticket_number}_{timestamp_utc}.pdf` | none | output filename template |
| `storage.path_policy.sanitize.replace_whitespace` | `_` | none | compatibility setting |
| `storage.path_policy.sanitize.strip_control_chars` | `true` | none | compatibility setting |

### `pdf`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `pdf.template_variant` | `default` | `PDF_TEMPLATE_VARIANT`, `TEMPLATE_VARIANT` | template variant |
| `pdf.locale` | `de_DE` | `PDF_LOCALE`, `RENDER_LOCALE` | locale setting (template-dependent) |
| `pdf.timezone` | `Europe/Berlin` | `PDF_TIMEZONE`, `RENDER_TIMEZONE` | timezone setting (template-dependent) |
| `pdf.max_articles` | `250` | `PDF_MAX_ARTICLES` | max article count (`0` disables limit) |

### `signing`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `signing.enabled` | `false` | `SIGNING_ENABLED` | enable signing flow |
| `signing.pfx_path` | `null` | `SIGNING_PFX_PATH` | PKCS#12/PFX path |
| `signing.pfx_password` | `null` | `SIGNING_PFX_PASSWORD` | PFX password |

#### `signing.pades`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `signing.pades.cert_path` | `null` | `SIGNING_CERT_PATH` | compatibility key (not used by current signer) |
| `signing.pades.key_path` | `null` | `SIGNING_KEY_PATH` | compatibility key (not used by current signer) |
| `signing.pades.key_password` | `null` | `SIGNING_KEY_PASSWORD` | compatibility key |
| `signing.pades.reason` | `Ticket Archivierung` | `SIGNING_REASON` | PDF signature reason |
| `signing.pades.location` | `Datacenter` | `SIGNING_LOCATION` | PDF signature location |

#### `signing.timestamp.rfc3161`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `signing.timestamp.enabled` | `false` | `TSA_ENABLED` | enable RFC3161 timestamping |
| `signing.timestamp.rfc3161.tsa_url` | `null` | `TSA_URL` | TSA endpoint URL |
| `signing.timestamp.rfc3161.timeout_seconds` | `10.0` | `TSA_TIMEOUT_SECONDS` | TSA timeout |
| `signing.timestamp.rfc3161.ca_bundle_path` | `null` | `TSA_CA_BUNDLE_PATH` | custom trust bundle path |

Env-only TSA auth keys:
- `TSA_USER`
- `TSA_PASS`

### `observability`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `observability.log_level` | `INFO` | `LOG_LEVEL` | log level |
| `observability.log_format` | `null` | `LOG_FORMAT` | `json` or `human` |
| `observability.json_logs` | `false` | `LOG_JSON` | legacy JSON toggle |
| `observability.metrics_enabled` | `false` | `METRICS_ENABLED`, `OBSERVABILITY_METRICS_ENABLED` | expose `/metrics` |
| `observability.metrics_bearer_token` | `null` | `METRICS_BEARER_TOKEN` | when set, require `Authorization: Bearer <token>` for `/metrics` |
| `observability.healthz_omit_version` | `false` | `HEALTHZ_OMIT_VERSION` | omit `version` and `service` from `/healthz` response |

### `hardening.rate_limit`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `hardening.rate_limit.enabled` | `true` | `RATE_LIMIT_ENABLED` | enable rate limit middleware |
| `hardening.rate_limit.rps` | `5.0` | `RATE_LIMIT_RPS` | token refill rate |
| `hardening.rate_limit.burst` | `10` | `RATE_LIMIT_BURST` | token bucket capacity |
| `hardening.rate_limit.include_metrics` | `false` | `RATE_LIMIT_INCLUDE_METRICS` | include `/metrics` path |
| `hardening.rate_limit.client_key_header` | `null` | `RATE_LIMIT_CLIENT_KEY_HEADER` | header for rate-limit key (e.g. `X-Forwarded-For`) when behind proxy |

### `hardening.body_size_limit`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `hardening.body_size_limit.max_bytes` | `1048576` | `MAX_BODY_BYTES` | max request body bytes (`0` disables) |

### `hardening.webhook`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `hardening.webhook.allow_unsigned` | `false` | `HARDENING_WEBHOOK_ALLOW_UNSIGNED` | allow unsigned webhooks |
| `hardening.webhook.require_delivery_id` | `false` | `HARDENING_WEBHOOK_REQUIRE_DELIVERY_ID` | require `X-Zammad-Delivery` header |

### `hardening.transport`

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `hardening.transport.trust_env` | `false` | `HARDENING_TRANSPORT_TRUST_ENV` | allow proxy env for outbound HTTP |
| `hardening.transport.allow_insecure_http` | `false` | `HARDENING_TRANSPORT_ALLOW_INSECURE_HTTP` | allow `http://` upstreams |
| `hardening.transport.allow_insecure_tls` | `false` | `HARDENING_TRANSPORT_ALLOW_INSECURE_TLS` | allow TLS verify disable |
| `hardening.transport.allow_local_upstreams` | `false` | `HARDENING_TRANSPORT_ALLOW_LOCAL_UPSTREAMS` | allow loopback/link-local upstreams |

## 4. Non-schema Runtime Environment Keys

These are used by runtime/deployment but not part of `Settings` model:
- `CONFIG_PATH` (YAML config path)
- `TEMPLATES_ROOT` (template base directory override)
- `TSA_USER`, `TSA_PASS` (TSA basic auth)

## 5. Nested Environment Examples

Equivalent nested env keys:

```bash
ZAMMAD__BASE_URL=https://zammad.example.local
ZAMMAD__API_TOKEN=CHANGE-ME
STORAGE__ROOT=/mnt/archive
HARDENING__WEBHOOK__ALLOW_UNSIGNED=false
```

## 6. Minimal Config Examples

### Minimal YAML

```yaml
zammad:
  base_url: "https://zammad.example.local"
  api_token: "CHANGE-ME"
  webhook_hmac_secret: "CHANGE-ME"
storage:
  root: "/mnt/archive"
```

### Minimal Env

```bash
ZAMMAD_BASE_URL=https://zammad.example.local
ZAMMAD_API_TOKEN=CHANGE-ME
WEBHOOK_HMAC_SECRET=CHANGE-ME
STORAGE_ROOT=/mnt/archive
```
