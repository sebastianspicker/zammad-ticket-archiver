"""Safe URL fetcher for WeasyPrint document assets (Bug #18)."""

# pylint: disable=import-outside-toplevel
from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse


class _SafeURLFetcher:
    """WeasyPrint-compatible fetcher: only data: and file under template_root."""

    def __init__(self, template_root: Path) -> None:
        self._root = template_root.resolve()

    def _file_path_from_url(self, parsed) -> Path:
        path = Path(unquote(parsed.path))
        if not path.is_absolute():
            return (self._root / path).resolve()
        return path.resolve()

    def _fetch_file_url(self, url: str, parsed):
        from weasyprint.urls import FatalURLFetchingError, URLFetcherResponse

        try:
            path = self._file_path_from_url(parsed)
            if self._root not in path.parents and path != self._root:
                raise FatalURLFetchingError(f"file URL outside template root: {url!r}")
            if not path.is_file():
                raise FatalURLFetchingError(f"file URL not a file: {url!r}")
        except (OSError, ValueError) as e:  # resolve() / is_file() failures
            raise FatalURLFetchingError(f"invalid file URL: {url!r}") from e

        body = path.read_bytes()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return URLFetcherResponse(
            url=url,
            body=body,
            headers={"Content-Type": mime_type},
            status=200,
        )

    def fetch(self, url: str, headers=None):
        """Fetch an allow-listed packaged asset while rejecting unsafe URL schemes."""
        from weasyprint.urls import (
            FatalURLFetchingError,
            URLFetcher,
        )

        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme == "data":
            return URLFetcher(allowed_protocols=("data",)).fetch(url, headers)
        if scheme == "file":
            return self._fetch_file_url(url, parsed)
        raise FatalURLFetchingError(f"URL scheme not allowed: {scheme!r}")

    def __call__(self, url: str, *args, **kwargs):
        headers = kwargs.get("headers") or kwargs.get("http_headers")
        return self.fetch(url, headers=headers)


def _safe_url_fetcher(template_root: Path) -> _SafeURLFetcher:
    """Return a WeasyPrint url_fetcher that only allows data: and file under template_root."""
    return _SafeURLFetcher(template_root)
