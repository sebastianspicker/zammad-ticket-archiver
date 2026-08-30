"""Exercise managed configuration persistence through its public store contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chronikwerk.configuration.errors import RevisionConflict
from chronikwerk.configuration.revisions import ManagedConfigStore


def test_store_stages_lists_and_restores_a_revision_chain(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin-state", keep_revisions=3)
    initial = store.current_revision()

    first = store.stage(
        {"workflow": {"trigger_tag": "archive:ready"}},
        expected_revision=initial,
        request_id="request-1",
    )
    second = store.stage(
        {"pdf": {"locale": "en-GB"}},
        expected_revision=first["revision"],
        request_id="request-2",
    )

    assert store.load() == {"pdf": {"locale": "en-GB"}}
    assert [item["revision"] for item in store.list_revisions()] == [
        second["revision"],
        first["revision"],
    ]
    assert store.revision_overlay(first["revision"]) == {
        "workflow": {"trigger_tag": "archive:ready"}
    }

    restored = store.restore(
        first["revision"],
        expected_revision=second["revision"],
        request_id="request-3",
    )

    assert restored["previous_revision"] == second["revision"]
    assert store.load() == {"workflow": {"trigger_tag": "archive:ready"}}
    assert not list(store.state_dir.rglob(".*"))
    assert (store.overlay_path.stat().st_mode & 0o777) == 0o600


def test_store_rejects_stale_revisions_and_accepts_legacy_overlay_payload(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin-state")
    initial = store.current_revision()
    store.overlay_path.write_text(
        json.dumps({"workflow": {"require_tag": False}}),
        encoding="utf-8",
    )

    assert store.load() == {"workflow": {"require_tag": False}}
    assert store.current_revision() != initial
    with pytest.raises(RevisionConflict, match="revision changed"):
        store.stage(
            {"workflow": {"trigger_tag": "archive:ready"}},
            expected_revision=initial,
            request_id="request-stale",
        )
