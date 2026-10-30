# Deployment

This project ships Docker and Docker Compose files for a single-host deployment. Use
one external environment file for both Compose interpolation and the container, and set
`CHRONIKWERK_ENV_FILE` in that file to its installed path.

## Prerequisites

- Linux host with Docker Engine and Docker Compose 2.24.0 or newer.
- Mounted archive storage path. For records, use a share or store that enforces write-once
  retention itself; a plain share is for testing only (see [07 - Storage](07-storage.md)).
- Zammad API token and webhook HMAC secret.
- PKCS#12/PFX sealing material, a trust anchor bundle, a reachable RFC 3161 timestamp authority,
  and a retention policy file. All four are required for a revision to complete.
- Local persistent disk for the request journal and for the capture build area.
- Optional PKCS#12/PFX PDF signing material when signing is enabled.
- A persistent admin-state directory and TLS proxy before the admin feature is enabled.

## Suggested layout

- Repository and compose files: `/opt/chronikwerk`
- Environment and secrets: `/etc/chronikwerk`
- Retention policy: `/etc/chronikwerk/retention.yaml`
- Trust anchors: `/etc/chronikwerk/trust-anchors.pem`
- Archive mount: `/mnt/archive` or another dedicated path
- Admin revision state: `/var/lib/chronikwerk/admin`
- Journal and catalog: `/var/lib/chronikwerk/journal.sqlite3` (local disk)
- Capture build area: `/var/lib/chronikwerk/tmp` (local disk)

```bash
sudo install -d -m 0755 /opt/chronikwerk
sudo install -d -m 0750 /etc/chronikwerk/secrets
```

Extract a reviewed source release into `/opt/chronikwerk`, or use a reviewed image when one
is available. Do not copy a developer working tree: it may contain ignored credentials,
local configuration, archives, reports, or tool state that do not belong on the deployment
host.

## Configure environment

Copy a template and edit it on the target host:

```bash
cd /opt/chronikwerk
sudo install -m 0640 -o root -g root infra/systemd/chronikwerk.env.example /etc/chronikwerk/chronikwerk.env
sudo ${EDITOR:-vi} /etc/chronikwerk/chronikwerk.env
```

Minimum values:

- `ZAMMAD__BASE_URL`
- `ZAMMAD__API_TOKEN`
- `ZAMMAD__WEBHOOK_HMAC_SECRET`
- `STORAGE__ROOT`
- `SEAL__PFX_PATH` and `SEAL__PFX_PASSWORD`
- `VERIFY__TRUST_ANCHORS_PATH`
- `SIGNING__TIMESTAMP__ENABLED=true` and `SIGNING__TIMESTAMP__RFC3161__TSA_URL`
- `RETENTION__POLICY_PATH` (default `/etc/chronikwerk/retention.yaml`)

The portable aliases `ZAMMAD_ORIGIN` and `ZAMMAD_API_TOKEN` may replace the
first two nested keys. Use one form only, or keep duplicate normalized values
identical; conflicting duplicates fail startup.

Keep this line in the installed file and update it if the location changes:

```bash
CHRONIKWERK_ENV_FILE=/etc/chronikwerk/chronikwerk.env
```

## Seal, trust anchors and retention policy

Every revision is sealed and timestamped; startup validation reports whichever of the seal PFX, the
trust anchors, the TSA settings or the timestamp switch is missing. Provision the files outside the
repository. Today the settings validator also rejects `SIGNING__TIMESTAMP__ENABLED=true` without
`SIGNING__ENABLED=true` and a signing PFX (see the next section); the seal PFX may be the same file.

```bash
sudo install -m 0640 -o 10001 -g 10001 /path/to/seal.pfx /etc/chronikwerk/secrets/seal.pfx
sudo install -m 0644 /path/to/trust-anchors.pem /etc/chronikwerk/trust-anchors.pem
sudo install -m 0644 config/retention.example.yaml /etc/chronikwerk/retention.yaml
```

The trust anchor bundle holds the dedicated CA of the seal identity and the CA of the timestamp
authority; see [06 - Signing and timestamping](06-signing-and-timestamp.md). The shipped retention
example has one placeholder class, `pilot-test`; replace it with the record classes and periods
that records management has approved before production use. There are no built-in retention values,
and a ticket whose record class is missing from the file fails with `record_class_missing`.

Mount the files into the container, for example in the local override:

```yaml
services:
  chronikwerk:
    volumes:
      - /etc/chronikwerk/secrets/seal.pfx:/run/secrets/seal.pfx:ro
      - /etc/chronikwerk/trust-anchors.pem:/etc/chronikwerk/trust-anchors.pem:ro
      - /etc/chronikwerk/retention.yaml:/etc/chronikwerk/retention.yaml:ro
```

## Journal and build area

The service keeps the request journal and catalog in `journal.path` and builds each package in
`capture.temp_dir` before it writes anything to the archive. Both must be on local, persistent
disk that the service user (UID/GID `10001` in the image) can write; neither belongs on the archive
share. Startup refuses (and `chronikwerk-admin validate-config` reports) a journal directory or
build area that resolves into `storage.root`, a build area that is a symbolic link, is not owned by
the service user or grants group or other access, a missing or read-only `storage.root`, and a
read-only journal directory.

The Compose file mounts the named volume `chronikwerk-state` at `/var/lib/chronikwerk`. The image
creates `/var/lib/chronikwerk` and `/var/lib/chronikwerk/tmp` owned by the service user with mode
`0700`, and Docker copies that ownership into a fresh named volume, so the defaults
(`/var/lib/chronikwerk/journal.sqlite3`, `/var/lib/chronikwerk/tmp`) work without preparation. A
volume created by an older image, or a volume `chown`ed by hand, must still be owned by `10001` with
the build area at mode `0700`. With the service stopped, fix it with a one-off container (the
Compose service itself drops `CAP_CHOWN`); Compose prefixes the volume name with the project name,
check it with `docker volume ls`:

```bash
sudo docker run --rm --user 0 --entrypoint sh \
  -v chronikwerk_chronikwerk-state:/state chronikwerk:local \
  -c 'chown 10001:10001 /state && install -d -m 0700 -o 10001 -g 10001 /state/tmp && chmod 0700 /state/tmp'
```

To use host directories instead, create them owned by the service user and mount them in the local
override:

```bash
sudo install -d -m 0750 -o 10001 -g 10001 /var/lib/chronikwerk/journal
sudo install -d -m 0700 -o 10001 -g 10001 /var/lib/chronikwerk/tmp
```

```yaml
services:
  chronikwerk:
    volumes:
      - /var/lib/chronikwerk/journal:/var/lib/chronikwerk/journal:rw
      - /var/lib/chronikwerk/tmp:/var/lib/chronikwerk/tmp:rw
```

With this layout, set `JOURNAL__PATH=/var/lib/chronikwerk/journal/journal.sqlite3`. The journal
directory must exist and must not be world-writable without the sticky bit.

Keep at least `capture.max_temp_bytes × admission.max_running` free on the disk that holds
`capture.temp_dir` (defaults: 4 GiB × 4 = 16 GiB), plus room for the journal to grow. The
maintenance task removes capture build areas abandoned for more than a day. Back up the journal as
described in [08 - Operations](08-operations.md).

## Optional signing material

The production image already includes the `signing` extra, so sealing, PDF signing and
`chronikwerk-admin verify` need no extra installation. For a source install, install the extra
(`pip install 'chronikwerk[signing]'`).

Store the real PFX outside the repository:

```bash
sudo install -m 0640 -o 10001 -g 10001 /path/to/signing.pfx /etc/chronikwerk/secrets/signing.pfx
```

The production image runs as UID/GID `10001`; preserve equivalent service-readable ownership if
your deployment maps that identity differently. Do not make the PFX group- or world-writable.

Then configure:

```bash
SIGNING__ENABLED=true
SIGNING__PFX_PATH=/run/secrets/signing.pfx
SIGNING__PFX_PASSWORD=CHANGE-ME
```

Save this local-only override as `/opt/chronikwerk/docker-compose.override.yml`. Docker
Compose loads it with the checked-in `docker-compose.yml` when the commands below run from
`/opt/chronikwerk`.

```yaml
services:
  chronikwerk:
    volumes:
      - /etc/chronikwerk/secrets/signing.pfx:/run/secrets/signing.pfx:ro
```

Do not commit the host-specific override or signing file.

## Start

```bash
cd /opt/chronikwerk
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env up -d --build
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env exec -T chronikwerk \
  python -c 'import os,urllib.request; p=os.getenv("SERVER__PORT","8080"); print(urllib.request.urlopen(f"http://127.0.0.1:{p}/healthz", timeout=2).read().decode())'
```

Production Compose publishes the service on `127.0.0.1` by default so plaintext
HTTP is reachable only by a same-host TLS proxy. Set `CHRONIKWERK_PUBLISH_HOST` to a
different host address only when an approved ingress and firewall provide the
equivalent boundary; setting it to `0.0.0.0` exposes the plaintext application
port on every host interface.

## Optional administration application

Keep `ADMIN__ENABLED=false` until the documented web and PDF release gates pass. Before
enabling it, provision the Compose `admin-state` volume (or an equivalent writable
systemd path), place the service behind TLS and the existing trusted-network boundary,
and set a high-entropy token of at least 32 characters outside Git. The
placeholder below must be replaced:

```bash
ADMIN__ENABLED=true
ADMIN__ACCESS_TOKEN=CHANGE-ME-AT-LEAST-32-CHARACTERS
ADMIN__STATE_DIR=/var/lib/chronikwerk/admin
ADMIN__COOKIE_SECURE=true
```

The UI can stage allowlisted non-secret values but cannot restart the service. After a
stage operation, restart externally and verify that Overview shows the revision as
active. Offline recovery commands are:

```bash
chronikwerk-admin list-config-revisions
chronikwerk-admin stage-config-rollback <full-revision-hash>
```

## CIFS/SMB storage

Mount the share on the host and point `STORAGE__ROOT` at the mountpoint.

Example one-off mount:

```bash
sudo mount -t cifs //fileserver/archive /mnt/archive \
  -o credentials=/etc/chronikwerk/cifs.creds,uid=10001,gid=10001,iocharset=utf8,file_mode=0640,dir_mode=0750,noserverino
```

For production, prefer `/etc/fstab` or a managed mount unit with credentials
stored outside the repository.

A plain CIFS/SMB share does not enforce retention: revisions on it are recorded with
`retention_enforced` false. The filesystem store also needs hard links and `flock` on the mount;
test both with these mount options before use, and see [07 - Storage](07-storage.md). Set
`STORAGE__FILESYSTEM__WORM_ENFORCED=true` only if the share itself enforces write-once retention.

## Operations

Start/stop, rollback, health checks, troubleshooting, and on-call steps live in
[08 - Operations](08-operations.md).
