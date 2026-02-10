from __future__ import annotations

from datetime import UTC, datetime
from importlib import metadata

from fastapi import APIRouter, Request

router = APIRouter()


def _service_version() -> str:
    try:
        return metadata.version("zammad-pdf-archiver")
    except metadata.PackageNotFoundError:
        return "0.0.0"


@router.get("/healthz")
def healthz(request: Request) -> dict[str, str]:
    out: dict[str, str] = {"status": "ok", "time": datetime.now(UTC).isoformat()}
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not getattr(settings.observability, "healthz_omit_version", False):
        out["service"] = "zammad-pdf-archiver"
        out["version"] = _service_version()
    return out
