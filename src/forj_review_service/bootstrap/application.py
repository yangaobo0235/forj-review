"""Compose the HTTP application and own the review runtime lifetime."""

import asyncio
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from uuid import uuid4

from fastapi import FastAPI

from forj_review_service.integrations.database import AsyncDatabase, review_database
from forj_review_service.interfaces.http.catalog import router as catalog_router
from forj_review_service.interfaces.http.errors import install_error_handlers
from forj_review_service.interfaces.http.review import router as review_router
from forj_review_service.interfaces.http.vehicle_models import router as vehicle_model_router
from forj_review_service.observability.http import RequestLoggingMiddleware
from forj_review_service.observability.logging import configured_logging
from forj_review_service.runtime.jobs.manager import ReviewJobManager
from forj_review_service.runtime.review_service import ReviewService


def create_app(*, review_service: ReviewService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with configured_logging():
            async with AsyncExitStack() as resources:
                logger = logging.getLogger(__name__)
                settings = None if review_service is not None else review_database()
                database = None
                if review_service is not None:
                    service = review_service
                else:
                    from forj_review_service.runtime.configuration.postgres import load_registry

                    database = await resources.enter_async_context(AsyncDatabase(settings))
                    await database.require_ready()
                    registry = await load_registry(database)
                    service = ReviewService(registry=registry)
                app.state.database = database
                jobs = ReviewJobManager(service)
                app.state.review_service = service
                app.state.review_jobs = jobs
                app.state.process_instance_id = uuid4().hex
                app.state.configuration_source = (
                    "postgresql:" + database.schema if database else None
                )
                app.state.configuration_directory = None
                reload_task = None
                app.state.configuration_loader = None
                if database is not None:
                    from forj_review_service.runtime.configuration.reloader import (
                        ConfigurationReloader,
                    )

                    loader = ConfigurationReloader(app, database)
                    app.state.configuration_loader = loader
                    reload_task = asyncio.create_task(loader.run(), name="configuration-reloader")
                logger.info(
                    "review service started release=%s profiles=%d model_configured=%s model_global=%d model_per_job=%d finalize=%d",
                    os.getenv("REVIEW_RELEASE_ID", "development"),
                    len(service.registry.active_profiles),
                    service.recognition is not None,
                    jobs.scheduler.global_limit,
                    jobs.scheduler.per_job_limit,
                    jobs.finalize_scheduler.global_limit,
                )
                try:
                    yield
                finally:
                    if reload_task is not None:
                        reload_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await reload_task
                    try:
                        # 等待后台循环取消任务并清理连接时，主循环仍可处理取消和退出。
                        await asyncio.to_thread(jobs.close)
                    finally:
                        await service.aclose()
                    logger.info("review service stopped")

    app = FastAPI(title="FORJ Review Service", lifespan=lifespan)
    install_error_handlers(app)
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(catalog_router)
    app.include_router(review_router)
    app.include_router(vehicle_model_router)
    return app
