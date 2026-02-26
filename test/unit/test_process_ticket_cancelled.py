from __future__ import annotations

import asyncio

import pytest

import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module
from test.support.settings_factory import make_settings


class _FakeClient:
    def __init__(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def test_process_ticket_with_client_cancellation_does_not_run_error_flow(
    monkeypatch, tmp_path
) -> None:
    settings = make_settings(str(tmp_path))

    async def _cancelled_pipeline(**kwargs):  # noqa: ANN003, ARG001
        raise asyncio.CancelledError()

    called = {"error_handler": 0}

    async def _error_handler(**kwargs):  # noqa: ANN003, ARG001
        called["error_handler"] += 1
        return process_ticket_module.ProcessTicketResult(
            status="failed_permanent",
            ticket_id=1,
            classification="Permanent",
            message="should-not-run",
        )

    monkeypatch.setattr(process_ticket_module, "AsyncZammadClient", _FakeClient)
    monkeypatch.setattr(process_ticket_module, "_run_ticket_pipeline", _cancelled_pipeline)
    monkeypatch.setattr(process_ticket_module, "_handle_ticket_pipeline_exception", _error_handler)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            process_ticket_module._process_ticket_with_client(  # noqa: SLF001
                settings=settings,
                payload={"ticket_id": 1},
                ticket_id=1,
                delivery_id="delivery-1",
                request_id="req-1",
            )
        )

    assert called["error_handler"] == 0
