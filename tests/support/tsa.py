"""Answer RFC 3161 requests over HTTP with the synthetic TSA for respx-mocked tests."""

from __future__ import annotations

import httpx
import respx
from asn1crypto import tsp

from tests.support.sealed_package import timestamper

TSA_URL = "https://tsa.example.local/tsr"


def tsa_reply(request: httpx.Request) -> httpx.Response:
    """Return a granted ``TimeStampResp`` for the posted ``TimeStampReq``."""
    query = tsp.TimeStampReq.load(request.content)
    response = timestamper().request_tsa_response(query)
    return httpx.Response(
        200,
        content=response.dump(),
        headers={"Content-Type": "application/timestamp-reply"},
    )


def mock_tsa(router: respx.MockRouter, url: str = TSA_URL) -> respx.Route:
    """Serve the synthetic TSA at ``url`` on ``router`` and return the route."""
    return router.post(url).mock(side_effect=tsa_reply)
