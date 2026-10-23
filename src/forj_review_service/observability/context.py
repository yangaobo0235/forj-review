"""Correlation metadata; never put page values or document contents in this context."""

import inspect
import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

_context: ContextVar[dict[str, str] | None] = ContextVar("review_log_context", default=None)
CONTEXT_KEYS = ("request_id", "trace_id", "job_id", "image_id", "business", "region", "profile")


def safe_id(value: object) -> str:
    text = str(value if value is not None else "-")
    return text if re.fullmatch(r"[\w.:-]{1,96}", text, flags=re.ASCII) else "invalid-id"


def current_log_context() -> dict[str, str]:
    return dict(_context.get() or {})


@contextmanager
def log_context(**values: object):
    merged = {
        **current_log_context(),
        **{key: safe_id(value) for key, value in values.items() if key in CONTEXT_KEYS},
    }
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def with_review_log_context(function):
    """Bind service calls before extraction and graph execution, including worker tasks."""
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        request = bound.arguments["request"]
        if not request.trace_id:
            request = request.model_copy(update={"trace_id": uuid4().hex})
            bound.arguments["request"] = request
        image = bound.arguments.get("image")
        values = dict(
            trace_id=request.trace_id,
            business=request.business_type,
            region=request.region,
            profile=request.profile_version,
        )
        if image is not None:
            values["image_id"] = image.image_id or image.index
        with log_context(**values):
            return await function(*bound.args, **bound.kwargs)

    return wrapped
