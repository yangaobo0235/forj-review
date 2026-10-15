"""跨审核任务的公平异步调度器。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class _WorkItem:
    job_id: str
    factory: Callable[[], Awaitable[Any]]
    future: concurrent.futures.Future[Any]


class FairAsyncScheduler:
    """在一个事件循环中按任务轮转，并限制单任务及全局并发。"""

    def __init__(self, *, global_limit: int, per_job_limit: int) -> None:
        if global_limit <= 0 or per_job_limit <= 0:
            raise ValueError("并发限制必须大于零")
        self.global_limit = global_limit
        self.per_job_limit = min(per_job_limit, global_limit)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: dict[str, deque[_WorkItem]] = {}
        self._order: deque[str] = deque()
        self._active_by_job: dict[str, int] = {}
        self._active_total = 0
        self._tasks: dict[str, set[asyncio.Task[None]]] = {}
        self._last_dispatched_job: str | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("公平调度器启动失败")

    def submit(
        self,
        job_id: str,
        factory: Callable[[], Awaitable[Any]],
    ) -> concurrent.futures.Future[Any]:
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        loop = self._loop
        if loop is None:
            future.set_exception(RuntimeError("公平调度器尚未启动"))
            return future
        loop.call_soon_threadsafe(
            self._enqueue,
            _WorkItem(job_id=job_id, factory=factory, future=future),
        )
        return future

    def cancel_pending(self, job_id: str) -> None:
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._cancel_pending, job_id)

    def cancel(self, job_id: str) -> None:
        """取消排队和运行中的协程，使已放弃的审核立即释放调度槽位。"""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._cancel, job_id)

    def _cancel(self, job_id: str) -> None:
        self._cancel_pending(job_id)
        for task in tuple(self._tasks.get(job_id, ())):
            task.cancel()

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    def _enqueue(self, item: _WorkItem) -> None:
        queue = self._queues.setdefault(item.job_id, deque())
        was_empty = not queue
        queue.append(item)
        if was_empty and item.job_id not in self._order:
            self._order.append(item.job_id)
        self._dispatch()

    def _cancel_pending(self, job_id: str) -> None:
        queue = self._queues.pop(job_id, deque())
        self._order = deque(item for item in self._order if item != job_id)
        for item in queue:
            item.future.cancel()

    def _next_item(self) -> _WorkItem | None:
        checks = len(self._order)
        fallback: str | None = None
        for _ in range(checks):
            job_id = self._order.popleft()
            queue = self._queues.get(job_id)
            if not queue:
                self._queues.pop(job_id, None)
                continue
            if self._active_by_job.get(job_id, 0) >= self.per_job_limit:
                self._order.append(job_id)
                continue
            # 有其它可运行任务时，不让刚刚获得槽位的任务连续占用新槽位。
            # 如果它是唯一可运行任务，循环结束后仍会通过 fallback 执行。
            if job_id == self._last_dispatched_job and checks > 1:
                fallback = job_id
                self._order.append(job_id)
                continue
            item = queue.popleft()
            if queue:
                self._order.append(job_id)
            else:
                self._queues.pop(job_id, None)
            return item
        if fallback is not None:
            queue = self._queues.get(fallback)
            if queue and self._active_by_job.get(fallback, 0) < self.per_job_limit:
                try:
                    self._order.remove(fallback)
                except ValueError:
                    pass
                item = queue.popleft()
                if queue:
                    self._order.append(fallback)
                else:
                    self._queues.pop(fallback, None)
                return item
        return None

    def _dispatch(self) -> None:
        while self._active_total < self.global_limit:
            item = self._next_item()
            if item is None:
                return
            self._active_total += 1
            self._active_by_job[item.job_id] = self._active_by_job.get(item.job_id, 0) + 1
            self._last_dispatched_job = item.job_id
            task = asyncio.create_task(self._run(item))
            self._tasks.setdefault(item.job_id, set()).add(task)
            task.add_done_callback(lambda completed, current=item: self._finished(current, completed))

    async def _run(self, item: _WorkItem) -> None:
        try:
            if not item.future.cancelled():
                result = await item.factory()
                if not item.future.done():
                    item.future.set_result(result)
        except asyncio.CancelledError:
            item.future.cancel()
        except Exception as exc:  # noqa: BLE001 - forward worker failures to its Future
            if not item.future.done():
                item.future.set_exception(exc)

    def _finished(self, item: _WorkItem, task: asyncio.Task[None]) -> None:
        # done 回调也覆盖“协程尚未开始就取消”，不能只依赖协程内部的 finally。
        if task.cancelled():
            item.future.cancel()
        self._tasks[item.job_id].discard(task)
        if not self._tasks[item.job_id]:
            self._tasks.pop(item.job_id)
        self._active_total -= 1
        active = self._active_by_job.get(item.job_id, 1) - 1
        if active > 0:
            self._active_by_job[item.job_id] = active
        else:
            self._active_by_job.pop(item.job_id, None)
        self._dispatch()
