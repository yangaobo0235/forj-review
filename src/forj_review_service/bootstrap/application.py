"""Own the audit runtime lifetime and validate its complete file snapshot at startup."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI

from forj_review_service.config.data import pages_directory
from forj_review_service.interfaces.http.catalog import router as catalog_router
from forj_review_service.interfaces.http.errors import install_error_handlers
from forj_review_service.interfaces.http.review import router as review_router
from forj_review_service.interfaces.http.vehicle_models import router as vehicle_model_router
from forj_review_service.observability.http import RequestLoggingMiddleware
from forj_review_service.observability.logging import configured_logging
from forj_review_service.runtime.configuration.service import validated_configuration
from forj_review_service.runtime.jobs.manager import ReviewJobManager
from forj_review_service.runtime.review_service import ReviewService


def create_app(*, review_service: ReviewService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with configured_logging():
            directory = None if review_service is not None else pages_directory()
            if review_service is None:
                _, registry = validated_configuration(directory)
                service = ReviewService(registry=registry)
            else:
                service = review_service
            jobs = ReviewJobManager(service)
            app.state.review_service = service
            app.state.review_jobs = jobs
            app.state.process_instance_id = uuid4().hex
            app.state.configuration_source = "json" if directory else "injected"
            app.state.configuration_directory = str(directory) if directory else None
            logger = logging.getLogger(__name__)
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
                    await asyncio.to_thread(jobs.close)
                finally:
                    await service.aclose()
                logger.info("review service stopped")

    app = FastAPI(title="FORJ Audit Service", lifespan=lifespan)
    install_error_handlers(app)
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(catalog_router)
    app.include_router(review_router)
    app.include_router(vehicle_model_router)
    return app
