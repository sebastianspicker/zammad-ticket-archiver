from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import Response

from zammad_pdf_archiver.observability.metrics import render_latest

router = APIRouter()


@router.get("/metrics")
def metrics(_request: Request) -> Response:
    payload, content_type = render_latest()
    return Response(content=payload, headers={"Content-Type": content_type})

