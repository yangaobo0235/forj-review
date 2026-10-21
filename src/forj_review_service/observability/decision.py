"""Privacy-preserving review decision summaries."""

import logging

from forj_review_service.contracts.review import FieldStatus, ReviewResponse

logger = logging.getLogger(__name__)


def log_review_decision(response: ReviewResponse, note: str = "") -> None:
    """记录字段键、状态和来源类型，不记录证据值；日志不代替独立回放证据。"""
    statuses = ",".join(f"{item.field}:{item.status.value}" for item in response.comparisons)
    conflicts = ",".join(
        f"{item.field}:"
        + "+".join(sorted({fact.document_type or fact.source for fact in item.evidence}))
        for item in response.comparisons
        if item.status == FieldStatus.CONFLICT
    )
    uncertain = ",".join(
        f"{item.field}:"
        + "+".join(
            sorted({fact.document_type or fact.source for fact in item.evidence if fact.uncertain})
        )
        for item in response.comparisons
        if any(fact.uncertain for fact in item.evidence)
    )
    logger.info(
        "review decision trace_id=%s business=%s region=%s profile=%s recommendation=%s"
        " note=%s fields=%s conflicts=%s uncertain=%s",
        response.trace_id,
        response.business_type,
        response.region,
        response.profile_version,
        response.recommendation.value,
        note or "-",
        statuses or "-",
        conflicts or "-",
        uncertain or "-",
    )
