"""Stable public configuration errors and private failure diagnostics."""

import logging
import traceback
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from forj_review_config_service.contracts.models import ConfigurationError

logger = logging.getLogger("forj_review_config_service")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ConfigurationError)
    async def configuration_error(request: Request, exc: ConfigurationError):
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

    async def invalid_contract(request: Request, exc: Exception):
        return JSONResponse(
            {"code": "INVALID_CONFIGURATION", "message": "配置格式无效，请检查字段类型和必填项"},
            status_code=422,
        )

    app.add_exception_handler(RequestValidationError, invalid_contract)
    app.add_exception_handler(ValidationError, invalid_contract)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(
            {"code": "HTTP_ERROR", "message": "请求地址不存在或方法不支持"},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
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
        return JSONResponse(
            {"code": "INTERNAL_ERROR", "message": "配置服务暂时不可用，请稍后重试或联系管理员"},
            status_code=500,
        )
