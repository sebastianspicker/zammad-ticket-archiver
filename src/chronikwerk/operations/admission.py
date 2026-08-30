"""Bound in-process admission so ticket work cannot grow unbounded."""

from __future__ import annotations

import asyncio

from chronikwerk.operations.metrics import (
    admission_pending,
    admission_rejected_total,
    admission_running,
)


class AdmissionClosed(Exception):
    """Raised when a reserved job cannot start during shutdown."""


class JobAdmission:
    """Bounded in-process admission for background ticket jobs.

    Reservations are made synchronously before ``asyncio.create_task``.  This
    keeps task objects bounded by ``max_pending + max_running`` while the
    condition limits active pipeline execution to ``max_running``.
    """

    def __init__(self, *, max_pending: int, max_running: int) -> None:
        self.max_pending = max_pending
        self.max_running = max_running
        self._pending = 0
        self._running = 0
        self._closing = False
        self._condition = asyncio.Condition()
        self._publish()

    @property
    def pending(self) -> int:
        """Return the number of admitted jobs waiting for worker capacity."""
        return self._pending

    @property
    def running(self) -> int:
        """Return the number of jobs currently holding a worker slot."""
        return self._running

    @property
    def closing(self) -> bool:
        """Return whether new background work is being rejected."""
        return self._closing

    def try_reserve(self, count: int = 1) -> bool:
        """Reserve slots without creating tasks; return false when full."""
        if count < 1 or self._closing:
            admission_rejected_total.inc(max(1, count))
            return False
        capacity = self.max_pending + self.max_running
        if self._pending + self._running + count > capacity:
            admission_rejected_total.inc(count)
            return False
        self._pending += count
        self._publish()
        return True

    async def acquire(self) -> None:
        """Move one reservation into running state, waiting for a worker slot."""
        async with self._condition:
            try:
                while self._running >= self.max_running:
                    if self._closing:
                        raise AdmissionClosed
                    await self._condition.wait()
                if self._closing:
                    raise AdmissionClosed
            except asyncio.CancelledError, AdmissionClosed:
                self._pending -= 1
                self._publish()
                self._condition.notify_all()
                raise

            self._pending -= 1
            self._running += 1
            self._publish()

    def cancel_reservation(self, count: int = 1) -> None:
        """Return pending reservations when a task cannot be created."""
        self._pending = max(0, self._pending - count)
        self._publish()

    async def release(self) -> None:
        """Release one running slot and wake a queued reservation."""
        async with self._condition:
            self._running = max(0, self._running - 1)
            self._publish()
            self._condition.notify_all()

    async def close(self) -> None:
        """Stop queued work from starting and wake all waiting reservations."""
        async with self._condition:
            self._closing = True
            self._publish()
            self._condition.notify_all()

    def _publish(self) -> None:
        admission_pending.set(self._pending)
        admission_running.set(self._running)
