"""Deterministic predicates for one bounded semantic retry."""

from forj_review_service.contracts.recognition import (
    RETRYABLE_UNCERTAIN_PREFIX,
    QwenExtraction,
)
from forj_review_service.integrations.recognition.errors import classify_qwen_error


def retry_reason_for_error(error: RuntimeError) -> str | None:
    return {
        "invalid_json_syntax": "invalid_json",
        "invalid_response_schema": "invalid_schema",
        "invalid_response_structure": "invalid_structure",
    }.get(classify_qwen_error(error))


def retry_reason_for_extraction(extraction: QwenExtraction, policy: object | None) -> str | None:
    if policy is None:
        return "document_type_mismatch"
    if extraction.document_type != getattr(policy, "document_type", None):
        return "document_type_mismatch"
    # `uncertain_fields` 已按材料白名单过滤，纯展示字段不会出现在这里，
    # 所以不需要再按必审字段收窄。
    if extraction.uncertain_fields:
        return RETRYABLE_UNCERTAIN_PREFIX + ",".join(sorted(extraction.uncertain_fields))
    if extraction.confidence is not None and extraction.confidence < 0.70:
        return "low_confidence"
    if not extraction.fields:
        return "empty_supported_fields"
    return None
