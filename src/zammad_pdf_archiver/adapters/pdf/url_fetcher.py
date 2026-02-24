"""Safe URL fetcher for WeasyPrint: blocks file:// outside template root (Bug #18)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse


class _SafeURLFetcher:
    """WeasyPrint-compatible fetcher: only data: and file under template_root."""

    def __init__(self, template_root: Path) -> None:
        self._root = template_root.resolve()

    def fetch(self, url: str, headers=None):
        from weasyprint.urls import (  # type: ignore[import-untyped]
            FatalURLFetchingError,
            URLFetcher,
            URLFetcherResponse,
        )

        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme == "data":
            return URLFetcher(allowed_protocols=("data",)).fetch(url, headers)
        if scheme == "file":
            path = Path(unquote(parsed.path))
            if not path.is_absolute():
                path = (self._root / path).resolve()
            else:
                path = path.resolve()
            try:
                if self._root not in path.parents and path != self._root:
                    raise FatalURLFetchingError(
                        f"file URL outside template root: {url!r}"
                    )
                if not path.is_file():
                    raise FatalURLFetchingError(f"file URL not a file: {url!r}")
            except FatalURLFetchingError:
                raise
            except Exception as e:
                raise FatalURLFetchingError(f"invalid file URL: {url!r}") from e
            body = path.read_bytes()
            return URLFetcherResponse(url=url, body=body, status=200)
        raise FatalURLFetchingError(f"URL scheme not allowed: {scheme!r}")

    def __call__(self, url: str, *args, **kwargs):
        headers = kwargs.get("headers") or kwargs.get("http_headers")
        return self.fetch(url, headers=headers)


def _safe_url_fetcher(template_root: Path) -> _SafeURLFetcher:
    """Return a WeasyPrint url_fetcher that only allows data: and file under template_root."""
    return _SafeURLFetcher(template_root)
