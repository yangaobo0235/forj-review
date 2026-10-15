import httpx

from forj_review_assistant.integrations.recognition.client import (
    QwenResponseSchemaError,
    QwenResponseStructureError,
    QwenResponseSyntaxError,
)
from forj_review_assistant.integrations.recognition.errors import (
    classify_qwen_error,
    describe_qwen_error,
)


def test_classifies_invalid_model_json_without_exposing_response() -> None:
    error = RuntimeError("Qwen 未分类资料响应格式无效：VIN=SHOULD-NOT-LEAK")

    assert classify_qwen_error(error) == "invalid_json"


def test_classifies_parser_categories_and_safe_schema_paths() -> None:
    syntax = RuntimeError("Qwen 字段响应格式无效")
    syntax.__cause__ = QwenResponseSyntaxError("malformed")
    assert classify_qwen_error(syntax) == "invalid_json_syntax"
    assert describe_qwen_error(syntax) == "json_decode"

    schema = RuntimeError("Qwen 字段响应格式无效")
    schema.__cause__ = QwenResponseSchemaError(
        "invalid model response", detail="confidence:greater_than_equal"
    )
    assert classify_qwen_error(schema) == "invalid_response_schema"
    assert describe_qwen_error(schema) == "confidence:greater_than_equal"

    structure = RuntimeError("Qwen 字段响应格式无效")
    structure.__cause__ = QwenResponseStructureError("multiple")
    assert classify_qwen_error(structure) == "invalid_response_structure"
    assert describe_qwen_error(structure) == "json_object_count"


def test_classifies_http_status_from_exception_cause() -> None:
    request = httpx.Request("POST", "https://example.test/chat/completions")
    response = httpx.Response(400, request=request)
    cause = httpx.HTTPStatusError("sensitive body", request=request, response=response)
    try:
        raise RuntimeError("Qwen 调用失败") from cause
    except RuntimeError as error:
        assert classify_qwen_error(error) == "qwen_http_400"


def test_classifies_response_structure_and_network_failures() -> None:
    try:
        raise RuntimeError("Qwen 调用失败") from KeyError("choices")
    except RuntimeError as structure_error:
        assert classify_qwen_error(structure_error) == "invalid_response_structure"

    request = httpx.Request("POST", "https://example.test/chat/completions")
    try:
        raise RuntimeError("Qwen 调用失败") from httpx.ReadTimeout("timeout", request=request)
    except RuntimeError as network_error:
        assert classify_qwen_error(network_error) == "qwen_network_error"
