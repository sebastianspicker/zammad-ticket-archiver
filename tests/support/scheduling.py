"""Small scheduling spies for HTTP boundary tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SchedulingSpy:
    """Record calls accepted by the web layer without coupling to job internals."""

    accept: bool = True
    scheduled: list[tuple[str | None, dict[str, Any]]] = field(default_factory=list)
    retries: list[tuple[int, str | None]] = field(default_factory=list)

    def schedule(self, *, delivery_id: str | None, payload: dict[str, Any]) -> bool:
        self.scheduled.append((delivery_id, payload))
        return self.accept

    def schedule_batch(self, jobs: list[tuple[str | None, dict[str, Any]]]) -> bool:
        self.scheduled.extend(jobs)
        return self.accept

    def schedule_retry(self, *, ticket_id: int, request_id: str | None) -> bool:
        self.retries.append((ticket_id, request_id))
        return self.accept
