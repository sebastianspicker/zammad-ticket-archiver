"""Coordinate graceful shutdown state across incoming and running work."""

import asyncio
from threading import Event

_SHUTTING_DOWN = Event()
_TASKS: set[asyncio.Task] = set()


def is_shutting_down() -> bool:
    """Return True if the application is in the process of shutting down."""
    return _SHUTTING_DOWN.is_set()


def set_shutting_down() -> None:
    """Mark the application as shutting down to stop new work from being accepted."""
    _SHUTTING_DOWN.set()


def clear_shutting_down() -> None:
    """Reopen admission after a test or controlled lifecycle restart."""
    _SHUTTING_DOWN.clear()


def track_task(task: asyncio.Task) -> None:
    """Register a background task so it is awaited during graceful shutdown."""
    if task.done():
        return
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def _active_tasks_for_current_loop() -> set[asyncio.Task]:
    running_loop = asyncio.get_running_loop()
    stale_tasks = {task for task in _TASKS if task.done() or task.get_loop() is not running_loop}
    _TASKS.difference_update(stale_tasks)
    return {task for task in _TASKS if not task.done() and task.get_loop() is running_loop}


async def _await_or_cancel_tasks(loop_tasks: set[asyncio.Task], *, timeout: float) -> None:
    try:
        await asyncio.wait_for(asyncio.gather(*loop_tasks, return_exceptions=True), timeout=timeout)
    except TimeoutError:
        for task in loop_tasks:
            task.cancel()
        await asyncio.gather(*loop_tasks, return_exceptions=True)
    finally:
        _TASKS.difference_update(loop_tasks)


async def wait_for_tasks(timeout: float = 1.0) -> None:
    """Await all tracked background tasks, cancelling any that exceed the timeout."""
    if not _TASKS:
        return

    loop_tasks = _active_tasks_for_current_loop()
    if not loop_tasks:
        return
    await _await_or_cancel_tasks(loop_tasks, timeout=timeout)
