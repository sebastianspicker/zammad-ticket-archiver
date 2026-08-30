"""Operational async helpers for synchronous work that cannot be stopped."""

from __future__ import annotations

import asyncio
from collections.abc import Callable


async def _wait_for_worker_once[T](
    worker: asyncio.Task[T], cancellation: asyncio.CancelledError
) -> bool:
    """Wait once and report whether cancellation interrupted unfinished work."""
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        return not worker.done()
    except Exception as worker_error:  # pylint: disable=broad-exception-caught
        cancellation.add_note(
            "Synchronous work also failed while cancellation was pending: "
            f"{type(worker_error).__name__}"
        )
    return False


async def _await_worker_after_cancellation[T](
    worker: asyncio.Task[T], cancellation: asyncio.CancelledError
) -> None:
    while await _wait_for_worker_once(worker, cancellation):
        pass


async def run_sync_cancellation_safe[**P, T](
    function: Callable[P, T],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """Run blocking work off-loop and do not detach it when the caller is cancelled.

    ``asyncio.to_thread`` cannot stop the worker thread. Waiting for the worker
    before propagating cancellation keeps locks and admission slots held until
    filesystem or cryptographic side effects have actually finished.
    """
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as cancellation:
        await _await_worker_after_cancellation(worker, cancellation)
        raise
