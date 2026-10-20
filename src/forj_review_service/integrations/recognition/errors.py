"""Qwen 调用错误的安全分类与日志详情提取。

对外只返回稳定的诊断代码，日志只保留有界的解析信息，避免泄露模型响应或业务数据。
"""

import httpx

from forj_review_service.integrations.recognition.client import (
    QwenResponseSchemaError,
    QwenResponseStructureError,
    QwenResponseSyntaxError,
)


def classify_qwen_error(error: RuntimeError) -> str:
    """把调用或解析异常转换为不暴露模型及业务数据的稳定诊断代码。"""
    cause = error.__cause__
    # 先识别自定义解析异常，避免被其 ValueError 基类归入宽泛的结构错误。
    if isinstance(cause, QwenResponseSyntaxError):
        return "invalid_json_syntax"
    if isinstance(cause, QwenResponseSchemaError):
        return "invalid_response_schema"
    if isinstance(cause, QwenResponseStructureError):
        return "invalid_response_structure"
    if "响应格式无效" in str(error):
        return "invalid_json"
    if isinstance(cause, httpx.HTTPStatusError):
        return f"qwen_http_{cause.response.status_code}"
    if isinstance(cause, httpx.HTTPError):
        return "qwen_network_error"
    if isinstance(cause, (KeyError, IndexError, TypeError, ValueError, AttributeError)):
        return "invalid_response_structure"
    return "qwen_runtime_error"


def describe_qwen_error(error: RuntimeError) -> str | None:
    """返回供服务端日志使用的有限、非敏感解析详情。"""
    cause = error.__cause__
    if isinstance(cause, QwenResponseSchemaError):
        return cause.detail
    if isinstance(cause, QwenResponseSyntaxError):
        return "json_decode"
    if isinstance(cause, QwenResponseStructureError):
        return "json_object_count"
    return None
