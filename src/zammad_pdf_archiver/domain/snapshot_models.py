from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _SnapshotModel(BaseModel):
    # Keep snapshots stable and versionable by defining a strict schema.
    model_config = ConfigDict(extra="forbid", frozen=True)


class PartyRef(_SnapshotModel):
    id: int | None = None
    login: str | None = None
    email: str | None = None
    name: str | None = None


class TicketMeta(_SnapshotModel):
    id: int
    number: str
    title: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    customer: PartyRef | None = None
    owner: PartyRef | None = None
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class AttachmentMeta(_SnapshotModel):
    article_id: int
    attachment_id: int | None = None
    filename: str | None = None
    size: int | None = None
    content_type: str | None = None
    content: bytes | None = None  # optional binary (PRD §8.2); set when include_attachment_binary


class Article(_SnapshotModel):
    id: int
    created_at: datetime | None = None
    internal: bool = False
    sender: str | None = None
    subject: str | None = None
    body_html: str = ""
    body_text: str = ""
    attachments: list[AttachmentMeta] = Field(default_factory=list)


class Snapshot(_SnapshotModel):
    ticket: TicketMeta
    articles: list[Article]

