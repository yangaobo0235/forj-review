import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from forj_review_assistant.bootstrap.application import create_app
from forj_review_assistant.runtime.scheduling.fair_scheduler import FairAsyncScheduler


def test_scheduler_close_cancels_running_and_pending_work_and_releases_thread():
    scheduler = FairAsyncScheduler(global_limit=1, per_job_limit=1)
    started, stopped = threading.Event(), threading.Event()

    async def running():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def pending():
        raise AssertionError("queued work must not run during shutdown")

    first = scheduler.submit("first", running)
    try:
        assert started.wait(2)
        second = scheduler.submit("second", pending)
        scheduler.close()
        assert stopped.is_set()
        assert first.cancelled() and second.cancelled()
        assert not scheduler._thread.is_alive()
        assert scheduler._loop.is_closed()
        with pytest.raises(RuntimeError, match="已关闭"):
            scheduler.submit("after-close", pending).result(timeout=1)
        scheduler.close()
    finally:
        scheduler.close()


def test_applications_have_isolated_runtimes_and_close_them():
    first, second = create_app(), create_app()
    assert not hasattr(first.state, "review_jobs")
    with TestClient(first) as a, TestClient(second) as b:
        assert a.get("/health").json() == b.get("/health").json() == {"status": "ok"}
        assert first.state.review_jobs is not second.state.review_jobs
        threads = [
            first.state.review_jobs.scheduler._thread,
            first.state.review_jobs.finalize_scheduler._thread,
            second.state.review_jobs.scheduler._thread,
            second.state.review_jobs.finalize_scheduler._thread,
        ]
        assert all(thread.is_alive() for thread in threads)
    assert all(not thread.is_alive() for thread in threads)
