# Configuration reference

Source of truth:

- `src/chronikwerk/configuration/models.py`
- `src/chronikwerk/configuration/sections.py`
- `src/chronikwerk/configuration/preservation.py`
- `src/chronikwerk/configuration/retention.py`
- `src/chronikwerk/configuration/signing.py`
- `src/chronikwerk/configuration/zammad.py`
- `src/chronikwerk/configuration/load.py`
- `src/chronikwerk/configuration/validation.py`

## Load precedence

Highest first:

1. Process environment variables.
2. Managed non-secret overlay in `admin.state_dir`.
3. Explicit constructor/YAML values from `CONFIG_PATH`, or
   `config/config.yaml` when present.
4. Dotenv values from `.env`.
5. File secrets when configured by the settings source.
6. Defaults in the settings model.

Nested environment keys use double underscores, for example
`ZAMMAD__BASE_URL`.

The version 1 portable runtime aliases `ZAMMAD_ORIGIN`, `ZAMMAD_API_TOKEN`,
`ZAMMAD_TIMEOUT_SECONDS`, `ZAMMAD_ALLOW_PRIVATE_ORIGIN`, and `ZAMMAD_TRUST_ENV`
are also accepted from the process environment. They have the same precedence as
nested process keys. If both forms are set, their parsed values must agree. Either
form marks the corresponding administration field as environment-owned and
read-only; validation and staging reject changes to that field.

`config/config.example.yaml` is a complete model example. The systemd and
Compose environment templates are partial deployment templates; their keys must be
known model keys, but omitted settings use model defaults.

## Minimum required values

Validated service startup requires:

- `zammad.base_url` / `ZAMMAD__BASE_URL` (portable alias: `ZAMMAD_ORIGIN`)
- `zammad.api_token` / `ZAMMAD__API_TOKEN` (portable alias: `ZAMMAD_API_TOKEN`)
- `storage.root` / `STORAGE__ROOT`
- `zammad.webhook_hmac_secret` / `ZAMMAD__WEBHOOK_HMAC_SECRET`

## Server

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `server.host` | `0.0.0.0` | `SERVER__HOST` | Bind host. |
| `server.port` | `8080` | `SERVER__PORT` | Bind port. |

## Zammad

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `zammad.base_url` | required | `ZAMMAD__BASE_URL` or `ZAMMAD_ORIGIN` | Zammad origin with no path, query, fragment, or credentials. HTTPS is required unless the explicit reviewed internal/test HTTP override is enabled. |
| `zammad.api_token` | required | `ZAMMAD__API_TOKEN` or `ZAMMAD_API_TOKEN` | Zammad API token. |
| `zammad.webhook_hmac_secret` | required by validation | `ZAMMAD__WEBHOOK_HMAC_SECRET` | HMAC secret for incoming webhooks; at least 32 characters and not a placeholder. The underlying model permits `null` only so validation can return a precise startup error. |
| `zammad.timeout_seconds` | `10.0` | `ZAMMAD__TIMEOUT_SECONDS` or `ZAMMAD_TIMEOUT_SECONDS` | Positive outbound API timeout. |
| `zammad.verify_tls` | `true` (fixed) | `ZAMMAD__VERIFY_TLS` | Compatibility input; `false` is rejected. Requests always verify TLS and use the fixed `/api/v1` root. |

## Workflow

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `workflow.trigger_tag` | `pdf:sign` | `WORKFLOW__TRIGGER_TAG` | Tag that requests archiving. |
| `workflow.require_tag` | `true` | `WORKFLOW__REQUIRE_TAG` | Require the trigger tag before processing. |
| `workflow.acknowledge_on_success` | `true` | `WORKFLOW__ACKNOWLEDGE_ON_SUCCESS` | Write a success note after archiving. |
| `workflow.delivery_id_ttl_seconds` | `3600` | `WORKFLOW__DELIVERY_ID_TTL_SECONDS` | In-memory dedupe TTL for `X-Zammad-Delivery`. |

## Fields

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `fields.archive_path` | `archive_path` | `FIELDS__ARCHIVE_PATH` | Ticket field containing archive path segments. |
| `fields.archive_user_mode` | `archive_user_mode` | `FIELDS__ARCHIVE_USER_MODE` | Ticket field selecting user directory mode. |
| `fields.archive_user` | `archive_user` | `FIELDS__ARCHIVE_USER` | Ticket field used when mode is `fixed`. |
| `fields.record_class` | `archive_record_class` | `FIELDS__RECORD_CLASS` | Ticket field holding the record class (see "Retention policy file"). |

## Storage

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `storage.root` | required | `STORAGE__ROOT` | Root directory for archive output. |
| `storage.fsync` | `true` | `STORAGE__FSYNC` | Fsync files/directories after writes. |
| `storage.filename_pattern` | `Ticket-{ticket_number}_{timestamp_utc}.pdf` | `STORAGE__FILENAME_PATTERN` | Deprecated and ignored: the preservation package layout replaced it. Setting it explicitly makes `load_settings` log a `config.deprecated_setting` warning through stdlib logging (stderr before structured logging is configured, so CLI JSON on stdout stays intact). |
| `storage.backend` | `filesystem` | `STORAGE__BACKEND` | `filesystem` or `s3`. With `s3`, `storage.s3.bucket`, `storage.s3.access_key_id` and `storage.s3.secret_access_key` are required. |
| `storage.filesystem.worm_enforced` | `false` | `STORAGE__FILESYSTEM__WORM_ENFORCED` | Operator's assertion that the share enforces WORM. |
| `storage.s3.endpoint_url` | `null` | `STORAGE__S3__ENDPOINT_URL` | S3-compatible endpoint; HTTPS only. |
| `storage.s3.region` | `null` | `STORAGE__S3__REGION` | Region, if the endpoint requires one. |
| `storage.s3.bucket` | `null` (required for `s3`) | `STORAGE__S3__BUCKET` | Bucket with versioning and Object Lock. |
| `storage.s3.object_lock_mode` | `COMPLIANCE` | `STORAGE__S3__OBJECT_LOCK_MODE` | `COMPLIANCE` or `GOVERNANCE`. |
| `storage.s3.staging_retention_days` | `7` | `STORAGE__S3__STAGING_RETENTION_DAYS` | Retention given to objects before commit; 1 to 365. |
| `storage.s3.access_key_id` | `null` (required for `s3`) | `STORAGE__S3__ACCESS_KEY_ID` | Capture identity key id; secret. |
| `storage.s3.secret_access_key` | `null` (required for `s3`) | `STORAGE__S3__SECRET_ACCESS_KEY` | Capture identity secret; redacted.

## PDF

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `pdf.locale` | `de-DE` | `PDF__LOCALE` | PDF locale; `de_DE`/`en_GB` legacy forms normalize to BCP 47. |
| `pdf.timezone` | `Europe/Berlin` | `PDF__TIMEZONE` | Time zone used by templates. |
| `pdf.max_articles` | `250` | `PDF__MAX_ARTICLES` | Maximum article count; `0` disables the limit. |
| `pdf.article_limit_mode` | `fail` | `PDF__ARTICLE_LIMIT_MODE` | `fail` or `cap_and_continue`. |
| `pdf.reading_copies` | `["pdf/a-2u", "pdf/ua-1"]` | `PDF__READING_COPIES` (JSON list) | Reading copies to render; non-empty, no duplicates, from `pdf/a-2u` and `pdf/ua-1`. |

## Signing

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `signing.enabled` | `false` | `SIGNING__ENABLED` | Enable PDF signing. |
| `signing.pfx_path` | `null` | `SIGNING__PFX_PATH` | PKCS#12/PFX bundle path. |
| `signing.pfx_password` | `null` | `SIGNING__PFX_PASSWORD` | PFX password. |
| `signing.pades.reason` | `Ticket Archivierung` | `SIGNING__PADES__REASON` | Signature reason. |
| `signing.pades.location` | `Datacenter` | `SIGNING__PADES__LOCATION` | Signature location. |
| `signing.pades.level` | `B-LT` | `SIGNING__PADES__LEVEL` | `B-T` or `B-LT` for the archival copy. |
| `signing.timestamp.enabled` | `false` | `SIGNING__TIMESTAMP__ENABLED` | Enable RFC 3161 timestamping. |
| `signing.timestamp.rfc3161.tsa_url` | `null` | `SIGNING__TIMESTAMP__RFC3161__TSA_URL` | TSA endpoint. |
| `signing.timestamp.rfc3161.ca_bundle_path` | `null` | `SIGNING__TIMESTAMP__RFC3161__CA_BUNDLE_PATH` | Optional CA bundle for TSA TLS verification. |
| `signing.timestamp.rfc3161.user` | `null` | `SIGNING__TIMESTAMP__RFC3161__USER` | TSA basic-auth user. |
| `signing.timestamp.rfc3161.password` | `null` | `SIGNING__TIMESTAMP__RFC3161__PASSWORD` | TSA basic-auth password. |
| `signing.timestamp.rfc3161.timeout_seconds` | `10.0` | `SIGNING__TIMESTAMP__RFC3161__TIMEOUT_SECONDS` | TSA request timeout. |

## Preservation

These settings belong to the preservation design. They are validated and documented now;
runtime behaviour that consumes them is added in later changes. Secret values
(`seal.pfx_password`, `storage.s3.access_key_id`, `storage.s3.secret_access_key`) are
redacted by `dump-config` and are never managed (staged) through the administration
application. The non-secret keys are owned by the environment or the YAML file.

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `journal.path` | `/var/lib/chronikwerk/journal.sqlite3` | `JOURNAL__PATH` | Journal and catalog database on local disk. |
| `capture.consistency_attempts` | `3` | `CAPTURE__CONSISTENCY_ATTEMPTS` | Collection attempts before the source counts as inconsistent; at least 1. |
| `capture.max_attachment_bytes` | `104857600` | `CAPTURE__MAX_ATTACHMENT_BYTES` | Largest single file (100 MiB); positive. |
| `capture.max_package_bytes` | `1073741824` | `CAPTURE__MAX_PACKAGE_BYTES` | Largest package (1 GiB); positive and not smaller than `capture.max_attachment_bytes`. |
| `capture.download_timeout_seconds` | `300` | `CAPTURE__DOWNLOAD_TIMEOUT_SECONDS` | Longest download of one object; positive. |
| `capture.temp_dir` | `/var/lib/chronikwerk/tmp` | `CAPTURE__TEMP_DIR` | Local build area; must be on local disk. |
| `capture.max_temp_bytes` | `4294967296` | `CAPTURE__MAX_TEMP_BYTES` | Cap on the build area (4 GiB); positive. |
| `capture.retry.max_attempts` | `5` | `CAPTURE__RETRY__MAX_ATTEMPTS` | Automatic retries of a transient failure; at least 1. |
| `capture.retry.max_delay_seconds` | `3600` | `CAPTURE__RETRY__MAX_DELAY_SECONDS` | Upper bound of the exponential back-off; positive. |
| `seal.pfx_path` | `null` | `SEAL__PFX_PATH` | PKCS#12 sealing identity; may be the same file as `signing.pfx_path`. |
| `seal.pfx_password` | `null` | `SEAL__PFX_PASSWORD` | Password of the seal PFX; secret. |
| `verify.trust_anchors_path` | `null` | `VERIFY__TRUST_ANCHORS_PATH` | PEM bundle with the seal CA and TSA CA. |
| `retention.policy_path` | `/etc/chronikwerk/retention.yaml` | `RETENTION__POLICY_PATH` | Record-class rules, see "Retention policy file". |
| `reconcile.interval_seconds` | `86400` | `RECONCILE__INTERVAL_SECONDS` | Reconciliation period; at least 60. |
| `reconcile.stale_after_seconds` | `86400` | `RECONCILE__STALE_AFTER_SECONDS` | Age at which a tagged ticket without a revision raises an alert; at least 60. |
| `fixity.cycle_days` | `90` | `FIXITY__CYCLE_DAYS` | Every revision is re-hashed at least once per cycle; 1 to 3650. |
| `alerts.webhook_url` | `null` | `ALERTS__WEBHOOK_URL` | Out-of-band alert endpoint (JSON POST); HTTPS only. |
| `renewal.lead_days` | `180` | `RENEWAL__LEAD_DAYS` | Renew this many days before the newest timestamp's TSA certificate expires; 1 to 3650. |

`preservation_requirements(settings)` in `validation.py` lists what a sealed, timestamped
archive still needs: `seal.pfx_path`, `verify.trust_anchors_path`,
`signing.timestamp.enabled` and `signing.timestamp.rfc3161.tsa_url`. Startup validation does
not call it yet.

## Retention policy file

`retention.policy_path` points to a YAML file (see `config/retention.example.yaml`):

```yaml
record_classes:
  pilot-test:
    retention: P30D
    trigger: capture
    readers: [service-desk]
```

Class names match `[a-z0-9-]{1,64}`. `retention` is an ISO 8601 duration with only `Y`, `M`
and `D` parts (for example `P6Y`, `P1Y6M`, `P30D`) and must be positive; months clamp to the
end of the month (31 January plus `P1M` is the end of February). `trigger` is `capture` or
`case_closed`. `readers` is a non-empty list of groups that receive read credentials for the
class prefix. Unknown keys are rejected, and errors name the file and the class. The example
values are placeholders; real periods come from records management.

## Observability

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `observability.log_level` | `INFO` | `OBSERVABILITY__LOG_LEVEL` | Log level. |
| `observability.log_format` | `null` | `OBSERVABILITY__LOG_FORMAT` | `json` or `human`. |
| `observability.metrics_enabled` | `false` | `OBSERVABILITY__METRICS_ENABLED` | Mount `/metrics`. |
| `observability.metrics_bearer_token` | `null` | `OBSERVABILITY__METRICS_BEARER_TOKEN` | Bearer token for `/metrics`; at least 32 characters when enabled. |
| `observability.healthz_omit_version` | `false` | `OBSERVABILITY__HEALTHZ_OMIT_VERSION` | Omit service/version from `/healthz`. |
| `observability.history_enabled` | `false` | `OBSERVABILITY__HISTORY_ENABLED` | Expose authenticated durable journal request history. |
| `observability.history_bearer_token` | `null` | `OBSERVABILITY__HISTORY_BEARER_TOKEN` | Dedicated bearer token required when history is enabled; at least 32 characters. |

The plain process variable `LOG_LEVEL`, when set and non-empty, overrides
`observability.log_level` at logging setup. It is outside the precedence chain above:
`validate-config`, `dump-config` and the admin application do not report it, and the admin
application does not mark the log level as environment-owned. Prefer
`OBSERVABILITY__LOG_LEVEL`, and unset a generic `LOG_LEVEL` shared with other services.

## Hardening

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `hardening.rate_limit.enabled` | `true` | `HARDENING__RATE_LIMIT__ENABLED` | Enable token-bucket rate limiting. |
| `hardening.rate_limit.rps` | `5.0` | `HARDENING__RATE_LIMIT__RPS` | Refill rate. |
| `hardening.rate_limit.burst` | `10` | `HARDENING__RATE_LIMIT__BURST` | Burst capacity. |
| `hardening.rate_limit.include_metrics` | `false` | `HARDENING__RATE_LIMIT__INCLUDE_METRICS` | Include `/metrics` in rate limiting. |
| `hardening.rate_limit.client_key_header` | `null` | `HARDENING__RATE_LIMIT__CLIENT_KEY_HEADER` | Trusted header for client key behind a proxy. |
| `hardening.body_size_limit.max_bytes` | `1048576` | `HARDENING__BODY_SIZE_LIMIT__MAX_BYTES` | Request body limit; `0` selects the non-disableable 32 MiB safety cap. Values above 32 MiB are capped. |
| `hardening.body_size_limit.timeout_seconds` | `10.0` | `HARDENING__BODY_SIZE_LIMIT__TIMEOUT_SECONDS` | Whole-body deadline for ingest and every body-bearing admin request. |
| `hardening.webhook.require_delivery_id` | `false` | `HARDENING__WEBHOOK__REQUIRE_DELIVERY_ID` | Require `X-Zammad-Delivery`. |
| `hardening.transport.trust_env` | `false` | `HARDENING__TRANSPORT__TRUST_ENV` or `ZAMMAD_TRUST_ENV` | Allow proxy and certificate environment settings for outbound HTTP. |
| `hardening.transport.allow_insecure_http` | `false` | `HARDENING__TRANSPORT__ALLOW_INSECURE_HTTP` | Explicitly permit an HTTP Zammad origin for a reviewed, isolated internal or test deployment. HTTPS remains the default, and certificate verification remains mandatory whenever HTTPS is used. |
| `hardening.transport.validation_hosts` | `[]` | `HARDENING__TRANSPORT__VALIDATION_HOSTS` (JSON list) | Bare host names (no scheme, port or path) of OCSP/CRL endpoints allowed for validation fetches; the TSA host is always allowed. |
| `hardening.transport.allow_private_networks` | `false` | `HARDENING__TRANSPORT__ALLOW_PRIVATE_NETWORKS` or `ZAMMAD_ALLOW_PRIVATE_ORIGIN` | Explicitly allow non-global Zammad addresses for reviewed internal deployments. |

## Admission

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `admission.max_pending` | `100` | `ADMISSION__MAX_PENDING` | Maximum requests in the journal's WAITING state, including transient failures waiting for their back-off retry; further admissions are rejected. Minimum `1`. |
| `admission.max_running` | `4` | `ADMISSION__MAX_RUNNING` | Maximum ticket pipelines running concurrently. |
| `admission.shutdown_timeout_seconds` | `5.0` | `ADMISSION__SHUTDOWN_TIMEOUT_SECONDS` | Grace period before async cancellation. In-flight PDF, signing, and filesystem worker threads are awaited after cancellation and can extend total shutdown time. |

## Administration

The administration application is disabled by default. When enabled, the access token
must contain at least 32 characters. The state directory stores only managed non-secret
overlays and the latest 20 revision records; mount it on persistent storage. Sessions are
process-local and do not survive a restart. All managed changes require an external
restart and environment-owned fields remain read-only.

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `admin.enabled` | `false` | `ADMIN__ENABLED` | Mount the `/admin` HTML and API routes. |
| `admin.access_token` | `null` | `ADMIN__ACCESS_TOKEN` | External admin token of at least 32 characters; never stored in a cookie or revision. |
| `admin.state_dir` | `/var/lib/chronikwerk/admin` | `ADMIN__STATE_DIR` | Persistent directory for non-secret managed revisions. |
| `admin.session_idle_seconds` | `1800` | `ADMIN__SESSION_IDLE_SECONDS` | Process-local idle session lifetime. |
| `admin.session_absolute_seconds` | `28800` | `ADMIN__SESSION_ABSOLUTE_SECONDS` | Absolute session lifetime. |
| `admin.cookie_secure` | `true` | `ADMIN__COOKIE_SECURE` | Send the session cookie only over HTTPS. |
| `admin.default_locale` | `de-DE` | `ADMIN__DEFAULT_LOCALE` | Initial admin locale; supports `de-DE` and `en-GB`. |

## Top-level runtime tokens

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `retry_bearer_token` | `null` | `RETRY_BEARER_TOKEN` | Bearer token of at least 32 characters for `POST /retry/{ticket_id}`. |

## Minimal YAML

```yaml
zammad:
  base_url: "https://zammad.example.local"
  api_token: "CHANGE-ME"
  webhook_hmac_secret: "CHANGE-ME-TO-A-RANDOM-32-BYTE-SECRET"
storage:
  root: "/mnt/archive"
hardening:
  transport:
    allow_private_networks: true
```

## Minimal environment

```bash
ZAMMAD_ORIGIN=https://zammad.example.local
ZAMMAD_API_TOKEN=CHANGE-ME
ZAMMAD__WEBHOOK_HMAC_SECRET=CHANGE-ME-TO-A-RANDOM-32-BYTE-SECRET
STORAGE__ROOT=/mnt/archive
ZAMMAD_ALLOW_PRIVATE_ORIGIN=true
```

The examples fail validation until every `CHANGE-ME` value is
replaced. Generate authentication secrets with at least 32 random characters. The
private-origin override is present only because the example uses a `.local` Zammad host;
omit it for a globally routable HTTPS origin.
