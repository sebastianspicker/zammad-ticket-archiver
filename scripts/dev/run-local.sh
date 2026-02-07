#!/usr/bin/env bash
set -euo pipefail

# Make executable:
#   chmod +x scripts/dev/run-local.sh
#
# Purpose:
#   Local developer entrypoint for running the FastAPI service.

usage() {
  cat >&2 <<'EOF'
Usage:
  scripts/dev/run-local.sh [--reload] [--dry-run]

Environment:
  SERVER_HOST (default: 0.0.0.0)
  SERVER_PORT (default: 8080)

Notes:
  - Loads configuration via the normal Settings loader, so `.env` in the repo root is supported.
EOF
}

reload=0
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reload)
      reload=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/../.." && pwd)"

host="${SERVER_HOST:-0.0.0.0}"
port="${SERVER_PORT:-8080}"

cmd=(python -m uvicorn zammad_pdf_archiver.asgi:app --host "${host}" --port "${port}")
if [[ "${reload}" -eq 1 ]]; then
  cmd+=(--reload)
fi

echo "Repo: ${repo_root}"
echo "Command:"
printf '  %q' "${cmd[@]}"
echo

if [[ "${dry_run}" -eq 1 ]]; then
  exit 0
fi

cd "${repo_root}"
export PYTHONPATH="${repo_root}/src"
exec "${cmd[@]}"
