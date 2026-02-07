#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast


def _split_paths(value: str) -> list[Path]:
    parts = [p for p in value.split(":") if p]
    return [Path(p).expanduser() for p in parts]


def _iter_cert_files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file():
                    out.append(child)
        else:
            out.append(p)
    return out


def _load_certs_from_paths(paths: list[Path]) -> list[object]:
    # Cert objects are asn1crypto.x509.Certificate instances; keep typing loose to
    # avoid importing pyHanko at module import time.
    from pyhanko.keys import load_certs_from_pemder_data

    certs: list[object] = []
    for cert_file in _iter_cert_files(paths):
        try:
            data = cert_file.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"Failed to read certificate file: {cert_file}") from exc

        try:
            certs.extend(list(load_certs_from_pemder_data(data)))
        except Exception as exc:  # noqa: BLE001 - report parsing issues clearly for ops
            raise RuntimeError(f"Failed to parse certificate(s) in: {cert_file}") from exc
    return certs


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: scripts/ops/verify-pdf.py /path/to/file.pdf", file=sys.stderr)
        return 2

    pdf_path = Path(sys.argv[1])
    if not pdf_path.is_file():
        print(f"PDF not found (or not a regular file): {pdf_path}", file=sys.stderr)
        return 1

    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko_certvalidator import ValidationContext
    except Exception as exc:  # noqa: BLE001 - missing deps / import errors
        print(
            "pyHanko validation libraries are not available in this Python environment.",
            file=sys.stderr,
        )
        print(f"Import error: {exc}", file=sys.stderr)
        return 2

    trust_paths = _split_paths(os.environ.get("VERIFY_PDF_TRUST", "")) if os.environ.get(
        "VERIFY_PDF_TRUST"
    ) else []
    other_paths = _split_paths(os.environ.get("VERIFY_PDF_OTHER_CERTS", "")) if os.environ.get(
        "VERIFY_PDF_OTHER_CERTS"
    ) else []

    trust_replace = os.environ.get("VERIFY_PDF_TRUST_REPLACE", "") == "1"
    retroactive_revinfo = os.environ.get("VERIFY_PDF_RETROACTIVE_REVINFO", "") == "1"
    show_details = os.environ.get("VERIFY_PDF_SHOW_DETAILS", "") == "1"

    try:
        trust_certs = _load_certs_from_paths(trust_paths) if trust_paths else []
        other_certs = _load_certs_from_paths(other_paths) if other_paths else []
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    vc_kwargs: dict[str, Any] = {
        "allow_fetching": True,
        "retroactive_revinfo": retroactive_revinfo,
    }
    if trust_certs:
        if trust_replace:
            vc_kwargs["trust_roots"] = trust_certs
        else:
            vc_kwargs["extra_trust_roots"] = trust_certs
    if other_certs:
        vc_kwargs["other_certs"] = other_certs

    try:
        vc = ValidationContext(**cast(Any, vc_kwargs))
    except TypeError:
        # Backwards compatibility with older ValidationContext signatures
        vc_kwargs.pop("retroactive_revinfo", None)
        vc = ValidationContext(**cast(Any, vc_kwargs))

    ok = True
    with pdf_path.open("rb") as doc:
        reader = PdfFileReader(doc)
        embedded_sigs = list(getattr(reader, "embedded_signatures", []) or [])
        if not embedded_sigs:
            print("No embedded PDF signatures found.", file=sys.stderr)
            return 1

        for idx, embedded_sig in enumerate(embedded_sigs, start=1):
            try:
                status = validate_pdf_signature(embedded_sig, vc)
            except Exception as exc:  # noqa: BLE001 - report validation error and fail
                ok = False
                field_name = getattr(embedded_sig, "field_name", None)
                where = f" (field {field_name})" if field_name else ""
                print(f"Signature #{idx}{where}: validation error: {exc}", file=sys.stderr)
                continue

            bottom_line = bool(getattr(status, "bottom_line", False))
            ok = ok and bottom_line

            if show_details or not bottom_line:
                field_name = getattr(embedded_sig, "field_name", None)
                where = f" (field {field_name})" if field_name else ""
                print(f"Signature #{idx}{where}:", file=sys.stderr)
                try:
                    print(status.pretty_print_details(), file=sys.stderr)
                except Exception:
                    print(str(status), file=sys.stderr)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
