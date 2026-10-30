"""Define the states, sources, rows and admission items of the request journal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal


class RequestState(StrEnum):
    """Lifecycle state of one journaled request."""

    WAITING = "waiting"
    COLLECTING = "collecting"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    FAILED = "failed"
    COALESCED = "coalesced"


class RequestSource(StrEnum):
    """Origin of one journaled request."""

    WEBHOOK = "webhook"
    RETRY = "retry"
    RECONCILE = "reconcile"
    MAIL_EVIDENCE = "mail_evidence"


class ZammadStatus(StrEnum):
    """Progress of the Zammad status note for a completed request."""

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AdmitItem:
    """One request offered to ``RequestJournal.admit_many``."""

    ticket_id: int
    source: RequestSource
    delivery_id: str | None = None
    evidence_json: str | None = None
    trigger_login: str | None = None


@dataclass(frozen=True, slots=True)
class AdmitResult:
    """Outcome of one admission attempt."""

    outcome: Literal["admitted", "duplicate_delivery", "coalesced", "full"]
    request_id: str | None
    active_request_id: str | None


@dataclass(frozen=True, slots=True)
class JournalRequest:
    """One row of the request journal."""

    request_id: str
    ticket_id: int
    delivery_id: str | None
    source: RequestSource
    received_at: datetime
    updated_at: datetime
    state: RequestState
    attempts: int
    next_attempt_at: datetime | None
    coalesced_into: str | None
    revision_id: str | None
    revision_number: int | None
    commit_step: str | None
    zammad_status: ZammadStatus
    error_code: str | None
    error_detail: str | None
    evidence_json: str | None
    context_json: str | None = None
    trigger_login: str | None = None
