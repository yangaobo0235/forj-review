"""文档提取批处理服务。

主要职责：管理并发、超时、失败分类和渐进式提取结果。
修改日期：2026-08-26
"""

import asyncio
import inspect
import logging
from collections.abc import Callable, Sequence
from typing import Any

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.material_policies import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
)
from forj_review_service.businesses.materials import DocumentPolicy
from forj_review_service.businesses.routing import (
    normalize_document_type,
)
from forj_review_service.contracts.recognition import (
    DocumentClassification,
    MaterialCompletenessReport,
    QwenExtraction,
    RecognitionBatchResult,
)
from forj_review_service.integrations.recognition.client import (
    QwenClient,
    restrict_extraction_to_policy,
)
from forj_review_service.integrations.recognition.errors import (
    classify_qwen_error,
    describe_qwen_error,
)
from forj_review_service.integrations.recognition.projection import build_extracted_evidence
from forj_review_service.integrations.recognition.retry import (
    RETRYABLE_UNCERTAIN_PREFIX,
)
from forj_review_service.integrations.recognition.workflow import ImageRecognitionWorkflow

# 整批提取在此限制并发与截止时间；异步作业逐图调用本服务，
# 跨任务的并发和公平性由 runtime/scheduling 中的调度器控制。
MAX_CONCURRENT_IMAGES = 6
IMAGE_TIMEOUT_SECONDS = 50.0
REVIEW_DEADLINE_SECONDS = 55.0
CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.70
logger = logging.getLogger(__name__)


def declared_material_types(business: BusinessContext) -> frozenset[str]:
    """Material candidates belong to the explicitly selected audit page."""
    return frozenset(business.document_policies)


ProgressCallback = Callable[[RecognitionBatchResult], Any]


class RecognitionService:
    """并发提取图片审核字段，同时确保确定性规则保留在模型之外。"""

    def __init__(
        self,
        client: QwenClient,
        *,
        max_concurrency: int = MAX_CONCURRENT_IMAGES,
        image_timeout: float = IMAGE_TIMEOUT_SECONDS,
        review_deadline: float = REVIEW_DEADLINE_SECONDS,
    ) -> None:
        """配置模型客户端、单图并发上限以及单图和整单截止时间。"""
        self.client = client
        self.max_concurrency = max_concurrency
        self.image_timeout = image_timeout
        self.review_deadline = review_deadline
        self.image_workflow = ImageRecognitionWorkflow(
            self._extract_image,
            build_extracted_evidence,
            self._focus_boxes,
            image_timeout,
        )

    async def extract_async(
        self,
        images: list[Any],
        on_progress: ProgressCallback | None = None,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        absolute_deadline: float | None = None,
        initial_report: MaterialCompletenessReport | None = None,
        allowed_document_types: frozenset[str] | None = None,
        *,
        business: BusinessContext = DEFAULT_BUSINESS,
    ) -> RecognitionBatchResult:
        """并发处理一批图片，在单图或整单超时时保留已经完成的部分结果。"""
        result = RecognitionBatchResult(
            total_count=len(images), material_completeness=initial_report
        )
        semaphore = asyncio.Semaphore(self.max_concurrency)
        loop = asyncio.get_running_loop()
        deadline = absolute_deadline or loop.time() + self.review_deadline
        deadline_expired = asyncio.Event()

        async def run_one(image: Any) -> None:
            """处理单张图片，并在每个终态向调用方发送进度快照。"""
            image_name = image.image_id or image.index
            first_read = None

            def preserve_first_read(extraction: QwenExtraction, policy: DocumentPolicy | None):
                nonlocal first_read
                first_read = (extraction, policy)

            if image.business_scope == "other":
                result.limitations.append(f"图片 {image_name} 不属于车辆审核资料，已跳过")
                result.completed_count += 1
                result.completed_image_ids.append(str(image_name))
                await self._notify(on_progress, result)
                return
            if image.collection_error or not image.recognition_source:
                result.failed_count += 1
                result.failed_image_ids.append(str(image_name))
                result.limitations.append(f"图片 {image_name} 内容不可用")
                await self._notify(on_progress, result)
                return
            started = loop.time()
            try:
                async with semaphore:
                    attempt_log = []
                    image_limitations = []
                    try:
                        outcome = await self.image_workflow.graph.ainvoke(
                            {
                                "image": image,
                                "business": business,
                                "allowed_document_types": allowed_document_types,
                                "retry_policy": retry_policy,
                                "deadline": deadline,
                                "attempt_deadline": min(deadline, loop.time() + self.image_timeout),
                                "attempts": attempt_log,
                                "limitations": image_limitations,
                                "on_first_read": preserve_first_read,
                                "deadline_expired": deadline_expired,
                            }
                        )
                    finally:
                        result.retry_summary.attempts.extend(attempt_log)
                        result.limitations.extend(image_limitations)
                    extraction, policy = outcome["extraction"], outcome["policy"]
                    retry_attempts = sum(attempt.result != "skipped" for attempt in attempt_log)
                    retry_result = attempt_log[-1].result if attempt_log else "-"
            except (TimeoutError, asyncio.CancelledError) as exc:
                if isinstance(exc, asyncio.CancelledError) and not deadline_expired.is_set():
                    raise
                if first_read is not None:
                    extraction, policy = first_read
                    outcome = {
                        "evidence": build_extracted_evidence(
                            image, extraction, policy, business=business
                        )
                    }
                    retry_attempts = sum(attempt.result != "skipped" for attempt in attempt_log)
                    retry_result = attempt_log[-1].result if attempt_log else "-"
                    result.limitations.append(
                        f"图片 {image_name} 补读截止，已保留首次读值与不确定状态"
                    )
                else:
                    result.timed_out_count += 1
                    result.timed_out_image_ids.append(str(image_name))
                    result.limitations.append(f"图片 {image_name} Qwen 识别超时")
                    logger.warning(
                        "Recognition image timed out: image=%s type=%s duration_ms=%d",
                        image_name,
                        image.category_hint or "-",
                        max(0, int((loop.time() - started) * 1000)),
                    )
                    await self._notify(on_progress, result)
                    return
            except RuntimeError as exc:
                error_code = classify_qwen_error(exc)
                result.failed_count += 1
                result.failed_image_ids.append(str(image_name))
                result.limitations.append(f"图片 {image_name} Qwen 处理失败（{error_code}）")
                logger.warning(
                    "Recognition image failed: image=%s type=%s error_type=%s error_code=%s"
                    " error_detail=%s duration_ms=%d",
                    image_name,
                    image.category_hint or "-",
                    type(exc).__name__,
                    error_code,
                    describe_qwen_error(exc) or "none",
                    max(0, int((loop.time() - started) * 1000)),
                )
                await self._notify(on_progress, result)
                return

            type_confidence = extraction.type_confidence
            if type_confidence is None:
                type_confidence = extraction.confidence
            if (
                type_confidence is not None
                and type_confidence >= CLASSIFICATION_CONFIDENCE_THRESHOLD
            ):
                sides = extraction.identity_sides
                side = extraction.fields.get("identity_card.side")
                if (
                    not sides
                    and isinstance(side, str)
                    and side in {"FRONT", "BACK"}
                    and "identity_card.side" not in extraction.uncertain_fields
                ):
                    sides = [side]
                result.document_classifications.append(
                    DocumentClassification(
                        target_id=str(image_name),
                        document_type=extraction.document_type,
                        business_scope=image.business_scope,
                        identity_sides=sides if extraction.document_type == "identity_card" else [],
                    )
                )
            if extraction.location_warnings:
                result.limitations.append(f"图片 {image_name} 部分原文定位不可用，原文读值已保留")
            hints = {image.document_type_hint, image.category_hint} & set(
                business.document_policies
            )
            if hints and extraction.document_type not in hints:
                result.limitations.append(
                    f"图片 {image_name} 实际类型与页面标签不一致，请核对上传位置"
                )
            if (
                policy is None
                or normalize_document_type(extraction.document_type) != policy.document_type
                or extraction.type_confidence is not None
                and extraction.type_confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD
            ):
                result.failed_count += 1
                result.failed_image_ids.append(str(image_name))
                result.limitations.append(f"图片 {image_name} 资料类型不一致，请人工复核")
                await self._notify(on_progress, result)
                return

            document, observations, routing_limitation = outcome["evidence"]
            result.recognized_documents.append(document)
            result.observations.extend(observations)
            if routing_limitation:
                result.limitations.append(f"图片 {image_name}：{routing_limitation}")
            accepted_fields = [item.field for item in observations]
            if extraction.confidence is not None:
                result.confidences.append(extraction.confidence)
            result.completed_count += 1
            result.completed_image_ids.append(str(image_name))
            logger.info(
                "Recognition image completed: image=%s type=%s category_hint=%s"
                " document_type_hint=%s accepted_field_count=%d accepted_fields=%s"
                " uncertain_field_count=%d uncertain_fields=%s retry_attempts=%d"
                " retry_result=%s duration_ms=%d",
                image_name,
                policy.document_type,
                # 前端从页面文案判出来的类型，和后端最终采用的政策并排打出来。
                # 两者不一致可用于排查材料类型跨业务误配。
                image.category_hint or "-",
                image.document_type_hint or "-",
                len(observations),
                # 只记录字段键，便于排查材料路由缺失，避免记录证件原值。
                ",".join(sorted(accepted_fields)) or "-",
                len(extraction.uncertain_fields),
                # 一票否决的直接线索：模型自己报了不确定，人工复核会在这里
                # 被触发。看不到字段名就只能翻响应体排查。
                ",".join(sorted(extraction.uncertain_fields)) or "-",
                # 剩下几个不确定字段是"第一次就这样"还是"重读之后还是这样"，
                # 决定了下一步该改提示词还是该改图片质量。
                retry_attempts,
                retry_result,
                max(0, int((loop.time() - started) * 1000)),
            )
            await self._notify(on_progress, result)

        tasks = {asyncio.create_task(run_one(image)): image for image in images}
        if not tasks:
            return result
        try:
            done, pending = await asyncio.wait(
                tasks, timeout=max(0, deadline - loop.time()), return_when=asyncio.FIRST_EXCEPTION
            )
            # Expected recognition errors are represented in the batch. Programming
            # errors and failed progress callbacks must reach the caller.
            for task in done:
                task.result()
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        # A retry may preserve its first-pass evidence when cancellation reaches it.
        deadline_expired.set()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            finished = set(
                result.completed_image_ids + result.failed_image_ids + result.timed_out_image_ids
            )
            for task in pending:
                image = tasks[task]
                image_name = str(image.image_id or image.index)
                if image_name not in finished:
                    result.timed_out_count += 1
                    result.timed_out_image_ids.append(image_name)
                    result.limitations.append(f"图片 {image_name} 超过整单审核时限")
            await self._notify(on_progress, result)
        return result

    async def _extract_image(
        self,
        image: Any,
        *,
        retry_reason: str | None = None,
        focus_boxes: Sequence[Sequence[float]] | None = None,
        allowed_document_types: frozenset[str] | None = None,
        business: BusinessContext = DEFAULT_BUSINESS,
    ) -> tuple[QwenExtraction, DocumentPolicy | None]:
        """One content-based type confirmation and extraction, independent of upload order."""
        extraction = await self.client.extract_document(
            image.model_dump(),
            retry_reason=retry_reason,
            focus_boxes=focus_boxes,
            business=business,
        )
        policy = business.document_policies.get(extraction.document_type)
        if (
            allowed_document_types is not None
            and extraction.document_type not in allowed_document_types
        ):
            policy = None
        if policy is not None:
            extraction = restrict_extraction_to_policy(extraction, policy, image.business_scope)
        return extraction, policy

    @staticmethod
    def _focus_boxes(extraction: QwenExtraction | None, reason: str) -> list[list[float]]:
        """把待重读字段的原图区域作为模型提示，不处理图片字节。"""
        if extraction is None or not reason.startswith(RETRYABLE_UNCERTAIN_PREFIX):
            return []
        fields = reason.removeprefix(RETRYABLE_UNCERTAIN_PREFIX).split(",")
        return [
            list(region.box) for region in extraction.evidence_regions if region.field in fields
        ]

    @staticmethod
    async def _notify(
        callback: ProgressCallback | None,
        result: RecognitionBatchResult,
    ) -> None:
        """向同步或异步回调发送隔离的结果快照。"""
        if callback is None:
            return
        callback_result = callback(result.model_copy(deep=True))
        if inspect.isawaitable(callback_result):
            await callback_result
