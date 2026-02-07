from __future__ import annotations

import asyncio

from fastapi import BackgroundTasks
from starlette.requests import Request

from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
        }
    )


def test_ingest_does_not_block_on_processing(tmp_path, monkeypatch) -> None:
    """
    Regression: docs/architecture promise that POST /ingest returns 202 immediately and does
    not wait for the full processing pipeline (PDF render/sign/storage/Zammad updates).
    """
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    called: list[object] = []
    gate = asyncio.Event()

    async def _slow_process_ticket(delivery_id, payload, settings) -> None:
        called.append((delivery_id, payload, settings))
        await gate.wait()

    monkeypatch.setattr(ingest_route, "process_ticket", _slow_process_ticket)

    app = create_app(_test_settings(str(tmp_path)))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/ingest",
        "headers": [(b"x-zammad-delivery", b"delivery-bg-1")],
        "app": app,
    }
    request = Request(scope)
    request.state.request_id = "req-bg-1"

    background = BackgroundTasks()

    async def _call() -> None:
        response = await ingest_route.ingest(request, {"ticket": {"id": 123}}, background)
        assert response.status_code == 202

    # If /ingest awaited the job, this would time out.
    asyncio.run(asyncio.wait_for(_call(), timeout=0.2))
    assert called == []
    assert len(background.tasks) == 1
