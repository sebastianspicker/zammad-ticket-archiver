#!/usr/bin/env bash
set -euo pipefail

# Make executable:
#   chmod +x scripts/ops/mount-cifs.sh
#
# Purpose:
#   Mount a CIFS/SMB share for archive storage.
#   This is a helper script for operations; adjust to your environment and security requirements.
#
# Required env vars (placeholders):
#   CIFS_REMOTE, CIFS_MOUNTPOINT, CIFS_USERNAME, CIFS_PASSWORD
# Optional:
#   CIFS_DOMAIN
#   CIFS_UID (default: 10001), CIFS_GID (default: 10001)
#   CIFS_CREDENTIALS_FILE (use existing credentials file instead of creating a temp one)

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: must run as root (mount requires privileges)." >&2
  exit 1
fi

: "${CIFS_REMOTE:?Missing CIFS_REMOTE (e.g. //server/share)}"
: "${CIFS_MOUNTPOINT:?Missing CIFS_MOUNTPOINT (e.g. /mnt/archive)}"

mkdir -p "${CIFS_MOUNTPOINT}"

uid="${CIFS_UID:-10001}"
gid="${CIFS_GID:-10001}"

creds_file="${CIFS_CREDENTIALS_FILE:-}"
tmp_creds=""

cleanup() {
  if [[ -n "${tmp_creds}" ]]; then
    rm -f "${tmp_creds}" || true
  fi
}
trap cleanup EXIT

if [[ -z "${creds_file}" ]]; then
  : "${CIFS_USERNAME:?Missing CIFS_USERNAME (or set CIFS_CREDENTIALS_FILE)}"
  : "${CIFS_PASSWORD:?Missing CIFS_PASSWORD (or set CIFS_CREDENTIALS_FILE)}"

  # Avoid putting credentials on the command line (visible via process listing).
  umask 077
  tmp_creds="$(mktemp)"
  {
    echo "username=${CIFS_USERNAME}"
    echo "password=${CIFS_PASSWORD}"
    if [[ -n "${CIFS_DOMAIN:-}" ]]; then
      echo "domain=${CIFS_DOMAIN}"
    fi
  } > "${tmp_creds}"
  creds_file="${tmp_creds}"
fi

opts="credentials=${creds_file},uid=${uid},gid=${gid},iocharset=utf8,file_mode=0640,dir_mode=0750,noserverino"

echo "Mounting ${CIFS_REMOTE} -> ${CIFS_MOUNTPOINT}"
echo "NOTE: This is a placeholder script. Review options (sec=..., vers=..., credentials file) before production."

mount -t cifs "${CIFS_REMOTE}" "${CIFS_MOUNTPOINT}" -o "${opts}"

echo "Mounted."
