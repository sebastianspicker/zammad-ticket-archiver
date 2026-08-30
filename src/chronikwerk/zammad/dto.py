"""Model the Zammad API payload subset needed for archival."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel


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


class TicketPreferences(_ZammadModel):
    """Carry custom-field data embedded in a Zammad ticket payload."""

    custom_fields: dict[str, Any] | None = None


class Ticket(_ZammadModel):
    """Model the ticket fields required to assemble an archival snapshot."""

    id: int
    number: str
    title: str | None = None

    owner: UserRef | None = None
    updated_by: UserRef | None = None
    customer: CustomerRef | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    preferences: TicketPreferences | None = None


class AttachmentMeta(_ZammadModel):
    """Represent one attachment included in an archive snapshot."""

    id: int | None = None
    filename: str | None = None
    size: int | None = None
    content_type: str | None = None
    preferences: dict[str, Any] | None = None


class Article(_ZammadModel):
    """Model a raw Zammad article; snapshot assembly sanitizes its body later."""

    id: int
    created_at: datetime | None = None
    internal: bool | None = None
    subject: str | None = None
    body: str | None = None
    content_type: str | None = None

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None

    attachments: list[AttachmentMeta] | None = None


class TagList(RootModel[list[str]]):
    """Represents the Zammad tag list response."""
