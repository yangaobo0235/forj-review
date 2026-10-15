"""文档提取批处理服务。

主要职责：管理并发、超时、失败分类和渐进式提取结果。
修改日期：2026-08-26
"""

import asyncio
import inspect
import logging
import re
from collections.abc import Callable, Mapping, Sequence
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
    route_fields,
)
from forj_review_assistant.contracts.recognition import (
    AgentBatchResult,
    MaterialCompletenessReport,
    QwenExtraction,
    RecognizedDocument,
    RetryAttempt,
)
from forj_review_assistant.contracts.review import FieldObservation
from forj_review_assistant.integrations.recognition.client import QwenClient
from forj_review_assistant.integrations.recognition.errors import (
    classify_qwen_error,
    describe_qwen_error,
)
from forj_review_assistant.integrations.recognition.retry import (
    RETRYABLE_UNCERTAIN_PREFIX,
    retry_reason_for_error,
    retry_reason_for_extraction,
)

# 并发与超时语义分两条路径，容易混淆：
# - 逐图流式路径（POST /api/review/jobs/{id}/images）每次只提交一张图，
#   因此 MAX_CONCURRENT_IMAGES 永远不会被争用，REVIEW_DEADLINE_SECONDS
#   也会退化为单图截止。真正生效的整单并发上限在 services.jobs 的
#   Fair Scheduler（REVIEW_MODEL_GLOBAL_CONCURRENCY /
#   REVIEW_MODEL_JOB_CONCURRENCY），不在本模块。
# - 整批路径（extract_async）一次传入全部图片，这里的并发上限和整单
#   截止才按本意生效。
MAX_CONCURRENT_IMAGES = 6
IMAGE_TIMEOUT_SECONDS = 50.0
REVIEW_DEADLINE_SECONDS = 55.0
CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.70
logger = logging.getLogger("uvicorn.error")

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


def _derived_from(
    policy: Any, field_name: str, extracted_fields: Mapping[str, Any]
) -> str | None:
    """该字段的值是不是从票面另一个号码派生的；是则返回那个号码的字段键。

    数电发票票面只有一个号码，页面上的「发票代码」由兼容层补齐。派生关系写在
    材料声明里（`MaterialDeclaration.derived_field`），这里只负责查。
    """
    derived = getattr(policy, "derived_field", None)
    if derived is None:
        return None
    number_field, derived_field = derived
    if field_name == derived_field and number_field in extracted_fields:
        return number_field
    return None


def _covered_pages(value: Any) -> list[int]:
    """Normalize page numbers returned as arrays or common OCR text variants."""

    values = value if isinstance(value, list) else [value]
    pages: set[int] = set()
    for item in values:
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            number = int(item)
            if number > 0:
                pages.add(number)
            continue
        for raw in re.findall(r"(?:第\s*)?(\d{1,2})(?:\s*页)?", str(item)):
            number = int(raw)
            if number > 0:
                pages.add(number)
    return sorted(pages)


def declared_material_types(business_type: Any) -> frozenset[str] | None:
    """该业务扩展包声明的材料类型集合；没有扩展包时返回 None 表示不限制。

    `_expected_document_type` 以前查的是跨业务合并的全局政策表，于是报废置换的
    「机动车销售发票」会被过户页面上「二手车发票」的小标题命中：类型串了业务，
    字段白名单跟着错，最后必然"材料类型不符"重试一次再失败。候选必须限制在
    本业务自己声明的材料里。

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
                            if first_error is not None:
                                raise first_error
                        else:
                            started = loop.time()
                            retry_attempts = 1
                            try:
                                extraction, policy = await asyncio.wait_for(
                                    self._extract_image(
                                        image,
                                        retry_reason=reason,
                                        focus_boxes=self._focus_boxes(extraction, reason),
                                        allowed_document_types=allowed_document_types,
                                    ),
                                    timeout=min(self.image_timeout, remaining),
                                )
                            except (RuntimeError, TimeoutError):
                                result.retry_summary.attempts.append(RetryAttempt(
                                    target_id=str(image_name), stage="qwen", attempt_number=2,
                                    reason_code=reason, strategy="focused_extraction", result="failed",
                                    duration_ms=max(0, int((loop.time() - started) * 1000)),
                                ))
                                raise
                            result.retry_summary.attempts.append(RetryAttempt(
                                target_id=str(image_name), stage="qwen", attempt_number=2,
                                reason_code=reason, strategy="focused_extraction", result="succeeded",
                                duration_ms=max(0, int((loop.time() - started) * 1000)),
                            ))
                            retry_result = "succeeded"
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

            result.recognized_documents.append(RecognizedDocument(
                target_id=str(image_name), image_index=image.index,
                document_type=policy.document_type, business_scope=image.business_scope,
                covered_pages=_covered_pages(extraction.fields.get("registration.covered_pages")),
                uncertain_fields=list(extraction.uncertain_fields),
                uncertain_values={field: extraction.fields.get(field) for field in extraction.uncertain_fields},
                qr_codes=list(dict.fromkeys(code.strip() for code in extraction.qr_codes if code.strip())),
            ))

            routed_fields, routing_limitation = route_fields(
                image.business_scope,
                policy.document_type,
                extraction.fields,
                group_order=image.group_order,
            )
            routed_regions: dict[str, list[float]] = {}
            source_regions = {region.field: list(region.box) for region in extraction.evidence_regions}
            for region in extraction.evidence_regions:
                region_fields, _ = route_fields(
                    image.business_scope,
                    policy.document_type,
                    {region.field: "__evidence_region__"},
                    group_order=image.group_order,
                )
                for routed_field in region_fields:
                    routed_regions.setdefault(routed_field, list(region.box))
            if routing_limitation:
                result.limitations.append(f"图片 {image_name}：{routing_limitation}")
            accepted = 0
            accepted_fields: list[str] = []
            entries = [(k, v, None) for k, v in routed_fields.items()]
            for field_name, value, source_field in entries:
                if value:
                    result.observations.append(
                        FieldObservation(
                            field=field_name,
                            source_field=source_field,
                            uncertain=bool(source_field and source_field in extraction.uncertain_fields),
                            source_type="image",
                            source_id=str(image_name),
                            document_type=policy.document_type,
                            image_index=image.index,
                            image_id=str(image_name),
                            business_scope=image.business_scope,
                            group_title=image.group_title,
                            group_order=image.group_order,
                            value=value,
                            # 派生字段标出它来自哪个号码：工作台据此说明
                            # 「发票代码由票面数电号码适配」，不让审核员以为
                            # 票面上真印了一个发票代码。关系由材料声明给出。
                            derived_from=_derived_from(policy, field_name, extraction.fields),
                            evidence_region=source_regions.get(source_field) if source_field else routed_regions.get(field_name),
                            confidence=extraction.confidence,
                        )
                    )
                    accepted += 1
                    accepted_fields.append(field_name)
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
                # 两者不一致就是材料类型串了业务——报废置换的「机动车销售发票」
                # 和过户的「二手车销售统一发票」撞在一起时，只有这一行能看出来。
                image.category_hint or "-",
                image.document_type_hint or "-",
                accepted,
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
    ) -> tuple[QwenExtraction, DocumentPolicy | None]:
        """已知槽位使用专属提示词；真正未知的图片先分类再专属提取。

        URL 图片直接交给模型重读原图，区域坐标随重读原因传递。
        """
        payload = image.model_dump()
        if focus_boxes:
            retry_reason = (retry_reason or "") + "；重点重读原图归一化区域 " + str(list(focus_boxes))
        expected_type = _expected_document_type(image, allowed_document_types)
        policy = DOCUMENT_POLICIES.get(expected_type or "")
        if policy is not None:
            extract_fields = self.client.extract_fields
            extraction = await extract_fields(
                payload,
                policy,
                **self._retry_kwargs(extract_fields, retry_reason),
            )
            return extraction.model_copy(
                update={
                    "document_type": normalize_document_type(
                        extraction.document_type
                    )
                }
            ), policy

        classification = await self.client.classify_document(payload)
        classified_type = normalize_document_type(classification.document_type)
        if (
            classified_type not in DOCUMENT_POLICIES
            or classification.confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD
        ):
            raise RuntimeError("资料类型无法可靠识别")
        policy = DOCUMENT_POLICIES[classified_type]
        extract_fields = self.client.extract_fields
        extraction = await extract_fields(
            payload,
            policy,
            **self._retry_kwargs(extract_fields, retry_reason),
        )
        normalized = extraction.model_copy(
            update={"document_type": normalize_document_type(extraction.document_type)}
        )
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
