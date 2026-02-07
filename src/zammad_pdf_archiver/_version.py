from __future__ import annotations

from importlib import metadata


def _read_version() -> str:
    try:
        return metadata.version("zammad-pdf-archiver")
    except metadata.PackageNotFoundError:
        return "0.0.0"


__version__ = _read_version()

