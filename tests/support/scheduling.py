"""Small scheduling spies for HTTP boundary tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from chronikwerk.operations.history import EmptyHistory
from chronikwerk.operations.job import TicketJob
from chronikwerk.operations.scheduling import JournalAdmission


@dataclass
class SchedulingSpy:
    """Record calls accepted by the web layer without coupling to job internals."""

    accept: bool = True
    scheduled: list[TicketJob] = field(default_factory=list)
    retries: list[tuple[int, str | None]] = field(default_factory=list)
    accepting: bool = True
    admission: JournalAdmission = field(
        default_factory=lambda: JournalAdmission(
            pending=0, running=0, max_pending=1, max_running=1, closing=False
        )
    )
    history: EmptyHistory = field(default_factory=EmptyHistory)
    closed: bool = False

    async def schedule(self, job: TicketJob) -> bool:
        """Record one scheduled job and answer with the configured capacity result."""
        self.scheduled.append(job)
        return self.accept

    async def schedule_batch(self, jobs: list[TicketJob]) -> bool:
        """Record a scheduled job group and answer with the configured capacity result."""
        self.scheduled.extend(jobs)
        return self.accept

    async def schedule_retry(self, *, ticket_id: int, request_id: str | None) -> bool:
        """Record one operator retry and answer with the configured capacity result."""
        self.retries.append((ticket_id, request_id))
        return self.accept

    async def aclose(self) -> None:
        """Record application shutdown and stop accepting further work."""
        self.accepting = False
        self.closed = True
