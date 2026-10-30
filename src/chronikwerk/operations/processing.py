"""Define the capability that turns one leased journal request into a final state."""

from __future__ import annotations

from typing import Protocol

from chronikwerk.operations.journal import JournalRequest


class RequestProcessor(Protocol):
    """Archive work consumed by ``JournaledSchedulingService``.

    ``process`` receives a COLLECTING row and owns its journal transitions until the row is
    terminal (COMPLETE/FAILED) or back in WAITING (``fail_transient``). ``resume`` receives a
    VERIFYING row found at startup and continues its commit at ``commit_step``. When either
    method raises, or returns while the row is still COLLECTING or VERIFYING, the scheduler
    records ``fail_transient(error_code="internal_error")``. Cancellation (shutdown) leaves the
    row unchanged for the next start.

    The scheduler's maintenance task calls ``publish_pending`` for COMPLETE rows whose Zammad
    status is PENDING or FAILED and ``report_failure`` for FAILED rows whose Zammad status is
    PENDING (failures the scheduler or a restart recorded). Exceptions are logged and the row
    is offered again at the next maintenance run.
    """

    async def process(self, request: JournalRequest) -> None:
        """Archive one leased COLLECTING request."""
        ...

    async def resume(self, request: JournalRequest) -> None:
        """Finish the commit of one VERIFYING request interrupted by a restart."""
        ...

    async def publish_pending(self, request: JournalRequest) -> None:
        """Retry the success tags and note of a COMPLETE row whose Zammad update is open."""
        ...

    async def report_failure(self, request: JournalRequest) -> None:
        """Post the failure note and ``pdf:error`` for a FAILED row, then mark Zammad DONE."""
        ...
