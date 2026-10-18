"""文档提取批处理服务。

主要职责：管理并发、超时、失败分类和渐进式提取结果。
修改日期：2026-08-26
"""

import asyncio
import inspect
import logging
from collections.abc import Callable, Sequence
from typing import Any

from forj_review_assistant.businesses.context import ContextMapping, current_pack
from forj_review_assistant.businesses.material_policies import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
)
from forj_review_assistant.businesses.materials import DOCUMENT_POLICIES, DocumentPolicy
from forj_review_assistant.businesses.packs import BUSINESS_PACKS
from forj_review_assistant.businesses.packs.model import slot_document_types
from forj_review_assistant.businesses.routing import (
    normalize_document_type,
)
from forj_review_assistant.contracts.recognition import (
    AgentBatchResult,
    DocumentClassification,
    MaterialCompletenessReport,
    QwenExtraction,
    RetryAttempt,
)
from forj_review_assistant.integrations.recognition.client import QwenClient
from forj_review_assistant.integrations.recognition.errors import (
    classify_qwen_error,
    describe_qwen_error,
)
from forj_review_assistant.integrations.recognition.projection import build_extracted_evidence
from forj_review_assistant.integrations.recognition.retry import (
    RETRYABLE_UNCERTAIN_PREFIX,
    retry_reason_for_error,
    retry_reason_for_extraction,
)

# 整批提取在此限制并发与截止时间；异步作业逐图调用本服务，
# 跨任务的并发和公平性由 runtime/scheduling 中的调度器控制。
MAX_CONCURRENT_IMAGES = 6
IMAGE_TIMEOUT_SECONDS = 50.0
REVIEW_DEADLINE_SECONDS = 55.0
CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.70
logger = logging.getLogger(__name__)

# 上传槽位 → 材料类型来自业务声明；前端也读同一份，不再各写一遍。
# 槽位键带业务分区，各业务分区互不重叠，可以直接合成一张表。
SLOT_DOCUMENT_TYPES = {
    slot: document_type
    for pack in BUSINESS_PACKS.values()
    for slot, document_type in slot_document_types(pack).items()
}
# 这些提示是页面业务归属（分区）名称，不是资料类型；不能拿去查资料白名单。
GENERIC_SCOPE_HINTS = {"", "unknown"} | {
    scope for pack in BUSINESS_PACKS.values() for scope in pack.scopes
}



SLOT_DOCUMENT_TYPES = ContextMapping(SLOT_DOCUMENT_TYPES, slot_document_types)


def declared_material_types(business_type: Any) -> frozenset[str] | None:
    """该业务扩展包声明的材料类型集合；没有扩展包时返回 None 表示不限制。

    候选必须限制在本业务自己声明的材料里，避免通用材料关键词
    命中其他业务的类型，导致读取白名单错误。

    返回 None 而不是空集合：尚未声明扩展包的业务没有这份名单，空集合会把它们
    全部推进两阶段分类，那是行为回退而不是收紧。
    """
    from forj_review_assistant.businesses.packs import pack_for_business
    pack = pack_for_business(business_type)
    if pack is None:
        return None
    return frozenset(material.document_type for material in pack.materials)


def _expected_document_type(
    image: Any,
    allowed_document_types: frozenset[str] | None = None,
) -> str | None:
    """Resolve a physical document type from an explicit label or fixed upload slot."""
    for raw_hint in (
        getattr(image, "document_type_hint", "unknown"),
        getattr(image, "category_hint", "unknown"),
    ):
        hint = str(raw_hint or "").strip()
        active_pack = current_pack()
        if hint in ({"", "unknown", *active_pack.scopes} if active_pack else GENERIC_SCOPE_HINTS):
            continue
        normalized = normalize_document_type(hint)
        # 命中全局政策表还不够：它还得是本业务声明过的材料，否则就是拿别的
        # 业务的白名单在提取这张图。
        if normalized in DOCUMENT_POLICIES and (
            allowed_document_types is None or normalized in allowed_document_types
        ):
            return normalized
    return SLOT_DOCUMENT_TYPES.get(
        (getattr(image, "business_scope", "unknown"), getattr(image, "group_order", None))
    )

ProgressCallback = Callable[[AgentBatchResult], Any]


class AgentService:
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

    async def extract_async(
        self,
        images: list[Any],
        on_progress: ProgressCallback | None = None,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        absolute_deadline: float | None = None,
        initial_report: MaterialCompletenessReport | None = None,
        allowed_document_types: frozenset[str] | None = None,
    ) -> AgentBatchResult:
        """并发处理一批图片，在单图或整单超时时保留已经完成的部分结果。"""
        result = AgentBatchResult(total_count=len(images), material_completeness=initial_report)
        semaphore = asyncio.Semaphore(self.max_concurrency)
        loop = asyncio.get_running_loop()
        deadline = absolute_deadline or loop.time() + self.review_deadline

        async def run_one(image: Any) -> None:
            """处理单张图片，并在每个终态向调用方发送进度快照。"""
            image_name = image.image_id or image.index
            classification: DocumentClassification | None = None

            def remember_classification(fact: DocumentClassification) -> None:
                nonlocal classification
                classification = fact
                result.document_classifications[:] = [
                    item for item in result.document_classifications if item.target_id != fact.target_id
                ]
                result.document_classifications.append(fact)

            expected_type = _expected_document_type(image, allowed_document_types)
            if expected_type:
                remember_classification(DocumentClassification(
                    target_id=str(image_name), document_type=expected_type,
                    business_scope=image.business_scope,
                    identity_sides=image.identity_sides_hint if expected_type == "identity_card" else [],
                ))
            if image.business_scope == "other":
                result.limitations.append(
                    f"图片 {image_name} 不属于车辆审核资料，已跳过"
                )
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
                    first_error: RuntimeError | None = None
                    # 本次重试的实际情况。`retry_attempts` 只数真正跑过的重读，
                    # 时间不够没跑成的那种记在 `retry_result=skipped` 里。
                    retry_attempts = 0
                    retry_result = "-"
                    try:
                        extraction, policy = await asyncio.wait_for(
                            self._extract_image(
                                image,
                                allowed_document_types=allowed_document_types,
                                known_classification=classification,
                                on_classified=remember_classification,
                            ),
                            timeout=min(self.image_timeout, max(0.001, deadline - loop.time())),
                        )
                        reason = retry_reason_for_extraction(extraction, policy)
                    except RuntimeError as exc:
                        first_error = exc
                        reason = retry_reason_for_error(exc)
                        extraction = policy = None
                    if first_error is not None and reason is None:
                        raise first_error
                    if reason and retry_policy.semantic_retry_count > 0:
                        remaining = deadline - loop.time()
                        if remaining < retry_policy.minimum_retry_window_seconds:
                            result.retry_summary.attempts.append(RetryAttempt(
                                target_id=str(image_name), stage="qwen", attempt_number=2,
                                reason_code=reason, strategy="focused_extraction", result="skipped", duration_ms=0,
                            ))
                            result.limitations.append(f"图片 {image_name} 满足重试条件但剩余时间不足")
                            retry_result = "skipped"
                            logger.info("review image retry image_id=%s attempt=2 reason=%s result=skipped duration_ms=0", image_name, reason)
                            if first_error is not None:
                                raise first_error
                        else:
                            retry_started = loop.time()
                            retry_attempts = 1
                            try:
                                extraction, policy = await asyncio.wait_for(
                                    self._extract_image(
                                        image,
                                        retry_reason=reason,
                                        focus_boxes=self._focus_boxes(extraction, reason),
                                        allowed_document_types=allowed_document_types,
                                        known_classification=classification,
                                        on_classified=remember_classification,
                                    ),
                                    timeout=min(self.image_timeout, remaining),
                                )
                            except (RuntimeError, TimeoutError):
                                result.retry_summary.attempts.append(RetryAttempt(
                                    target_id=str(image_name), stage="qwen", attempt_number=2,
                                    reason_code=reason, strategy="focused_extraction", result="failed",
                                    duration_ms=max(0, int((loop.time() - retry_started) * 1000)),
                                ))
                                logger.warning("review image retry image_id=%s attempt=2 reason=%s result=failed duration_ms=%d", image_name, reason, int((loop.time() - retry_started) * 1000))
                                raise
                            result.retry_summary.attempts.append(RetryAttempt(
                                target_id=str(image_name), stage="qwen", attempt_number=2,
                                reason_code=reason, strategy="focused_extraction", result="succeeded",
                                duration_ms=max(0, int((loop.time() - retry_started) * 1000)),
                            ))
                            retry_result = "succeeded"
                            logger.info("review image retry image_id=%s attempt=2 reason=%s result=succeeded duration_ms=%d", image_name, reason, int((loop.time() - retry_started) * 1000))
            except TimeoutError:
                result.timed_out_count += 1
                result.timed_out_image_ids.append(str(image_name))
                result.limitations.append(f"图片 {image_name} Qwen 识别超时")
                logger.warning(
                    "Agent image timed out: image=%s type=%s duration_ms=%d",
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
                    "Agent image failed: image=%s type=%s error_type=%s error_code=%s"
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

            if (
                policy is None
                or normalize_document_type(extraction.document_type)
                != policy.document_type
            ):
                result.failed_count += 1
                result.failed_image_ids.append(str(image_name))
                result.limitations.append(
                    f"图片 {image_name} 资料类型不一致，请人工复核"
                )
                await self._notify(on_progress, result)
                return

            document, observations, routing_limitation = build_extracted_evidence(image, extraction, policy)
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
                "Agent image completed: image=%s type=%s category_hint=%s"
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
        _, pending = await asyncio.wait(tasks, timeout=max(0, deadline - loop.time()))
        # 整单截止后取消剩余任务，但不丢弃截止前已经生成的观察结果。
        for task in pending:
            task.cancel()
            image = tasks[task]
            image_name = image.image_id or image.index
            result.timed_out_count += 1
            result.timed_out_image_ids.append(str(image_name))
            result.limitations.append(f"图片 {image_name} 超过整单审核时限")
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            await self._notify(on_progress, result)
        return result

    async def _extract_image(
        self,
        image: Any,
        *,
        retry_reason: str | None = None,
        focus_boxes: Sequence[Sequence[float]] | None = None,
        allowed_document_types: frozenset[str] | None = None,
        known_classification: DocumentClassification | None = None,
        on_classified: Callable[[DocumentClassification], None] | None = None,
    ) -> tuple[QwenExtraction, DocumentPolicy | None]:
        """已知槽位使用专属提示词；真正未知的图片先分类再专属提取。

        URL 图片直接交给模型重读原图，区域坐标随重读原因传递。
        """
        payload = image.model_dump()
        if focus_boxes:
            retry_reason = (retry_reason or "") + "；重点重读原图归一化区域 " + str(list(focus_boxes))
        expected_type = (
            known_classification.document_type if known_classification
            else _expected_document_type(image, allowed_document_types)
        )
        policy = DOCUMENT_POLICIES.get(expected_type or "")
        if policy is None:
            classification = await self.client.classify_document(payload)
            classified_type = normalize_document_type(classification.document_type)
            if (
                classified_type not in DOCUMENT_POLICIES
                or allowed_document_types is not None and classified_type not in allowed_document_types
                or classification.confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD
            ):
                raise RuntimeError("资料类型无法可靠识别")
            policy = DOCUMENT_POLICIES[classified_type]
            known_classification = DocumentClassification(
                target_id=str(image.image_id or image.index), document_type=classified_type,
                business_scope=image.business_scope,
                identity_sides=classification.identity_sides if classified_type == "identity_card" else [],
            )
            if on_classified:
                on_classified(known_classification)
        extract_fields = self.client.extract_fields
        extraction = await extract_fields(
            payload,
            policy,
            **self._retry_kwargs(extract_fields, retry_reason),
        )
        normalized = extraction.model_copy(
            update={"document_type": normalize_document_type(extraction.document_type)}
        )
        side = extraction.fields.get("identity_card.side")
        if (
            known_classification and policy.document_type == "identity_card"
            and normalize_document_type(extraction.document_type) == "identity_card"
            and isinstance(side, str) and side in {"FRONT", "BACK"}
            and "identity_card.side" not in extraction.uncertain_fields
            and (extraction.confidence is None or extraction.confidence >= CLASSIFICATION_CONFIDENCE_THRESHOLD)
            and not known_classification.identity_sides and on_classified
        ):
            on_classified(known_classification.model_copy(update={"identity_sides": [side]}))
        return normalized, policy

    @staticmethod
    def _focus_boxes(extraction: QwenExtraction, reason: str) -> list[list[float]]:
        """把待重读字段的原图区域作为模型提示，不处理图片字节。"""
        if not reason.startswith(RETRYABLE_UNCERTAIN_PREFIX):
            return []
        fields = reason.removeprefix(RETRYABLE_UNCERTAIN_PREFIX).split(",")
        return [list(region.box) for region in extraction.evidence_regions if region.field in fields]

    @staticmethod
    def _retry_kwargs(method: Callable[..., Any], retry_reason: str | None) -> dict[str, str]:
        """仅向支持新契约的客户端传递纠错原因，兼容现有受控测试客户端。"""
        if retry_reason is None:
            return {}
        parameters = inspect.signature(method).parameters.values()
        if any(
            parameter.name == "retry_reason"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        ):
            return {"retry_reason": retry_reason}
        return {}

    @staticmethod
    async def _notify(
        callback: ProgressCallback | None,
        result: AgentBatchResult,
    ) -> None:
        """向同步或异步回调发送隔离的结果快照。"""
        if callback is None:
            return
        callback_result = callback(result.model_copy(deep=True))
        if inspect.isawaitable(callback_result):
            await callback_result
