"""仅把明确可读的观察作为确定性政策和主体关系证据。"""

from forj_review_assistant.businesses.routing import route_fields
from forj_review_assistant.contracts.recognition import AgentBatchResult
from forj_review_assistant.contracts.review import FieldObservation, ReviewRequest
from forj_review_assistant.evidence.comparison.aggregate import EVIDENCE_SOURCE_LABELS
from forj_review_assistant.evidence.fields.readability import readable_value as readable_value


def trusted_page_value(request: ReviewRequest, field: str):
    """存在多个候选控件的字段没有可信的页面值，不能用来证明冲突。"""
    return (None if field in request.collection_diagnostics.ambiguous_fields
            else request.page_fields.get(field))


def batch_observations(batch: AgentBatchResult) -> list[FieldObservation]:
    """在真实批处理边界传播模型不确定标记，保留原值供人工查看。

    同时标记该图片是否已经重读过：重读后仍不确定，理由文本会和"从未重读"
    区分开，审核员据此知道机器已经试过了。
    """
    retried_targets = {
        attempt.target_id
        for attempt in batch.retry_summary.attempts
        if attempt.stage == "qwen" and attempt.attempt_number > 1
    }
    result = []
    for item in batch.observations:
        uncertain = item.uncertain
        retried = False
        for document in batch.recognized_documents:
            if document.target_id not in {item.source_id, item.image_id} and not (
                document.image_index is not None
                and document.image_index == item.image_index
            ):
                continue
            retried = retried or document.target_id in retried_targets
            routed, _ = route_fields(
                item.business_scope or document.business_scope,
                document.document_type,
                dict.fromkeys(document.uncertain_fields, True),
                group_order=item.group_order,
            )
            uncertain = (
                uncertain
                or item.field in document.uncertain_fields
                or item.source_field in document.uncertain_fields
                or item.field in routed
            )
        result.append(item.model_copy(update={"uncertain": uncertain, "retried": retried}))
    return result


def observation_evidence(observations: list[FieldObservation]) -> list[dict]:
    return [
        {
            "source": EVIDENCE_SOURCE_LABELS.get(item.source_type, item.source_type),
            **item.model_dump(),
        }
        for item in observations
    ]
