#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  scripts/ops/verify-pdf.sh /path/to/file.pdf

Environment (optional):
  VERIFY_PDF_TRUST              Colon-separated list of PEM/DER cert files or directories to trust.
  VERIFY_PDF_TRUST_REPLACE=1    Replace default trust anchors with VERIFY_PDF_TRUST.
  VERIFY_PDF_OTHER_CERTS        Colon-separated list of extra PEM/DER certs to aid chain building.
  VERIFY_PDF_RETROACTIVE_REVINFO=1  Allow fetching revocation info retroactively (OCSP/CRL).
  VERIFY_PDF_SHOW_DETAILS=1     Print pyHanko validation details even on success.
EOF
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

pdf_path="$1"
if [[ ! -f "${pdf_path}" ]]; then
  echo "FAIL"  # keep a machine-readable first line
  echo "PDF not found (or not a regular file): ${pdf_path}" >&2
  exit 1
fi

validator=()
if command -v pyhanko >/dev/null 2>&1; then
  validator=(pyhanko)
elif command -v pyhanko-cli >/dev/null 2>&1; then
  validator=(pyhanko-cli)
elif command -v python3 >/dev/null 2>&1; then
  # Fallback: run a small Python validator that uses the pyHanko library directly.
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "${script_dir}/verify-pdf.py" ]] && python3 -c "import pyhanko" >/dev/null 2>&1; then
    validator=(python3 "${script_dir}/verify-pdf.py")
  fi
fi

if [[ ${#validator[@]} -eq 0 ]]; then
  echo "FAIL"
  echo "No validator available: install pyHanko CLI (pyhanko/pyhanko-cli) or ensure python3 + scripts/ops/verify-pdf.py exist." >&2
  exit 1
fi

args=()
if [[ "${validator[0]}" == "pyhanko" || "${validator[0]}" == "pyhanko-cli" ]]; then
  args+=(sign validate)

  if [[ "${VERIFY_PDF_TRUST_REPLACE:-}" == "1" ]]; then
    args+=(--trust-replace)
  fi

  if [[ -n "${VERIFY_PDF_TRUST:-}" ]]; then
    IFS=':' read -r -a _trust_paths <<<"${VERIFY_PDF_TRUST}"
    for p in "${_trust_paths[@]}"; do
      [[ -n "${p}" ]] || continue
      args+=(--trust "${p}")
    done
  fi

  if [[ -n "${VERIFY_PDF_OTHER_CERTS:-}" ]]; then
    IFS=':' read -r -a _other_certs <<<"${VERIFY_PDF_OTHER_CERTS}"
    for p in "${_other_certs[@]}"; do
      [[ -n "${p}" ]] || continue
      args+=(--other-certs "${p}")
    done
  fi

  if [[ "${VERIFY_PDF_RETROACTIVE_REVINFO:-}" == "1" ]]; then
    args+=(--retroactive-revinfo)
  fi

  # Human-readable output (we only print it on failure or when requested).
  args+=(--pretty-print)
fi

set +e
output="$("${validator[@]}" "${args[@]}" "${pdf_path}" 2>&1)"
status=$?
set -e

if [[ ${status} -eq 0 ]]; then
  echo "PASS"
  if [[ "${VERIFY_PDF_SHOW_DETAILS:-}" == "1" && -n "${output}" ]]; then
    echo "${output}"
  fi
  exit 0
fi

echo "FAIL"
if [[ -n "${output}" ]]; then
  echo "${output}" >&2
fi
exit 1
