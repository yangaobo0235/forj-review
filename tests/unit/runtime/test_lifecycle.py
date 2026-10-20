import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient

from forj_review_service.bootstrap.application import create_app
from forj_review_service.config.settings import QwenConfig
from forj_review_service.contracts.review import ReviewRequest, ReviewResponse
from forj_review_service.integrations.recognition.client import QwenClient
from forj_review_service.integrations.recognition.extraction import RecognitionService
from forj_review_service.runtime.review_service import ReviewService
from forj_review_service.runtime.scheduling.fair_scheduler import FairAsyncScheduler


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
    from forj_review_service.runtime.review_service import ReviewService

    first, second = (
        create_app(review_service=ReviewService(load_model=False)),
        create_app(review_service=ReviewService(load_model=False)),
    )
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


class LoopBoundModelTransport(httpx.AsyncBaseTransport):
    """像真实连接一样要求在所属循环清理；普通 MockTransport 无法复现此问题。"""

    def __init__(self, *, block=False):
        self.block = block
        self.started = threading.Event()
        self.owners = set()
        self.active = set()
        self.closed = set()

    async def handle_async_request(self, request):
        loop = asyncio.get_running_loop()
        self.owners.add(loop)
        self.active.add(loop)
        try:
            if self.block:
                self.started.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json={"choices": [{"message": {
                "content": '{"document_type":"invoice","confidence":0.98}',
            }}]})
        finally:
            self.active.remove(loop)

    async def aclose(self):
        loop = asyncio.get_running_loop()
        assert loop in self.owners, "连接池必须在其所属事件循环中关闭"
        assert loop not in self.active, "先等待识别任务取消，再清理连接池"
        assert not loop.is_closed()
        self.closed.add(loop)


def model_app(transport=None, *, base_url="https://model.example.test/v1"):
    model = QwenClient(
        QwenConfig("test-only", base_url, "test-model"),
        transport=transport,
    )
    service = ReviewService(load_model=False)
    service.recognition = RecognitionService(model)
    return create_app(review_service=service), model


def test_application_closes_model_pools_on_both_worker_loops_before_stopping_them():
    transport = LoopBoundModelTransport()
    app, model = model_app(transport)
    image = {"index": 1, "src": "https://images.example.test/test.jpg"}
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        schedulers = [app.state.review_jobs.scheduler, app.state.review_jobs.finalize_scheduler]
        for scheduler in schedulers:
            # 同一循环内多次请求仍然复用一个连接池。
            for _ in range(2):
                result = scheduler.submit("pool-test", lambda: model.classify_document(image))
                assert result.result(timeout=2).document_type == "invoice"
        # HTTP 主循环也可能持有连接，后台循环退出后还要清理这一份。
        assert client.portal.call(model.classify_document, image).document_type == "invoice"
        assert len(model._clients) == 3
    assert transport.closed == transport.owners
    assert len(transport.closed) == 3
    assert not model._clients
    assert all(not scheduler._thread.is_alive() for scheduler in schedulers)
    assert all(loop.is_closed() for loop in transport.closed)


def test_application_cancels_active_model_request_before_closing_its_pool():
    transport = LoopBoundModelTransport(block=True)
    app, model = model_app(transport)
    image = {"index": 1, "src": "https://images.example.test/test.jpg"}
    with TestClient(app):
        jobs = app.state.review_jobs
        future = jobs.scheduler.submit("running", lambda: model.classify_document(image))
        assert transport.started.wait(2)
    assert future.cancelled()
    assert transport.closed == transport.owners
    assert not transport.active and not model._clients
    assert not jobs.scheduler._thread.is_alive()
    assert not jobs.finalize_scheduler._thread.is_alive()


def test_scheduler_cleanup_failure_is_reported_and_still_releases_thread():
    scheduler = FairAsyncScheduler(global_limit=1, per_job_limit=1)

    async def failed_cleanup():
        assert asyncio.get_running_loop() is scheduler._loop
        raise ValueError("cleanup failed")

    with pytest.raises(ValueError, match="cleanup failed"):
        scheduler.close(cleanup=failed_cleanup)
    assert not scheduler._thread.is_alive()
    assert scheduler._loop.is_closed()
    scheduler.close()


@pytest.mark.parametrize("fail", [False, True])
def test_synchronous_assist_closes_pool_before_its_temporary_loop(monkeypatch, fail):
    transport = LoopBoundModelTransport()
    model = QwenClient(
        QwenConfig("test-only", "https://model.example.test/v1", "test-model"),
        transport=transport,
    )
    service = ReviewService(load_model=False)
    service.recognition = RecognitionService(model)

    async def assist_async(request, *, business):
        await model.classify_document({"index": 1, "src": "https://images.example.test/test.jpg"})
        if fail:
            raise ValueError("review failed")
        return ReviewResponse(recommendation="REVIEW_REQUIRED", risk_level="MEDIUM", summary="test")

    monkeypatch.setattr(service, "assist_async", assist_async)
    request = ReviewRequest(page_url="https://example.test/review")
    for _ in range(2):
        if fail:
            with pytest.raises(ValueError, match="review failed"):
                service.assist(request)
        else:
            assert service.assist(request).summary == "test"
        assert not model._clients
    assert len(transport.owners) == 2 and transport.closed == transport.owners
    assert all(loop.is_closed() for loop in transport.closed)


@pytest.mark.parametrize("proactor", [False, pytest.param(
    True, marks=pytest.mark.skipif(sys.platform != "win32", reason="Windows event loop"),
)])
def test_real_http_keepalive_connections_close_before_worker_loops(monkeypatch, proactor):
    """真实 TCP 长连接覆盖 Windows Proactor，避免 MockTransport 掩盖关闭错误。"""
    if proactor:
        monkeypatch.setattr(asyncio, "new_event_loop", asyncio.ProactorEventLoop)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    class ModelHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps({"choices": [{"message": {
                "content": '{"document_type":"invoice","confidence":0.98}',
            }}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            app, model = model_app(base_url=f"http://127.0.0.1:{server.server_port}/v1")
            image = {"index": 1, "src": "https://images.example.test/test.jpg"}
            with TestClient(app):
                jobs = app.state.review_jobs
                for scheduler in (jobs.scheduler, jobs.finalize_scheduler):
                    for _ in range(2):
                        future = scheduler.submit("real-http", lambda: model.classify_document(image))
                        assert future.result(timeout=5).document_type == "invoice"
                pools = list(model._clients.values())
                assert len(pools) == 2 and all(not pool.is_closed for pool in pools)
            assert not model._clients and all(pool.is_closed for pool in pools)
            assert not jobs.scheduler._thread.is_alive()
            assert not jobs.finalize_scheduler._thread.is_alive()
        finally:
            server.shutdown()
            thread.join(timeout=2)
