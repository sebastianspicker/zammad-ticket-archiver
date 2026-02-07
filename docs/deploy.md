# Deployment (production)

This repo ships production artifacts:

- `Dockerfile` (prod image)
- `docker-compose.yml` (prod compose)
- `infra/systemd/zammad-archiver.service` + `infra/systemd/zammad-archiver.env` (optional systemd wrapper)

The service exposes a health endpoint at `GET /healthz`.

## 1) Prerequisites

- Linux host with Docker Engine + Docker Compose v2 plugin (`docker compose ...`)
- A mounted archive storage path on the host (e.g. a CIFS/SMB mount) that will be bind-mounted into the container
- Optional signing material (PKCS#12/PFX) available on disk if signing is enabled

## 2) Install files on the server

Suggested layout:

- Code + compose: `/opt/zammad-ticket-archiver`
- Config/env/secrets: `/etc/zammad-archiver`

Example:

```bash
sudo mkdir -p /opt/zammad-ticket-archiver
sudo mkdir -p /etc/zammad-archiver/secrets

sudo rsync -a --delete ./ /opt/zammad-ticket-archiver/
```

## 3) Configure environment

Copy the template and edit:

```bash
sudo install -m 0640 -o root -g root infra/systemd/zammad-archiver.env /etc/zammad-archiver/zammad-archiver.env
sudo ${EDITOR:-vi} /etc/zammad-archiver/zammad-archiver.env
```

Minimum required values:

- `ZAMMAD_BASE_URL`
- `ZAMMAD_API_TOKEN`
- `STORAGE_ROOT`

Config file (optional):

- If you keep `CONFIG_PATH=./config/config.yaml`, create `/opt/zammad-ticket-archiver/config/config.yaml`.
- If you want an absolute path, set `CONFIG_PATH=/etc/zammad-archiver/config.yaml` and bind-mount it (see below).

## 4) Optional signing material (PFX)

Create the secret file:

```bash
sudo install -m 0640 -o root -g root /dev/null /etc/zammad-archiver/secrets/signing.pfx
# Copy your real PFX into place:
sudo cp /path/to/your/signing.pfx /etc/zammad-archiver/secrets/signing.pfx
```

Then in `/etc/zammad-archiver/zammad-archiver.env` set:

- `SIGNING_ENABLED=true`
- `SIGNING_PFX_PATH=/run/secrets/signing.pfx`
- `SIGNING_PFX_PASSWORD=...` (if needed)

Add a compose override to mount the file into the container:

```yaml
services:
  zammad-pdf-archiver:
    volumes:
      - /etc/zammad-archiver/secrets/signing.pfx:/run/secrets/signing.pfx:ro
```

## 5) CIFS mount example (brief)

If your archive storage is a CIFS/SMB share, mount it on the host and point `STORAGE_ROOT` to the mountpoint.

One-off mount (placeholder helper; review options before production):

```bash
sudo -E bash -lc 'source /etc/zammad-archiver/zammad-archiver.env; scripts/ops/mount-cifs.sh'
```

For a persistent mount, prefer `/etc/fstab` with a credentials file (not shown here).

## 6) Start with Docker Compose (manual)

```bash
cd /opt/zammad-ticket-archiver
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env up -d --build
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env ps
```

Health check:

```bash
curl -fsS "http://127.0.0.1:${SERVER_PORT:-8080}/healthz"
```

## 7) Start with systemd (optional)

Install the unit:

```bash
sudo install -m 0644 infra/systemd/zammad-archiver.service /etc/systemd/system/zammad-archiver.service
sudo systemctl daemon-reload
sudo systemctl enable --now zammad-archiver.service
sudo systemctl status zammad-archiver.service
```

Notes:

- The unit assumes the repo is deployed to `/opt/zammad-ticket-archiver`. Adjust `WorkingDirectory=` in the unit
  if you use a different path.
- The unit runs `docker compose up -d` on start and `docker compose down` on stop.

## 8) Update and rollback

Update (rebuild + recreate container):

```bash
cd /opt/zammad-ticket-archiver
sudo rsync -a --delete /path/to/updated/repo/ ./
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env up -d --build
```

Rollback (re-deploy previous revision and rebuild):

```bash
cd /opt/zammad-ticket-archiver
sudo git checkout <known-good-commit-or-tag>
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env up -d --build
```

If you want true “no-build” rollbacks, publish versioned images to a registry and pin `image:` tags instead of
building on the server.
