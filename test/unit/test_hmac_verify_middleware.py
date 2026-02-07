from __future__ import annotations

import asyncio

from zammad_pdf_archiver.app.middleware.hmac_verify import _read_body


def test_read_body_returns_when_client_disconnects() -> None:
    async def receive() -> dict[str, object]:
        # Yield control so asyncio.wait_for can cancel reliably if this regresses.
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    chunks = asyncio.run(
        asyncio.wait_for(_read_body(receive, on_chunk=lambda _chunk: None), timeout=0.1)
    )
    assert chunks == []
