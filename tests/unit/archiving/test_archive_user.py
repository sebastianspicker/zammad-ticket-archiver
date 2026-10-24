"""Resolve the archive user from Zammad's real webhook and ticket shapes."""

from __future__ import annotations

from typing import Any

import pytest

from chronikwerk.archiving.path import determine_username
from chronikwerk.zammad.dto import Ticket
from tests.support.zammad_fixtures import load_zammad_fixture


def _ticket(**overrides: Any) -> Ticket:
    """Return the documented expanded ticket (owner alice, updated_by bob)."""
    return Ticket.model_validate({**load_zammad_fixture("ticket_show_expand.json"), **overrides})


def _current_agent(
    ticket: Ticket, payload: dict[str, Any], *, service_login: str | None = None
) -> str:
    """Resolve the archive user in current_agent mode."""
    return determine_username(
        ticket=ticket,
        payload=payload,
        custom_fields={"archive_user_mode": "current_agent"},
        mode_field_name="archive_user_mode",
        service_login=service_login,
    )


def test_current_agent_uses_the_webhook_ticket_updated_by() -> None:
    payload = load_zammad_fixture("webhook_trigger_payload.json")

    assert _current_agent(_ticket(), payload) == "carol"


def test_current_agent_falls_back_to_the_fetched_ticket_on_retry() -> None:
    assert _current_agent(_ticket(), {"ticket_id": 42}) == "bob"


def test_current_agent_ignores_the_system_login_in_the_payload() -> None:
    payload = load_zammad_fixture("webhook_trigger_payload.json")
    payload["ticket"]["updated_by"]["login"] = "-"

    assert _current_agent(_ticket(), payload) == "bob"


def test_current_agent_keeps_an_explicit_top_level_user_from_a_custom_payload() -> None:
    payload = {"ticket": {"id": 42}, "user": {"login": "dave"}}

    assert _current_agent(_ticket(), payload) == "dave"


def test_current_agent_without_any_login_fails() -> None:
    with pytest.raises(ValueError, match="ticket.updated_by.login"):
        _current_agent(_ticket(updated_by="-"), {"ticket_id": 42})


def test_owner_mode_uses_the_expanded_owner_login() -> None:
    username = determine_username(
        ticket=_ticket(),
        payload={},
        custom_fields={},
        mode_field_name="archive_user_mode",
    )

    assert username == "alice"


def test_current_agent_never_resolves_to_the_service_account_on_retry() -> None:
    # After a failed run the service's own error note can make it the ticket's last editor.
    ticket = _ticket(updated_by="chronikwerk-api")

    with pytest.raises(ValueError, match="ticket.updated_by.login is the Chronikwerk API user"):
        _current_agent(ticket, {"ticket_id": 42}, service_login="chronikwerk-api")


def test_current_agent_skips_the_service_account_in_the_payload() -> None:
    payload = load_zammad_fixture("webhook_trigger_payload.json")
    payload["ticket"]["updated_by"]["login"] = "Chronikwerk-API"

    assert _current_agent(_ticket(), payload, service_login="chronikwerk-api") == "bob"
