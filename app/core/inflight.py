"""Bounded, event-loop-local shared work; a cancelled waiter cannot cancel peers."""
import asyncio
from dataclasses import dataclass
from weakref import WeakKeyDictionary


class BusyError(ValueError):
    pass


@dataclass
class _Work:
    task: asyncio.Task
    waiters: int = 0


class SingleFlight:
    def __init__(self, limit: int = 64):
        self.limit = limit
        self._loops = WeakKeyDictionary()

    async def run(self, key, factory):
        pending = self._loops.setdefault(asyncio.get_running_loop(), {})
        work = pending.get(key)
        if work is None:
            if len(pending) >= self.limit:
                raise BusyError('刷新任务较多，请稍后重试')
            work = _Work(asyncio.create_task(factory()))
            pending[key] = work
        work.waiters += 1
        try:
            return await asyncio.shield(work.task)
        finally:
            work.waiters -= 1
            if work.waiters == 0:
                if pending.get(key) is work:
                    del pending[key]
                if not work.task.done():
                    work.task.cancel()
                # Retrieve errors and complete resource cleanup even when all
                # callers disconnect; never leave orphan fetch tasks running.
                await asyncio.gather(work.task, return_exceptions=True)
