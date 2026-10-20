"""Compose the HTTP application and own the review runtime lifetime."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI

from forj_review_service.integrations.database import review_database
from forj_review_service.interfaces.http.catalog import router as catalog_router
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
            logger = logging.getLogger(__name__)
            database = None if review_service is not None else review_database()
            if review_service is not None:
                service = review_service
            else:
                from forj_review_service.runtime.configuration.postgres import load_registry

                await asyncio.to_thread(database.require_ready)
                registry = await asyncio.to_thread(load_registry, database)
                service = ReviewService(registry=registry)
            jobs = ReviewJobManager(service)
            app.state.review_service = service
            app.state.review_jobs = jobs
            app.state.process_instance_id = uuid4().hex
            app.state.configuration_source = "postgresql:" + database.schema if database else None
            app.state.configuration_directory = None
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
                try:
                    # 等待后台循环取消任务并清理连接时，主循环仍可处理取消和退出。
                    await asyncio.to_thread(jobs.close)
                finally:
                    await service.aclose()
                logger.info("review service stopped")

    app = FastAPI(title="FORJ Review Service", lifespan=lifespan)
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(catalog_router)
    app.include_router(review_router)
    app.include_router(vehicle_model_router)
    return app
