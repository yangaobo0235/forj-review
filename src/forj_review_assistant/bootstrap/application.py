"""Compose the HTTP application and own the review runtime lifetime."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from forj_review_assistant.interfaces.http.catalog import router as catalog_router
from forj_review_assistant.interfaces.http.review import router as review_router
from forj_review_assistant.observability.logging import configure_log_file
from forj_review_assistant.runtime.jobs.manager import ReviewJobManager
from forj_review_assistant.runtime.review_service import ReviewService


def create_app(*, review_service: ReviewService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_log_file(logging.getLogger("uvicorn.error"))
        service = review_service if review_service is not None else ReviewService()
        jobs = ReviewJobManager(service)
        app.state.review_service = service
        app.state.review_jobs = jobs
        try:
            yield
        finally:
            jobs.close()

    app = FastAPI(title="FORJ Review Assistant", lifespan=lifespan)
    app.include_router(catalog_router)
    app.include_router(review_router)
    return app
