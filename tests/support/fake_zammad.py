"""In-memory Zammad client double that records every call for pipeline tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from chronikwerk.zammad.dto import TagList, Ticket
from chronikwerk.zammad.gateway import AsyncZammadClient


class FakeZammad:
    """Record gateway calls and keep a mutable tag set like a real ticket."""

    def __init__(self, *, tags: list[str], ticket: Ticket | None = None) -> None:
        self.tags: set[str] = set(tags)
        self.ticket = ticket or default_ticket()
        self.calls: list[tuple[str, ...]] = []
        self.notes: list[tuple[str, str]] = []
        self.on_add_tag: Callable[[str], None] | None = None
        self.fail_note = False
        self.current_login = "chronikwerk"

    @property
    def client(self) -> AsyncZammadClient:
        """Return this double typed as the gateway client accepted by the processor."""
        return cast(AsyncZammadClient, self)

    @property
    def names(self) -> list[str]:
        """Return the recorded call names with tag arguments appended."""
        return [":".join(call) for call in self.calls]

    async def get_current_user_login(self) -> str:
        """Return the login of the API user this client authenticates as."""
        self.calls.append(("get_current_user_login",))
        return self.current_login

    async def list_tags(self, _ticket_id: int) -> TagList:
        """Return the current tag set."""
        self.calls.append(("list_tags",))
        return TagList(sorted(self.tags))

    async def add_tag(self, _ticket_id: int, tag: str) -> None:
        """Record and apply a tag addition, running the optional hook first."""
        self.calls.append(("add_tag", tag))
        if self.on_add_tag is not None:
            self.on_add_tag(tag)
        self.tags.add(tag)

    async def remove_tag(self, _ticket_id: int, tag: str) -> None:
        """Record and apply a tag removal."""
        self.calls.append(("remove_tag", tag))
        self.tags.discard(tag)

    async def create_internal_article(self, _ticket_id: int, subject: str, body: str) -> Any:
        """Record a posted note, optionally failing like an unavailable Zammad."""
        self.calls.append(("create_internal_article",))
        if self.fail_note:
            raise RuntimeError("note endpoint unavailable")
        self.notes.append((subject, body))


def default_ticket() -> Ticket:
    """Build an expanded ticket whose attributes place the archive under alice/Customers/ACME."""
    return Ticket.model_validate(
        {
            "id": 1,
            "number": "10001",
            "title": "Printer broken",
            "owner_id": 4,
            "owner": "alice",
            "preferences": {},
            "archive_path": "Customers > ACME",
        }
    )
