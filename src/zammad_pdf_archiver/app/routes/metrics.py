from __future__ import annotations

import hmac

from fastapi import APIRouter, Request
from starlette.responses import Response

from zammad_pdf_archiver.observability.metrics import render_latest

router = APIRouter()


def _metrics_unauthorized() -> Response:
    return Response(content="Unauthorized\n", status_code=401, media_type="text/plain")


@router.get("/metrics")
def metrics(request: Request) -> Response:
    settings = getattr(request.app.state, "settings", None)
    if settings is not None:
        token = settings.observability.metrics_bearer_token
        if token is not None:
            expected = token.get_secret_value().encode("utf-8")
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or len(auth) < 8:
                return _metrics_unauthorized()
            provided = auth[7:].strip().encode("utf-8")
            if not expected or not hmac.compare_digest(expected, provided):
                return _metrics_unauthorized()
    payload, content_type = render_latest()
    return Response(content=payload, headers={"Content-Type": content_type})

