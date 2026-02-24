import asyncio

_SHUTTING_DOWN = False
_TASKS: set[asyncio.Task] = set()


def is_shutting_down() -> bool:
    return _SHUTTING_DOWN


def set_shutting_down() -> None:
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True


def track_task(task: asyncio.Task) -> None:
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def wait_for_tasks(timeout: float = 25.0) -> None:
    if not _TASKS:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*_TASKS, return_exceptions=True), timeout=timeout)
    except asyncio.TimeoutError:
        pass
