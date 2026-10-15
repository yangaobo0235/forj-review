"""Review HTTP boundary: validation, status mapping and application delegation."""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from forj_review_assistant.businesses.context_validation import BusinessContextMismatch
from forj_review_assistant.businesses.registry import BusinessProfileNotFound
from forj_review_assistant.contracts.review import (
    ReviewJobCreated,
    ReviewJobSnapshot,
    ReviewRequest,
    ReviewResponse,
)
from forj_review_assistant.interfaces.http.dependencies import (
    get_review_jobs,
    get_review_service,
)
from forj_review_assistant.runtime.jobs.manager import ReviewJobManager
from forj_review_assistant.runtime.review_service import ReviewService

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


def validate_business_profile(request: ReviewRequest, review_service: ReviewService) -> None:
    try:
        review_service.resolve_profile(request)
    except BusinessContextMismatch as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BusinessProfileNotFound as exc:
        raise HTTPException(
            status_code=422,
            detail="审核业务配置不存在或版本不兼容",
        ) from exc


@router.post("/api/review/assist", response_model=ReviewResponse)
def assist(
    request: ReviewRequest, review_service: Annotated[ReviewService, Depends(get_review_service)]
) -> ReviewResponse:
    """执行一次只读审核辅助，不会修改原审核系统。"""
    validate_business_profile(request, review_service)
    logger.info(
        "Review request received: trace_id=%s application_id=%s field_count=%d image_count=%d",
        request.trace_id or "generated",
        request.application_id or "unknown",
        len(request.page_fields),
        len(request.images),
    )
    response = review_service.assist(request)
    logger.info(
        "Review completed: trace_id=%s recommendation=%s comparison_count=%d issue_count=%d",
        response.trace_id,
        response.recommendation,
        len(response.comparisons),
        len(response.issues),
    )
    return response


@router.post(
    "/api/review/jobs",
    response_model=ReviewJobCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_review_job(
    request: ReviewRequest,
    review_service: Annotated[ReviewService, Depends(get_review_service)],
    review_jobs: Annotated[ReviewJobManager, Depends(get_review_jobs)],
) -> ReviewJobCreated:
    validate_business_profile(request, review_service)
    logger.info(
        "Review job requested: trace_id=%s application_id=%s image_count=%d",
        request.trace_id or "generated",
        request.application_id or "unknown",
        len(request.images),
    )
    try:
        created = review_jobs.create(request)
    except ValueError as exc:
        # 请求本身不合法返回 422。
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info("Review job created: job_id=%s", created.job_id)
    return created


@router.post(
    "/api/review/jobs/urls",
    response_model=ReviewJobCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_url_review_job(
    request: ReviewRequest,
    review_service: Annotated[ReviewService, Depends(get_review_service)],
    review_jobs: Annotated[ReviewJobManager, Depends(get_review_jobs)],
) -> ReviewJobCreated:
    """直接提交 DOM 采集的图片地址，不下载、压缩或接收图片正文。"""
    validate_business_profile(request, review_service)
    try:
        created = review_jobs.create_urls(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "URL review job created: job_id=%s image_count=%d", created.job_id, len(request.images)
    )
    return created


@router.delete(
    "/api/review/jobs/{job_id}",
    response_model=ReviewJobSnapshot,
)
def cancel_review_job(
    job_id: str, review_jobs: Annotated[ReviewJobManager, Depends(get_review_jobs)]
) -> ReviewJobSnapshot:
    try:
        return review_jobs.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="审核任务不存在或已过期") from exc


@router.get("/api/review/jobs/{job_id}", response_model=ReviewJobSnapshot)
def get_review_job(
    job_id: str, review_jobs: Annotated[ReviewJobManager, Depends(get_review_jobs)]
) -> ReviewJobSnapshot:
    try:
        return review_jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="审核任务不存在或已过期") from exc
