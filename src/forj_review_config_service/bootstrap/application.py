"""Separate app factory: importing this module starts neither backend."""

import logging
import re
from contextlib import AsyncExitStack, asynccontextmanager
from logging.handlers import RotatingFileHandler
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from filelock import FileLock
from pydantic import ValidationError

from forj_review_config_service.application.service import ConfigurationService
from forj_review_config_service.config.settings import Settings, load_settings
from forj_review_config_service.contracts.models import ConfigurationError
from forj_review_config_service.integrations.async_database import (
    AsyncDatabase,
    ExecutorCheckpointer,
)
from forj_review_config_service.integrations.configuration_store.async_postgres import (
    AsyncSessionStore,
)
from forj_review_config_service.integrations.conversation.client import ConversationClient
from forj_review_config_service.integrations.database import Database
from forj_review_config_service.integrations.review_kernel.postgres import PostgresReviewKernel
from forj_review_config_service.interfaces.http import router


def create_app(settings: Settings | None = None, *, model=None, kernel=None):
    settings = settings or load_settings()
    if not settings.database_url:
        raise ValueError("配置服务必须配置 PostgreSQL 数据库连接")

    @asynccontextmanager
    async def lifespan(app):
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        # A state directory has one graph executor, even if a second CLI is launched by mistake.
        lock = FileLock(str(settings.state_dir / "service.lock"))
        lock.acquire(timeout=0)
        handler = RotatingFileHandler(
            settings.state_dir / "review-config-service.log",
            maxBytes=5_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        instance_id = uuid4().hex
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s service=review-config-service instance="
                + instance_id
                + " %(levelname)s %(message)s"
            )
        )
        logger = logging.getLogger("forj_review_config_service")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        client = model or ConversationClient(settings)
        try:
            async with AsyncExitStack() as stack:
                database = await stack.enter_async_context(
                    AsyncDatabase(Database(settings.database_url, settings.database_schema))
                )
                await database.require_ready()
                await database.acquire_executor()
                saver = ExecutorCheckpointer(database)
                store = AsyncSessionStore(database)
                await store.interrupt_running()
                adapter = kernel or PostgresReviewKernel(
                    database,
                    settings.cases_dir,
                    settings.review_url,
                    settings.state_dir / "case-sets",
                )
                app.state.service = ConfigurationService(
                    store, adapter, client, saver, settings.max_calls
                )
                app.state.database = database
                database.watch_executor(app.state.service.close)
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

    app = FastAPI(title="FORJ Review Configuration Service", lifespan=lifespan)

    @app.exception_handler(ConfigurationError)
    async def configuration_error(request: Request, exc: ConfigurationError):
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

    @app.exception_handler(ValueError)
    async def invalid_configuration(request: Request, exc: ValueError):
        return JSONResponse(
            {"code": "INVALID_CONFIGURATION", "message": str(exc)[:2000]}, status_code=422
        )

    @app.exception_handler(ValidationError)
    async def invalid_contract(request: Request, exc: ValidationError):
        return JSONResponse(
            {"code": "INVALID_CONFIGURATION", "message": str(exc)[:2000]}, status_code=422
        )

    @app.middleware("http")
    async def local_access(request, call_next):
        host = request.headers.get("host", "").split(":")[0].lower()
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"code": "LOCAL_ONLY"}, status_code=403)
        if request.url.path == "/health":
            return await call_next(request)
        if request.app.state.database.lost.is_set():
            return JSONResponse(
                {"code": "EXECUTOR_LOST", "message": "配置执行器失去所有权，请重启配置服务"},
                status_code=503,
            )
        origin = request.headers.get("origin")
        if origin and not re.fullmatch(
            r"chrome-extension://[a-p]{32}|http://(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?",
            origin,
        ):
            return JSONResponse(
                {"code": "ORIGIN_REJECTED", "message": "配置服务仅接受本机应用或浏览器扩展请求"},
                status_code=403,
            )
        if request.method == "OPTIONS" and origin:
            response = JSONResponse({"ok": True})
            response.headers.update(
                {
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
                    "Access-Control-Allow-Headers": "content-type",
                    "Vary": "Origin",
                }
            )
            return response
        response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/health")
    async def health():
        if app.state.database.lost.is_set():
            return JSONResponse({"status": "unavailable", "code": "EXECUTOR_LOST"}, status_code=503)
        return {"status": "ok", "service": "review-config-service", "protocol": "1.0"}

    app.include_router(router)
    return app
