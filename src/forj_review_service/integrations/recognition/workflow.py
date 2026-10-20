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
    DocumentClassification,
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
    classification: DocumentClassification | None
    on_classified: Callable[[DocumentClassification], None] | None
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


class ImageRecognitionWorkflow:
    def __init__(
        self,
        classify: Callable[..., Awaitable[DocumentClassification]],
        extract: Callable[..., Awaitable[tuple[QwenExtraction, DocumentPolicy | None]]],
        project: Callable[..., Evidence],
        focus_boxes: Callable[[QwenExtraction | None, str], list[list[float]]],
        image_timeout: float,
    ):
        self.classify, self.extract, self.project = classify, extract, project
        self.focus_boxes, self.image_timeout = focus_boxes, image_timeout
        builder = StateGraph(ImageState)
        for name in ("classify", "extract", "assess", "focused_retry", "project"):
            builder.add_node(name, getattr(self, "_" + name))
        builder.add_edge(START, "classify")
        builder.add_conditional_edges(
            "classify", lambda state: "assess" if state.get("error") else "extract"
        )
        builder.add_edge("extract", "assess")
        builder.add_conditional_edges(
            "assess", lambda state: "focused_retry" if state.get("retry_reason") else "project"
        )
        builder.add_edge("focused_retry", "project")
        builder.add_edge("project", END)
        self.graph = builder.compile(checkpointer=False)

    async def _classify(self, state: ImageState):
        try:
            async with asyncio.timeout_at(state["attempt_deadline"]):
                classification = await self.classify(
                    state["image"],
                    state["allowed_document_types"],
                    state["classification"],
                    state["on_classified"],
                    business=state["business"],
                )
            return {"classification": classification, "error": None}
        except RuntimeError as exc:
            return {"error": exc}

    async def _extract(self, state: ImageState):
        try:
            async with asyncio.timeout_at(state["attempt_deadline"]):
                extraction, policy = await self.extract(
                    state["image"],
                    known_classification=state["classification"],
                    on_classified=state["on_classified"],
                    business=state["business"],
                    allowed_document_types=state["allowed_document_types"],
                )
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
        if (
            state["deadline"] - asyncio.get_running_loop().time()
            < state["retry_policy"].minimum_retry_window_seconds
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
                    known_classification=state.get("classification"),
                    on_classified=state["on_classified"],
                    allowed_document_types=state["allowed_document_types"],
                    business=state["business"],
                )
        except (RuntimeError, TimeoutError):
            self._record(
                state, state["retry_reason"], "failed", int((loop.time() - started) * 1000)
            )
            raise
        self._record(state, state["retry_reason"], "succeeded", int((loop.time() - started) * 1000))
        return {"extraction": extraction, "policy": policy, "error": None}

    async def _project(self, state: ImageState):
        if state.get("error") is not None:
            raise state["error"]
        extraction, policy = state["extraction"], state["policy"]
        if policy is None or extraction.document_type != policy.document_type:
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
