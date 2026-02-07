from __future__ import annotations

from datetime import UTC, datetime
from importlib import metadata

from fastapi import APIRouter

router = APIRouter()


def _service_version() -> str:
    try:
        return metadata.version("zammad-pdf-archiver")
    except metadata.PackageNotFoundError:
        return "0.0.0"


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "zammad-pdf-archiver",
        "version": _service_version(),
        "time": datetime.now(UTC).isoformat(),
    }
