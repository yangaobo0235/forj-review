"""Single HTTP mapping for public errors; internal exception text stays private."""

import logging
import traceback
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from forj_review_service.businesses.context_validation import BusinessContextMismatch
from forj_review_service.businesses.registry import BusinessProfileNotFound
from forj_review_service.contracts.errors import (
    ReviewInputError,
    ReviewJobNotFound,
    ReviewResourceError,
    VehicleModelCatalogUnavailable,
)

logger = logging.getLogger(__name__)


def install_error_handlers(app: FastAPI) -> None:
    def response(code: str, message: str, status: int) -> JSONResponse:
        # Keep detail for existing extension installations during service upgrades.
        return JSONResponse({"code": code, "message": message, "detail": message}, status)

    async def public_error(request: Request, exc: Exception):
        if isinstance(exc, ReviewResourceError):
            return response(exc.code, exc.message, exc.status)
        if isinstance(exc, ReviewInputError):
            return response("INVALID_REVIEW_INPUT", str(exc), 422)
        if isinstance(exc, BusinessContextMismatch):
            return response("BUSINESS_CONTEXT_MISMATCH", str(exc), 422)
        if isinstance(exc, BusinessProfileNotFound):
            return response("BUSINESS_PROFILE_NOT_FOUND", "审核业务配置不存在或版本不兼容", 422)
        if isinstance(exc, ReviewJobNotFound):
            return response("REVIEW_JOB_NOT_FOUND", "审核任务不存在或已过期", 404)
        return response("VEHICLE_CATALOG_UNAVAILABLE", str(exc), 503)

    for error in (
        ReviewInputError,
        BusinessContextMismatch,
        BusinessProfileNotFound,
        ReviewJobNotFound,
        ReviewResourceError,
        VehicleModelCatalogUnavailable,
    ):
        app.add_exception_handler(error, public_error)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return response("INVALID_REQUEST", "请求格式无效，请检查字段类型和必填项", 422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        message = "请求地址不存在" if exc.status_code == 404 else "请求无法处理"
        result = response("HTTP_ERROR", message, exc.status_code)
        if exc.headers:
            result.headers.update(exc.headers)
        return result

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        # Exception messages/tracebacks may contain SQL, credentials or input data.
        locations = " > ".join(
            f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__)[-12:]
        )
        logger.error(
            "request failed method=%s error_type=%s stack=%s",
            request.method,
            type(exc).__name__,
            locations,
        )
        return response("INTERNAL_ERROR", "审核服务暂时不可用，请稍后重试或联系管理员", 500)
