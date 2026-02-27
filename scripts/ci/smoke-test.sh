#!/usr/bin/env bash
set -euo pipefail

# Make executable:
#   chmod +x scripts/ci/smoke-test.sh
#
# Purpose:
#   Minimal CI smoke test for repo health (bootstrap phase).

echo "Smoke test: repo structure sanity"

required_paths=(
  "README.md"
  "pyproject.toml"
  "docs/01-architecture.md"
  "config/config.example.yaml"
  "src/zammad_pdf_archiver/templates/default/ticket.html"
  ".github/workflows/ci.yml"
)

for p in "${required_paths[@]}"; do
  if [[ ! -e "$p" ]]; then
    echo "Missing required path: $p" >&2
    exit 1
  fi
done

echo "OK."
