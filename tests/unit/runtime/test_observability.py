import asyncio
import logging
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forj_review_service.observability.context import current_log_context, log_context
from forj_review_service.observability.http import RequestLoggingMiddleware
from forj_review_service.observability.logging import (
    BoundedDailyFileHandler,
    SafeFormatter,
    configured_logging,
)
from forj_review_service.runtime.scheduling.fair_scheduler import FairAsyncScheduler


def test_production_sinks_include_all_namespaces_once_and_restore(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REVIEW_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("REVIEW_LOG_REQUIRED", "true")
    monkeypatch.setenv("REVIEW_LOG_LEVEL", "INFO")
    app = logging.getLogger("forj_review_service")
    previous = list(app.handlers), app.level, app.propagate
    for run in range(2):
        with configured_logging(), log_context(trace_id=f"trace-{run}", job_id="job-1"):
            logging.getLogger("forj_review_service.runtime.workflow.graph").info(
                "test graph event %d", run
            )
            logging.getLogger("forj_review_service.capabilities.registry").info(
                "test capability event %d", run
            )
            logging.getLogger("uvicorn.error").info("test server event %d", run)
        assert (app.handlers, app.level, app.propagate) == previous
    file_text = (tmp_path / "review-service.log").read_text(encoding="utf-8")
    console_text = capsys.readouterr().err
    for text in (file_text, console_text):
        for run in range(2):
            for event in ("graph", "capability", "server"):
                line = next(
                    line for line in text.splitlines() if f"test {event} event {run}" in line
                )
                assert f"trace_id=trace-{run} job_id=job-1" in line
                assert text.count(f"test {event} event {run}") == 1


def test_formatter_omits_exception_payload_and_bounds_injection():
    try:
        raise ValueError("private document name and https://secret.test?q=private")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "failed https://secret.test?token=123 Bearer token-value sk-fake-key 13812345678\nFORGED",
            (),
            sys.exc_info(),
        )
    with log_context(trace_id="bad\nFORGED", business="transfer"):
        text = SafeFormatter("%(message)s %(context_fields)s").format(record)
    assert "\n" not in text and "\r" not in text
    assert "private document" not in text and "secret.test" not in text
    assert "token-value" not in text and "sk-fake-key" not in text and "13812345678" not in text
    assert "exception_type=ValueError" in text and "test_formatter_omits" in text
    assert "trace_id=invalid-id" in text
    assert record.exc_info is not None


def test_file_rotates_by_size_and_day_with_one_archive_limit(tmp_path):
    path = tmp_path / "review-service.log"
    handler = BoundedDailyFileHandler(str(path), max_bytes=160, backup_count=2)
    logger = logging.Logger("rotation")
    logger.addHandler(handler)
    for _ in range(6):
        logger.info("x" * 90)
    assert len(list(tmp_path.glob("review-service.log.*"))) == 2
    # Old daily archives count toward the same limit on the next rotation.
    (tmp_path / "review-service.log.2000-01-01").write_text("legacy", encoding="utf-8")
    handler.rolloverAt = int(time.time()) - 1
    logger.info("midnight")
    handler.close()
    assert len(list(tmp_path.glob("review-service.log.*"))) == 2
    assert not (tmp_path / "review-service.log.2000-01-01").exists()
    assert "midnight" in path.read_text(encoding="utf-8")


def test_required_log_failure_is_fatal_but_local_can_degrade(tmp_path, monkeypatch):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("REVIEW_LOG_DIR", str(blocked))
    monkeypatch.setenv("REVIEW_LOG_REQUIRED", "true")
    with pytest.raises(RuntimeError, match="not writable"), configured_logging():
        pass
    monkeypatch.setenv("REVIEW_LOG_REQUIRED", "false")
    with configured_logging():
        logging.getLogger("forj_review_service").info("console remains available")


def test_disk_write_failure_does_not_dump_raw_arguments(tmp_path, monkeypatch, capsys):
    handler = BoundedDailyFileHandler(str(tmp_path / "failure.log"), max_bytes=100, backup_count=1)
    logger = logging.Logger("failure")
    logger.addHandler(handler)

    def fail(record):
        raise OSError("private failure detail")

    monkeypatch.setattr(handler, "shouldRollover", fail)
    logger.error("raw token=%s", "PRIVATE")
    handler.close()
    output = capsys.readouterr().err
    assert "write failed error_type=OSError" in output
    assert "PRIVATE" not in output and "private failure detail" not in output


def test_scheduler_preserves_each_queued_context_including_thread_work():
    scheduler = FairAsyncScheduler(global_limit=1, per_job_limit=1)

    async def work():
        await asyncio.sleep(0.01)
        return current_log_context(), await asyncio.to_thread(current_log_context)

    try:
        futures = []
        for index in range(6):
            with log_context(trace_id=f"trace-{index}", job_id=f"job-{index}"):
                futures.append(scheduler.submit(f"job-{index}", work))
        for index, future in enumerate(futures):
            expected = {"trace_id": f"trace-{index}", "job_id": f"job-{index}"}
            assert future.result(timeout=3) == (expected, expected)
        assert current_log_context() == {}
    finally:
        scheduler.close()


def test_http_logs_routes_and_errors_without_queries_or_noisy_polls(caplog):
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/review/jobs/{job_id}")
    def job(job_id: str):
        return {"status": "RUNNING"}

    with caplog.at_level(logging.INFO, logger="forj_review_service"), TestClient(app) as client:
        client.get("/health?token=SECRET")
        client.get("/api/review/jobs/private-id")
        client.post("/api/review/jobs/private-id?token=SECRET", json={"name": "PRIVATE"})
        client.get("/unknown-private-path?token=SECRET")
    lines = [
        record.getMessage() for record in caplog.records if "review http" in record.getMessage()
    ]
    assert len(lines) == 2
    assert "status=405" in lines[0] and "route=/api/review/jobs/{job_id}" in lines[0]
    assert "status=404" in lines[1] and "route=unmatched" in lines[1]
    assert not any(
        value in " ".join(lines) for value in ("SECRET", "PRIVATE", "private-id", "unknown-private")
    )
