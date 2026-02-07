#!/usr/bin/env bash
set -euo pipefail

# Make executable:
#   chmod +x scripts/dev/gen-dev-certs.sh
#
# Purpose:
#   Generate local development TLS certificates (self-signed).
#   Useful for testing webhook delivery via HTTPS locally.
#
# Note:
#   Do not use these certificates in production.

usage() {
  cat >&2 <<'EOF'
Usage:
  scripts/dev/gen-dev-certs.sh [--dry-run] [OUT_DIR]

Defaults:
  OUT_DIR=./.dev-certs

Outputs:
  dev.key, dev.crt
EOF
}

dry_run=0
out_dir="./.dev-certs"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      out_dir="$1"
      shift
      ;;
  esac
done

key_path="${out_dir%/}/dev.key"
crt_path="${out_dir%/}/dev.crt"

cmd=(
  openssl req -x509 -newkey rsa:2048 -sha256 -days 7 -nodes
  -keyout "${key_path}"
  -out "${crt_path}"
  -subj "/CN=localhost"
)

echo "Output directory: ${out_dir}"
echo "Command:"
printf '  %q' "${cmd[@]}"
echo

if [[ "${dry_run}" -eq 1 ]]; then
  exit 0
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "ERROR: openssl not found in PATH." >&2
  exit 1
fi

mkdir -p "${out_dir}"
exec "${cmd[@]}"
