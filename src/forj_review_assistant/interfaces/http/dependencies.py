"""Application-scoped service dependencies; HTTP handlers own no global services."""

from fastapi import Request

from forj_review_assistant.runtime.jobs.manager import ReviewJobManager
from forj_review_assistant.runtime.review_service import ReviewService


def get_review_service(request: Request) -> ReviewService:
    return request.app.state.review_service


def get_review_jobs(request: Request) -> ReviewJobManager:
    return request.app.state.review_jobs
