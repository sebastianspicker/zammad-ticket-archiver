from __future__ import annotations

import argparse
import json

from test.support.settings_factory import make_settings
from zammad_pdf_archiver import cli


def test_cmd_queue_stats_prints_json(monkeypatch, capsys, tmp_path) -> None:
    settings = make_settings(str(tmp_path))

    async def _stub_stats(_settings):
        return {"execution_backend": "inprocess", "queue_enabled": False}

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "get_queue_stats", _stub_stats)

    rc = cli.cmd_queue_stats(argparse.Namespace())
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == {"execution_backend": "inprocess", "queue_enabled": False}


def test_cmd_queue_drain_dlq_requires_redis_backend(monkeypatch, capsys, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"execution_backend": "inprocess"}},
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    rc = cli.cmd_queue_drain_dlq(argparse.Namespace(limit=5))
    assert rc == 1
    assert "requires workflow.execution_backend=redis_queue" in capsys.readouterr().err


def test_cmd_queue_drain_dlq_success(monkeypatch, capsys, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}},
    )

    async def _stub_drain(_settings, *, limit: int):
        assert limit == 7
        return 3

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "drain_dlq", _stub_drain)

    rc = cli.cmd_queue_drain_dlq(argparse.Namespace(limit=7))
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == {"status": "ok", "drained": 3}


def test_cmd_queue_history_prints_json(monkeypatch, capsys, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}},
    )

    async def _stub_history(_settings, *, limit: int, ticket_id: int | None = None):
        assert limit == 9
        assert ticket_id == 77
        return [{"status": "processed", "ticket_id": 77}]

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "read_history", _stub_history)

    rc = cli.cmd_queue_history(argparse.Namespace(limit=9, ticket_id=77))
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == {
        "status": "ok",
        "count": 1,
        "items": [{"status": "processed", "ticket_id": 77}],
    }
