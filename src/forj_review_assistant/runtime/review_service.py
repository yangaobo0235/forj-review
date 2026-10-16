"""审核服务编排门面。

主要职责：协调业务配置、工作流和辅助工具，不内嵌响应策略。
修改日期：2026-08-26
"""

import asyncio
import inspect
import logging
from collections.abc import Callable, Mapping
from typing import Any

from forj_review_assistant.businesses.context import with_business_context
from forj_review_assistant.businesses.context_validation import validate_request_route
from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.businesses.registry import (
    BusinessRegistry,
    build_business_registry,
)
from forj_review_assistant.businesses.rules.scrap_certificate_qr import qr_candidates
from forj_review_assistant.capabilities.page_actions import PageActionHandler
from forj_review_assistant.capabilities.specs import (
    BusinessRuleHandler,
    ExternalCheckHandler,
)
from forj_review_assistant.config.settings import image_private_hosts, load_qwen_config
from forj_review_assistant.contracts.recognition import (
    AgentBatchResult,
    MaterialCompletenessReport,
)
from forj_review_assistant.contracts.review import (
    FieldStatus,
    ImageInput,
    QrCheck,
    ReviewRequest,
    ReviewResponse,
)
from forj_review_assistant.evidence.collection import collect_observations, collection_issues
from forj_review_assistant.integrations.qr.decoder import QrImageDecoder
from forj_review_assistant.integrations.qr.web_verifier import QrWebVerifier
from forj_review_assistant.integrations.recognition.client import QwenClient
from forj_review_assistant.integrations.recognition.extraction import (
    AgentService,
    declared_material_types,
)
from forj_review_assistant.observability.context import with_review_log_context
from forj_review_assistant.presentation.response import (
    build_review_response,
    build_unconfigured_response,
)
from forj_review_assistant.runtime.workflow.graph import ReviewWorkflow

ReviewProgressCallback = Callable[[ReviewResponse, AgentBatchResult], Any]
logger = logging.getLogger(__name__)


class ReviewService:
    """Coordinate document extraction, verification tools, and response building."""

    def __init__(
        self,
        registry: BusinessRegistry | None = None,
        external_check_handlers: Mapping[str, ExternalCheckHandler] | None = None,
        business_rule_handlers: Mapping[str, BusinessRuleHandler] | None = None,
        page_action_handlers: Mapping[str, PageActionHandler] | None = None,
        qr_decoder: QrImageDecoder | None = None,
        load_model: bool = True,
    ) -> None:
        config = load_qwen_config() if load_model else None
        self.qr_web = QrWebVerifier(allowed_hosts=["qclt.mofcom.gov.cn"])
        self.qr_decoder = qr_decoder or QrImageDecoder(private_hosts=image_private_hosts())
        self.registry = registry or build_business_registry()
        self.agent = AgentService(QwenClient(config)) if config and config.available else None
        self.workflow = ReviewWorkflow(
            self,
            external_check_handlers=external_check_handlers,
            business_rule_handlers=business_rule_handlers,
            page_action_handlers=page_action_handlers,
        )

    def assist(self, request: ReviewRequest) -> ReviewResponse:
        return asyncio.run(self.assist_async(request))

    async def assist_async(
        self,
        request: ReviewRequest,
        on_progress: ReviewProgressCallback | None = None,
    ) -> ReviewResponse:
        """解析业务配置并执行审核；未配置规则时安全降级。"""

        response, _ = await self.assist_with_batch_async(request, on_progress=on_progress)
        return response

    @with_business_context
    @with_review_log_context
    async def assist_with_batch_async(
        self,
        request: ReviewRequest,
        on_progress: ReviewProgressCallback | None = None,
        initial_batch: AgentBatchResult | None = None,
    ) -> tuple[ReviewResponse, AgentBatchResult]:
        """执行审核并复用 URL 任务已完成的图片提取结果。"""

        profile = self.resolve_profile(request)

        async def handle_agent_progress(batch: AgentBatchResult) -> None:
            if on_progress is None:
                return
            progress_response = self._build_response(
                request,
                batch,
                profile,
            )
            callback_result = on_progress(progress_response, batch)
            if inspect.isawaitable(callback_result):
                await callback_result

        if not profile.rules_configured:
            batch = initial_batch or AgentBatchResult(total_count=len(request.images))
            response = self._build_unconfigured_response(request, profile)
            if on_progress:
                callback_result = on_progress(response, batch)
                if inspect.isawaitable(callback_result):
                    await callback_result
            return response, batch

        response, batch = await self.workflow.run(
            request,
            profile,
            handle_agent_progress,
            initial_batch=initial_batch,
        )
        if on_progress:
            callback_result = on_progress(response, batch.model_copy(deep=True))
            if inspect.isawaitable(callback_result):
                await callback_result
        return response, batch

    @with_business_context
    async def _extract_documents(
        self,
        request: ReviewRequest,
        batch_callback: Callable[[AgentBatchResult], Any] | None = None,
        profile: BusinessProfile | None = None,
        initial_report: MaterialCompletenessReport | None = None,
    ) -> AgentBatchResult:
        resolved_profile = profile or self.resolve_profile(request)
        if (
            not request.images
            and resolved_profile.material_policy is None
            and not resolved_profile.required_fields
        ):
            return AgentBatchResult()
        if self.agent:
            return await self.agent.extract_async(
                request.images,
                batch_callback,
                retry_policy=resolved_profile.retry_policy,
                initial_report=initial_report,
                # 材料类型候选限制在本业务声明的集合内：过户页面上的「二手车
                # 发票」不得命中报废置换的机动车销售发票政策。
                allowed_document_types=declared_material_types(
                    resolved_profile.business_type
                ),
            )

        unavailable_image_ids = [
            str(image.image_id or image.index)
            for image in request.images
            if image.collection_error or not image.recognition_source
        ]
        return AgentBatchResult(
            total_count=len(request.images),
            failed_count=len(unavailable_image_ids),
            failed_image_ids=unavailable_image_ids,
            limitations=["DashScope Agent 未配置：缺少 DASHSCOPE_API_KEY"],
        )

    def resolve_profile(self, request: ReviewRequest) -> BusinessProfile:
        validate_request_route(request, self.registry.active_profiles)
        return self.registry.resolve(
            request.business_type,
            request.region,
            request.profile_version,
            page_id=request.page_id,
        )

    @with_business_context
    def _build_response(
        self,
        request: ReviewRequest,
        batch: AgentBatchResult,
        profile: BusinessProfile | None = None,
        *,
        qr_checks: list[QrCheck] | None = None,
        business_checks: list | None = None,
        page_fill_intent: list | None = None,
        defer_advice: bool = False,
    ) -> ReviewResponse:
        resolved_profile = profile or self.resolve_profile(request)
        observations, _ = collect_observations(request, resolved_profile, batch)
        issues = collection_issues(request, resolved_profile)

        response = build_review_response(
            request,
            batch,
            resolved_profile,
            observations,
            issues,
            qr_checks,
            business_checks,
            page_fill_intent,
            defer_advice,
        )
        # 页面动作由注册能力（如 verify_invoice）产出并经主图汇入
        # response.page_actions；本函数不再注入任何字段级动作。
        return response

    @with_business_context
    @with_review_log_context
    async def extract_image_async(
        self,
        request: ReviewRequest,
        image: ImageInput,
    ) -> AgentBatchResult:
        """识别 URL 清单中的一张图片，最终规则汇总阶段复用此结果。"""

        profile = self.resolve_profile(request)
        image_request = request.model_copy(update={"images": [image]})
        return await self._extract_documents(image_request, profile=profile)

    @staticmethod
    def _build_unconfigured_response(
        request: ReviewRequest,
        profile: BusinessProfile,
    ) -> ReviewResponse:
        return build_unconfigured_response(request, profile)

    async def _collect_qr_checks(
        self,
        request: ReviewRequest,
        batch: AgentBatchResult,
        profile: BusinessProfile | None = None,
    ) -> list[QrCheck]:
        resolved_profile = profile or self.resolve_profile(request)
        checks: list[QrCheck] = []
        preferred, fallback = qr_candidates(request, batch, resolved_profile)
        # 普通材料识别仍然只把原图 URL 交给模型；二维码官网核验是唯一允许
        # 额外读取图片的专用例外，且只对候选材料、只读内存一次。
        documents = {document.image_index: document for document in batch.recognized_documents}
        missing: list[QrCheck] = []
        for position, image in enumerate([*preferred, *fallback]):
            if position >= len(preferred) and any(check.domain_valid for check in checks):
                break
            document = documents.get(image.index)
            codes = list(document.qr_codes) if document and not image.collection_error else []
            decoder = "model" if codes else None
            decode_result = None
            # 模型没有产出文档或文档没有 qr_codes，都进入二维码专用兜底。
            # 这样模型整张图超时/解析失败时，官网核验仍有机会由本地解码完成；
            # 材料识别状态会在诊断中保留，不会被伪装成成功。
            if not codes and not image.collection_error:
                decode_result = await self.qr_decoder.decode(image.recognition_source)
                codes = list(decode_result.values)
                decoder = decode_result.decoder if codes else None
            logger.info("review qr decode image_id=%s source=%s codes=%d error_code=%s duration_ms=%d",
                        image.image_id or image.index, decoder or (decode_result.decoder if decode_result else "none"), len(codes),
                        decode_result.error_code if decode_result else ("collection_error" if image.collection_error else "-"),
                        decode_result.elapsed_ms if decode_result else 0)
            if not codes:
                if decode_result is not None:
                    if decode_result.error_code == "decode_empty":
                        detail = "ZXing 未读到二维码；OpenCV 也未读到二维码"
                    elif decode_result.error_code in {"download_timeout", "download_error", "http_status"}:
                        detail = f"二维码图片读取失败（{decode_result.error_code}）"
                    else:
                        detail = f"二维码读取失败（{decode_result.error_code or 'unknown'}）"
                    prefix = "模型未提取到可靠的二维码" if document else "材料识别未完成"
                    message = f"{prefix}；{detail}，请查看原图人工核验"
                else:
                    message = (
                        "材料识别未完成，尚未取得二维码内容，请重试该材料或人工核验"
                        if not document or image.collection_error
                        else "模型未提取到可靠的二维码内容，请查看原图人工核验"
                    )
                missing.append(QrCheck(
                    image_index=image.index,
                    status=FieldStatus.REVIEW_REQUIRED,
                    message=message,
                    decoder=decode_result.decoder if decode_result is not None else "none",
                    decode_error=decode_result.error_code if decode_result is not None else None,
                    decode_elapsed_ms=decode_result.elapsed_ms if decode_result is not None else None,
                ))
                continue
            for code in dict.fromkeys(codes):
                if hasattr(self.qr_web, "verify_with_retry"):
                    verified, attempts = await self.qr_web.verify_with_retry(
                        code,
                        image.index,
                        retry_count=resolved_profile.retry_policy.qr_web_retry_count,
                    )
                    batch.retry_summary.attempts.extend(attempts)
                else:
                    verified = await self.qr_web.verify(code, image.index)
                status = (
                    FieldStatus.MATCH
                    if verified.status == "MATCH"
                    else FieldStatus.REVIEW_REQUIRED
                )
                checks.append(
                    QrCheck(
                        image_index=image.index,
                        raw_value=code,
                        url=verified.url,
                        domain_valid=verified.domain_valid,
                        accessible=verified.accessible,
                        page_fields=verified.page_fields,
                        status=status,
                        message=verified.message or (
                            "二维码网页字段已提取"
                            if verified.page_fields
                            else "二维码网页无法完成核验"
                        ),
                        decoder=decoder,
                        decode_error=decode_result.error_code if decode_result is not None else None,
                        decode_elapsed_ms=decode_result.elapsed_ms if decode_result is not None else None,
                    )
                )
        # 找到二维码后不展示其他候选图的解码空结果；整体失败只保留一项复核。
        return checks or missing[:1]
