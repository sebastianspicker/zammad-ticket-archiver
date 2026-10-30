"""Model the Zammad API payload subset needed for archival."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

# Zammad's system user (id 1) stands in for "no owner" and expands to this login.
_UNASSIGNED_LOGIN = "-"
_EXPANDED_USER_RELATIONS = ("owner", "updated_by", "customer")


class _ZammadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class UserRef(_ZammadModel):
    """Model the login field returned for a Zammad user reference."""

    login: str | None = None


class CustomerRef(_ZammadModel):
    """Model the identity fields returned for a ticket customer."""

    id: int | None = None
    login: str | None = None
    email: str | None = None


class Ticket(_ZammadModel):
    """
    Model the ticket fields required to assemble an archival snapshot.

    The gateway fetches tickets with ``expand=true``, so user relations arrive as login strings
    next to their ``*_id`` keys. Custom object attributes are top-level keys and are kept as
    extra fields; read them with ``attribute``.
    """

    model_config = ConfigDict(extra="allow")

    id: int
    number: str
    title: str | None = None

    owner: UserRef | None = None
    updated_by: UserRef | None = None
    customer: CustomerRef | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _expanded_user_relations(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        mapped = dict(data)
        for relation in _EXPANDED_USER_RELATIONS:
            login = mapped.get(relation)
            if not isinstance(login, str):
                continue
            login = login.strip()
            mapped[relation] = (
                None
                if login in ("", _UNASSIGNED_LOGIN)
                else {"id": mapped.get(f"{relation}_id"), "login": login}
            )
        return mapped

    def attribute(self, name: str) -> Any:
        """Return an undeclared top-level ticket attribute, such as a custom field."""
        return (self.model_extra or {}).get(name)


class AttachmentMeta(_ZammadModel):
    """Represent one attachment included in an archive snapshot."""

    id: int | None = None
    filename: str | None = None
    size: int | None = None
    content_type: str | None = None
    preferences: dict[str, Any] | None = None

    @field_validator("size", mode="before")
    @classmethod
    def _numeric_size(cls, value: Any) -> Any:
        # Zammad sends the size as a decimal string.
        if isinstance(value, str) and value.strip().isdecimal():
            return int(value.strip())
        return value


class Article(_ZammadModel):
    """Model a raw Zammad article; snapshot assembly sanitizes its body later."""

    id: int
    created_at: datetime | None = None
    internal: bool | None = None
    subject: str | None = None
    body: str | None = None
    updated_at: datetime | None = None
    content_type: str | None = None
    type: str | None = None
    sender: str | None = None

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    cc: str | None = None
    message_id: str | None = None
    in_reply_to: str | None = None

    attachments: list[AttachmentMeta] | None = None


class TagList(RootModel[list[str]]):
    """Represents the Zammad tag list response."""
