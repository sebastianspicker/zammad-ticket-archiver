"""Exercise observable archive pipeline ordering without external services."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import chronikwerk.archiving.workflow as workflow
from chronikwerk.archiving.options import ArchiveRuntimeOptions
from chronikwerk.archiving.workflow import ArchiveAttempt, ArchivePipelineRequest
from chronikwerk.zammad.dto import TagList, Ticket


class _PipelineClient:
    """Return fixed ticket state while recording fetch order."""

    def __init__(self, tags: list[str]) -> None:
        self.events: list[str] = []
        self._tags = tags

    async def get_ticket(self, ticket_id: int) -> Ticket:
        self.events.append("fetch-ticket")
        return Ticket(id=ticket_id, number="20240042")

    async def list_tags(self, ticket_id: int) -> TagList:
        self.events.append("fetch-tags")
        return TagList(self._tags)


def _request(client: _PipelineClient, *, force_reprocess: bool = False) -> ArchivePipelineRequest:
    """Build the narrow pipeline request used by orchestration tests."""
    runtime = cast(
        ArchiveRuntimeOptions,
        SimpleNamespace(
            workflow=SimpleNamespace(trigger_tag="archive:ready", require_trigger_tag=True)
        ),
    )
    return ArchivePipelineRequest(
        client=client,  # type: ignore[arg-type]
        attempt=ArchiveAttempt(
            runtime=runtime,
            ticket_id=42,
            delivery_id="d-42",
            request_id="r-42",
        ),
        payload={},
        force_reprocess=force_reprocess,
    )


def test_pipeline_persists_before_terminal_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _PipelineClient(["archive:ready"])

    async def mark_processing(*_args: object, **_kwargs: object) -> None:
        client.events.append("mark-processing")

    def resolve_paths(*_args: object, **_kwargs: object) -> tuple[Path, Path]:
        client.events.append("resolve-paths")
        return Path("/archive/42.pdf"), Path("/archive/42.pdf.json")

    async def render_and_store(*_args: object, **_kwargs: object) -> object:
        client.events.append("store-pdf-and-sidecar")
        return object()

    async def finalize(*_args: object, **_kwargs: object) -> None:
        client.events.append("apply-terminal-tags")

    monkeypatch.setattr(workflow, "apply_processing", mark_processing)
    monkeypatch.setattr(workflow, "resolve_storage_paths", resolve_paths)
    monkeypatch.setattr(workflow, "render_and_store_ticket", render_and_store)
    monkeypatch.setattr(workflow, "finalize_success", finalize)

    outcome, observe_total = asyncio.run(workflow.run_ticket_pipeline(_request(client)))

    assert outcome.status == "processed"
    assert observe_total is True
    assert client.events == [
        "fetch-ticket",
        "fetch-tags",
        "mark-processing",
        "resolve-paths",
        "store-pdf-and-sidecar",
        "apply-terminal-tags",
    ]


def test_pipeline_skips_non_triggered_ticket_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _PipelineClient(["other:tag"])

    async def unexpected_mutation(*_args: object, **_kwargs: object) -> None:
        pytest.fail("a non-triggered ticket must not start archive processing")

    monkeypatch.setattr(workflow, "apply_processing", unexpected_mutation)

    outcome, observe_total = asyncio.run(workflow.run_ticket_pipeline(_request(client)))

    assert outcome.status == "skipped_not_triggered"
    assert outcome.ticket_id == 42
    assert observe_total is False
    assert client.events == ["fetch-ticket", "fetch-tags"]
