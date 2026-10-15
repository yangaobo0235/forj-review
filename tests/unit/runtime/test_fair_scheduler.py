import asyncio
import threading
import time
from collections import defaultdict

from forj_review_assistant.runtime.scheduling.fair_scheduler import FairAsyncScheduler


def test_scheduler_enforces_global_and_per_job_limits() -> None:
    scheduler = FairAsyncScheduler(global_limit=4, per_job_limit=2)
    release = threading.Event()
    lock = threading.Lock()
    active_by_job: dict[str, int] = defaultdict(int)
    max_by_job: dict[str, int] = defaultdict(int)
    active_total = 0
    max_total = 0

    async def work(job_id: str) -> str:
        nonlocal active_total, max_total
        with lock:
            active_total += 1
            active_by_job[job_id] += 1
            max_total = max(max_total, active_total)
            max_by_job[job_id] = max(max_by_job[job_id], active_by_job[job_id])
        await asyncio.to_thread(release.wait)
        with lock:
            active_total -= 1
            active_by_job[job_id] -= 1
        return job_id

    futures = [
        scheduler.submit(job_id, lambda current=job_id: work(current))
        for job_id in ("a", "a", "a", "a", "b", "b", "b", "b", "c", "c")
    ]
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with lock:
            if active_total == 4:
                break
        time.sleep(0.01)

    with lock:
        assert max_total == 4
        assert all(value <= 2 for value in max_by_job.values())
        assert len([job_id for job_id, count in active_by_job.items() if count]) >= 2
    release.set()
    assert [future.result(timeout=2) for future in futures].count("c") == 2


def test_scheduler_round_robins_waiting_jobs() -> None:
    scheduler = FairAsyncScheduler(global_limit=1, per_job_limit=1)
    release_first = threading.Event()
    started: list[str] = []
    lock = threading.Lock()

    async def work(label: str, wait: bool = False) -> str:
        with lock:
            started.append(label)
        if wait:
            await asyncio.to_thread(release_first.wait)
        return label

    first = scheduler.submit("a", lambda: work("a-1", True))
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and not started:
        time.sleep(0.005)
    second_a = scheduler.submit("a", lambda: work("a-2"))
    first_b = scheduler.submit("b", lambda: work("b-1"))
    time.sleep(0.02)
    release_first.set()

    assert first.result(timeout=2) == "a-1"
    assert first_b.result(timeout=2) == "b-1"
    assert second_a.result(timeout=2) == "a-2"
    assert started == ["a-1", "b-1", "a-2"]
