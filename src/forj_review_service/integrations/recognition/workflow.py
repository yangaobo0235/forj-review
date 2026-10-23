"""Single-image recognition stages; batch scheduling remains outside the graph."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from forj_review_service.businesses.context import BusinessContext
from forj_review_service.businesses.material_policies import RetryPolicy
from forj_review_service.businesses.materials import DocumentPolicy
from forj_review_service.contracts.recognition import (
    QwenExtraction,
    RecognizedDocument,
    RetryAttempt,
)
from forj_review_service.contracts.review import FieldObservation, ImageInput
from forj_review_service.integrations.recognition.retry import (
    retry_reason_for_error,
    retry_reason_for_extraction,
)

Evidence = tuple[RecognizedDocument, list[FieldObservation], str | None]

logger = logging.getLogger(__name__)


class ImageState(TypedDict, total=False):
    image: ImageInput
    business: BusinessContext
    allowed_document_types: frozenset[str] | None
    retry_policy: RetryPolicy
    deadline: float
    attempt_deadline: float
    extraction: QwenExtraction
    policy: DocumentPolicy | None
    error: RuntimeError | None
    retry_reason: str | None
    attempts: list[RetryAttempt]
    limitations: list[str]
    evidence: Evidence | None
    on_first_read: Callable[[QwenExtraction, DocumentPolicy | None], None]
    deadline_expired: asyncio.Event


class ImageRecognitionWorkflow:
    def __init__(
        self,
        extract: Callable[..., Awaitable[tuple[QwenExtraction, DocumentPolicy | None]]],
        project: Callable[..., Evidence],
        focus_boxes: Callable[[QwenExtraction | None, str], list[list[float]]],
        image_timeout: float,
    ):
        self.extract, self.project = extract, project
        self.focus_boxes, self.image_timeout = focus_boxes, image_timeout
        builder = StateGraph(ImageState)
        for name in ("extract", "assess", "focused_retry", "project"):
            builder.add_node(name, getattr(self, "_" + name))
        builder.add_edge(START, "extract")
        builder.add_edge("extract", "assess")
        builder.add_conditional_edges(
            "assess", lambda state: "focused_retry" if state.get("retry_reason") else "project"
        )
        builder.add_edge("focused_retry", "project")
        builder.add_edge("project", END)
        self.graph = builder.compile(checkpointer=False)

    async def _extract(self, state: ImageState):
        try:
            if asyncio.get_running_loop().time() >= state["attempt_deadline"]:
                raise TimeoutError
            async with asyncio.timeout_at(state["attempt_deadline"]):
                extraction, policy = await self.extract(
                    state["image"],
                    business=state["business"],
                    allowed_document_types=state["allowed_document_types"],
                )
            if self._usable(extraction, policy):
                state["on_first_read"](extraction, policy)
            return {"extraction": extraction, "policy": policy, "error": None}
        except RuntimeError as exc:
            return {"error": exc}

    async def _assess(self, state: ImageState):
        error = state.get("error")
        reason = (
            retry_reason_for_error(error)
            if error
            else retry_reason_for_extraction(state["extraction"], state["policy"])
        )
        if not reason or state["retry_policy"].semantic_retry_count == 0:
            return {"retry_reason": None}
        if state["deadline"] - asyncio.get_running_loop().time() < max(
            state["retry_policy"].minimum_retry_window_seconds, min(10, self.image_timeout / 5)
        ):
            self._record(state, reason, "skipped", 0)
            state["limitations"].append(f"图片 {self._image_id(state)} 满足重试条件但剩余时间不足")
            return {"retry_reason": None}
        return {"retry_reason": reason}

    async def _focused_retry(self, state: ImageState):
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            async with asyncio.timeout_at(min(state["deadline"], started + self.image_timeout)):
                extraction, policy = await self.extract(
                    state["image"],
                    retry_reason=state["retry_reason"],
                    focus_boxes=self.focus_boxes(state.get("extraction"), state["retry_reason"]),
                    allowed_document_types=state["allowed_document_types"],
                    business=state["business"],
                )
        except (RuntimeError, TimeoutError, asyncio.CancelledError) as exc:
            self._record(
                state, state["retry_reason"], "failed", int((loop.time() - started) * 1000)
            )
            if isinstance(exc, asyncio.CancelledError) and not state["deadline_expired"].is_set():
                raise
            if not self._usable(state.get("extraction"), state.get("policy")):
                raise
            state["limitations"].append(
                f"图片 {self._image_id(state)} 补充识别失败，已保留首次读值与不确定状态"
            )
            return {"retry_reason": None, "error": None}
        self._record(state, state["retry_reason"], "succeeded", int((loop.time() - started) * 1000))
        previous = state.get("extraction")
        if self._usable(previous, state.get("policy")):
            if extraction.document_type != previous.document_type:
                state["limitations"].append(
                    f"图片 {self._image_id(state)} 两次资料类型不一致，请人工复核"
                )
                return {
                    "extraction": previous.model_copy(
                        update={
                            "uncertain_fields": sorted(
                                set(previous.uncertain_fields) | set(previous.fields)
                            ),
                        }
                    ),
                    "error": None,
                }
            if not self._usable(extraction, policy):
                state["limitations"].append(
                    f"图片 {self._image_id(state)} 补读类型无法确认，保留首次结果"
                )
                return {"error": None}
            extraction = merge_extractions(previous, extraction)
        return {"extraction": extraction, "policy": policy, "error": None}

    @staticmethod
    def _usable(extraction: QwenExtraction | None, policy: DocumentPolicy | None) -> bool:
        return bool(
            extraction is not None
            and policy is not None
            and extraction.document_type == policy.document_type
            and (extraction.type_confidence is None or extraction.type_confidence >= 0.7)
        )

    async def _project(self, state: ImageState):
        if state.get("error") is not None:
            raise state["error"]
        extraction, policy = state["extraction"], state["policy"]
        if not self._usable(extraction, policy):
            return {"evidence": None}
        return {
            "evidence": self.project(state["image"], extraction, policy, business=state["business"])
        }

    @staticmethod
    def _image_id(state: ImageState) -> str:
        return str(state["image"].image_id or state["image"].index)

    def _record(self, state: ImageState, reason: str, result: str, duration: int) -> None:
        state["attempts"].append(
            RetryAttempt(
                target_id=self._image_id(state),
                stage="qwen",
                attempt_number=2,
                reason_code=reason,
                strategy="focused_extraction",
                result=result,
                duration_ms=max(0, duration),
            )
        )
        logger.info(
            "review image retry image_id=%s attempt=2 reason=%s result=%s duration_ms=%d",
            self._image_id(state),
            reason,
            result,
            duration,
        )


def merge_extractions(first: QwenExtraction, second: QwenExtraction) -> QwenExtraction:
    """Supplement uncertain readings; retain reliable disagreements as separate evidence."""
    fields = dict(first.fields)
    alternatives = {key: list(values) for key, values in first.field_alternatives.items()}
    unresolved = set(first.uncertain_fields)
    confidences = {key: first.field_confidences.get(key, first.confidence) for key in first.fields}
    for key, value in second.fields.items():
        was_reliable = (
            key in fields
            and key not in unresolved
            and (first.confidence is None or first.confidence >= 0.7)
        )
        is_reliable = key not in second.uncertain_fields and (
            second.confidence is None or second.confidence >= 0.7
        )
        if was_reliable:
            if is_reliable and value != fields[key]:
                values = alternatives.setdefault(key, [fields[key]])
                if value not in values:
                    values.append(value)
        else:
            fields[key] = value
            confidences[key] = second.field_confidences.get(key, second.confidence)
            if is_reliable:
                unresolved.discard(key)
                alternatives.pop(key, None)
    unresolved.update(
        key
        for key in second.uncertain_fields
        if key not in first.fields or key in first.uncertain_fields
    )
    for key, values in second.field_alternatives.items():
        if key in first.fields and key not in first.uncertain_fields:
            if (
                key in second.uncertain_fields
                or second.confidence is not None
                and second.confidence < 0.7
            ):
                continue
        existing = alternatives.setdefault(key, [fields[key]] if key in fields else [])
        existing.extend(value for value in values if value not in existing)
    rows = list(first.raw_fields)
    rows.extend(row for row in second.raw_fields if row not in rows)
    regions = list(first.evidence_regions)
    regions.extend(region for region in second.evidence_regions if region not in regions)
    return second.model_copy(
        update={
            "fields": fields,
            "field_alternatives": alternatives,
            "field_confidences": confidences,
            "uncertain_fields": sorted(unresolved),
            "raw_fields": rows,
            "evidence_regions": regions,
            "location_warnings": list(
                dict.fromkeys(first.location_warnings + second.location_warnings)
            ),
        }
    )
