"""HTTP diagnostics without query strings, payloads or noisy successful polling."""

import logging
from time import perf_counter
from uuid import uuid4

from forj_review_assistant.observability.context import log_context

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started, status = perf_counter(), 500

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        with log_context(request_id=uuid4().hex):
            try:
                await self.app(scope, receive, capture)
            finally:
                route = getattr(scope.get("route"), "path", "unmatched")
                quiet = scope["method"] == "GET" and route in {"/health", "/api/review/jobs/{job_id}"}
                if status >= 400 or not quiet:
                    logger.log(logging.WARNING if status >= 400 else logging.INFO,
                               "review http completed method=%s route=%s status=%d duration_ms=%d",
                               scope["method"], route, status, int((perf_counter() - started) * 1000))
