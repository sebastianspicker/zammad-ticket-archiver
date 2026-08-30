"""Verify the document asset fetcher accepts only packaged-safe URLs."""

from __future__ import annotations

from pathlib import Path

import pytest
from weasyprint.urls import FatalURLFetchingError

from chronikwerk.documents.url_fetcher import _safe_url_fetcher


def test_document_fetcher_reads_template_files_and_rejects_network_or_escape(
    tmp_path: Path,
) -> None:
    stylesheet = tmp_path / "styles.css"
    stylesheet.write_text("body { color: black; }", encoding="utf-8")
    fetcher = _safe_url_fetcher(tmp_path)

    response = fetcher(stylesheet.as_uri())

    assert response.read() == b"body { color: black; }"
    assert response.content_type == "text/css"
    with pytest.raises(FatalURLFetchingError, match="scheme not allowed"):
        fetcher("https://example.invalid/style.css")
    with pytest.raises(FatalURLFetchingError, match="outside template root"):
        fetcher((tmp_path.parent / "outside.css").as_uri())
