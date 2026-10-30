"""Verify runtime ownership and the preservation startup requirements at the composition root."""

import asyncio
import stat

import pytest

from chronikwerk import composition
from chronikwerk.composition_preservation import startup_problems, timestamp_options
from chronikwerk.configuration.validation import ConfigValidationError
from chronikwerk.operations.history import JournalHistoryView
from chronikwerk.operations.journal import JournalStateError
from chronikwerk.operations.scheduling import JournaledSchedulingService
from tests.support.preservation_settings import make_runtime_settings
from tests.support.sealed_package import write_anchors
from tests.support.settings_factory import make_settings


def _install_fakes(monkeypatch, settings, clients: list, events: list) -> None:
    """Replace configuration loading, logging and the Zammad client."""

    class Client:
        def __init__(self, *, connection):
            assert connection == settings.zammad_connection
            self.closed = False
            clients.append(self)

        async def aclose(self):
            self.closed = True
            events.append("client")

    monkeypatch.setattr(composition, "load_settings", lambda: settings)
    monkeypatch.setattr(composition, "configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(composition, "AsyncZammadClient", Client)


def _issue_paths(error: ConfigValidationError) -> set[str]:
    """Return the setting paths a startup refusal names."""
    return {issue.path for issue in error.issues}


def test_runtime_wires_the_journal_and_lifespan_closes_client_then_databases(
    monkeypatch, tmp_path
) -> None:
    settings = make_runtime_settings(tmp_path)
    clients: list = []
    events: list = []
    _install_fakes(monkeypatch, settings, clients, events)

    configured, app = composition.build_runtime_application()
    scheduler = app.state.scheduler
    assert configured is settings
    assert len(clients) == 1
    assert isinstance(scheduler, JournaledSchedulingService)
    assert isinstance(app.state.history, JournalHistoryView)
    assert app.state.history is scheduler.history
    # The WAITING cap is admission.max_pending itself; back-off retries count towards it.
    assert scheduler.admission.max_pending == settings.admission.max_pending
    temp_dir = settings.capture.temp_dir
    assert stat.S_IMODE(temp_dir.stat().st_mode) == 0o700

    async def scenario():
        async with app.router.lifespan_context(app):
            assert not clients[0].closed
            assert scheduler.history.read(10) == []
        assert clients[0].closed
        assert not scheduler.accepting

    asyncio.run(scenario())
    assert events == ["client"]
    with pytest.raises(JournalStateError):
        scheduler.history.read(10)


def test_startup_refuses_and_lists_every_unmet_requirement(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path / "archive"),
        overrides={
            "journal": {"path": str(tmp_path / "missing" / "journal.sqlite3")},
            "retention": {"policy_path": str(tmp_path / "retention.yaml")},
        },
    )
    clients: list = []
    _install_fakes(monkeypatch, settings, clients, [])

    with pytest.raises(ConfigValidationError) as raised:
        composition.build_runtime_application()

    assert _issue_paths(raised.value) >= {
        "seal.pfx_path",
        "verify.trust_anchors_path",
        "signing.timestamp.enabled",
        "signing.timestamp.rfc3161.tsa_url",
        "retention.policy_path",
        "journal.path",
    }
    assert clients == []
    assert not (tmp_path / "missing").exists()


def test_s3_backend_is_refused_until_the_store_exists(tmp_path) -> None:
    settings = make_runtime_settings(
        tmp_path,
        overrides={
            "storage": {
                "backend": "s3",
                "s3": {"bucket": "archive", "access_key_id": "id", "secret_access_key": "key"},
            }
        },
    )

    [issue] = startup_problems(settings)

    assert issue.path == "storage.backend"
    assert "S3 archive store is not available in this build" in issue.message


def test_seal_that_does_not_chain_to_the_trust_anchors_is_refused(tmp_path) -> None:
    other = write_anchors(tmp_path, trusted=False)
    settings = make_runtime_settings(
        tmp_path, overrides={"verify": {"trust_anchors_path": str(other)}}
    )

    problems = startup_problems(settings)

    assert [issue.path for issue in problems] == ["seal.pfx_path"]
    assert "does not chain to a trust anchor" in problems[0].message


def test_wrong_seal_password_and_bad_policy_are_reported_together(tmp_path) -> None:
    settings = make_runtime_settings(tmp_path, overrides={"seal": {"pfx_password": "wrong"}})
    settings.retention.policy_path.write_text("record_classes: {}\n", encoding="utf-8")

    assert {issue.path for issue in startup_problems(settings)} == {
        "seal.pfx_path",
        "retention.policy_path",
    }


def test_build_area_below_the_archive_root_is_refused(tmp_path) -> None:
    settings = make_runtime_settings(
        tmp_path, overrides={"capture": {"temp_dir": str(tmp_path / "archive" / "tmp")}}
    )

    [issue] = startup_problems(settings)

    assert issue.path == "capture.temp_dir"


def test_tsa_username_is_unwrapped_only_at_the_composition_boundary(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "signing": {"timestamp": {"rfc3161": {"user": "tsa-user", "password": "tsa-password"}}}
        },
    )

    options = timestamp_options(settings)

    configured_user = settings.signing.timestamp.rfc3161.user
    assert configured_user is not None
    assert configured_user.get_secret_value() == "tsa-user"
    assert options.user == "tsa-user"
    assert options.password == "tsa-password"
