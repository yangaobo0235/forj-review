"""Separate app factory: importing this module starts neither backend."""

import logging
import re
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from filelock import FileLock
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import ValidationError

from forj_configuration_agent.application.service import ConfigurationService
from forj_configuration_agent.config.settings import Settings, load_settings
from forj_configuration_agent.contracts.models import AgentError
from forj_configuration_agent.integrations.configuration_store.sqlite import SessionStore
from forj_configuration_agent.integrations.conversation.client import ConversationClient
from forj_configuration_agent.integrations.review_kernel.library import ReviewKernel
from forj_configuration_agent.interfaces.http import router


def create_app(settings: Settings | None = None, *, model=None, kernel=None):
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app):
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        # A state directory has one graph executor, even if a second CLI is launched by mistake.
        lock = FileLock(str(settings.state_dir / "service.lock"))
        lock.acquire(timeout=0)
        handler = RotatingFileHandler(settings.state_dir / "configuration-agent.log",
                                       maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        instance_id = uuid4().hex
        handler.setFormatter(logging.Formatter("%(asctime)s service=configuration-agent instance=" + instance_id + " %(levelname)s %(message)s"))
        logger = logging.getLogger("forj_configuration_agent")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        client = model or ConversationClient(settings)
        try:
            async with AsyncSqliteSaver.from_conn_string(str(settings.state_dir / "checkpoints.sqlite3")) as saver:
                await saver.setup()
                store = SessionStore(settings.state_dir / "configuration.sqlite3")
                adapter = kernel or ReviewKernel(settings.pages_dir, settings.cases_dir, settings.review_url,
                                                 local_cases_dir=settings.state_dir / "case-sets")
                app.state.service = ConfigurationService(store, adapter, client, saver, settings.max_calls)
                logger.info("started")
                try:
                    yield
                finally:
                    await app.state.service.close()
        finally:
            if hasattr(client, "close"):
                await client.close()
            logger.info("stopped")
            logger.removeHandler(handler)
            handler.close()
            lock.release()

    app = FastAPI(title="FORJ Configuration Agent", lifespan=lifespan)

    @app.exception_handler(AgentError)
    async def agent_error(request: Request, exc: AgentError):
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

    @app.exception_handler(ValueError)
    async def invalid_configuration(request: Request, exc: ValueError):
        return JSONResponse({"code": "INVALID_CONFIGURATION", "message": str(exc)[:2000]}, status_code=422)

    @app.exception_handler(ValidationError)
    async def invalid_contract(request: Request, exc: ValidationError):
        return JSONResponse({"code": "INVALID_CONFIGURATION", "message": str(exc)[:2000]}, status_code=422)

    @app.middleware("http")
    async def local_access(request, call_next):
        host = request.headers.get("host", "").split(":")[0].lower()
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"code": "LOCAL_ONLY"}, status_code=403)
        if request.url.path == "/health":
            return await call_next(request)
        origin = request.headers.get("origin")
        if origin and not re.fullmatch(
            r"chrome-extension://[a-p]{32}|http://(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?", origin
        ):
            return JSONResponse({"code": "ORIGIN_REJECTED", "message": "配置服务仅接受本机应用或浏览器扩展请求"}, status_code=403)
        if request.method == "OPTIONS" and origin:
            response = JSONResponse({"ok": True})
            response.headers.update({"Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
                "Access-Control-Allow-Headers": "content-type", "Vary": "Origin"})
            return response
        response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "configuration-agent", "protocol": "1.0"}

    app.include_router(router)
    return app
