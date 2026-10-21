"""审核服务编排门面。

主要职责：协调业务配置、工作流和辅助工具，不内嵌响应策略。
修改日期：2026-08-26
"""

import asyncio
import inspect
import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.context_validation import validate_request_route
from forj_review_service.businesses.material_presence import evaluate_presence
from forj_review_service.businesses.profiles import BusinessProfile
from forj_review_service.businesses.registry import (
    BusinessRegistry,
)
from forj_review_service.businesses.rules.scrap_certificate_qr import qr_candidates
from forj_review_service.capabilities.page_actions import PageActionHandler
from forj_review_service.capabilities.specs import (
    BusinessRuleHandler,
    ExternalCheckHandler,
)
from forj_review_service.config.event_loop import new_event_loop
from forj_review_service.config.settings import image_private_hosts, load_qwen_config
from forj_review_service.contracts.recognition import (
    MaterialCompletenessReport,
    RecognitionBatchResult,
)
from forj_review_service.contracts.review import (
    ImageInput,
    QrCheck,
    ReviewRequest,
    ReviewResponse,
)
from forj_review_service.evidence.collection import collect_observations, collection_issues
from forj_review_service.integrations.qr.decoder import QrImageDecoder
from forj_review_service.integrations.qr.web_verifier import QrWebVerifier
from forj_review_service.integrations.qr.workflow import QrVerificationWorkflow
from forj_review_service.integrations.recognition.client import QwenClient
from forj_review_service.integrations.recognition.extraction import (
    RecognitionService,
    declared_material_types,
)
from forj_review_service.observability.context import with_review_log_context
from forj_review_service.presentation.response import (
    build_review_response,
    build_unconfigured_response,
)
from forj_review_service.runtime.workflow.graph import ReviewWorkflow

ReviewProgressCallback = Callable[[ReviewResponse, RecognitionBatchResult], Any]
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
        database_settings=None,
    ) -> None:
        self.database_settings = database_settings
        self._databases = {}
        self._database_lock = threading.Lock()
        config = load_qwen_config() if load_model else None
        self.qr_web = QrWebVerifier(allowed_hosts=["qclt.mofcom.gov.cn"])
        self.qr_decoder = qr_decoder or QrImageDecoder(private_hosts=image_private_hosts())
        self.qr_workflow = QrVerificationWorkflow()
        self.registry = registry if registry is not None else BusinessRegistry(())
        self._handler_overrides = dict(external_check_handlers=external_check_handlers,
                                       business_rule_handlers=business_rule_handlers,
                                       page_action_handlers=page_action_handlers)
        self.recognition = RecognitionService(QwenClient(config)) if config and config.available else None
        self.workflow = ReviewWorkflow(
            self,
            external_check_handlers=external_check_handlers,
            business_rule_handlers=business_rule_handlers,
            page_action_handlers=page_action_handlers,
        )

    async def lookup_engine_emissions(self, models):
        from forj_review_service.contracts.engine_emissions import EngineEmissionLookupUnavailable
        from forj_review_service.integrations.database import AsyncDatabase, review_database
        from forj_review_service.integrations.engine_emissions import lookup_engine_emissions_async

        try:
            loop = asyncio.get_running_loop()
            with self._database_lock:
                database = self._databases.get(loop)
                if database is None:
                    database = AsyncDatabase(self.database_settings or review_database())
                    self._databases[loop] = database
            return await lookup_engine_emissions_async(models, database=database)
        except ValueError:
            raise EngineEmissionLookupUnavailable("发动机排放对应表读取失败，请检查数据库配置") from None

    async def aclose(self) -> None:
        """释放当前事件循环的模型连接池，须在该循环停止前调用。"""
        try:
            if self.recognition is not None:
                await self.recognition.client.aclose()
        finally:
            with self._database_lock:
                database = self._databases.pop(asyncio.get_running_loop(), None)
            if database is not None:
                await database.engine.dispose()

    def assist(
        self, request: ReviewRequest, *, business: BusinessContext = DEFAULT_BUSINESS
    ) -> ReviewResponse:
        async def run():
            try:
                return await self.assist_async(request, business=business)
            finally:
                await self.aclose()

        # 同步入口拥有临时循环，也必须在 asyncio.run 关闭它之前释放连接。
        with asyncio.Runner(loop_factory=new_event_loop) as runner:
            return runner.run(run())

    async def assist_async(
        self,
        request: ReviewRequest,
        on_progress: ReviewProgressCallback | None = None,
        *,
        business: BusinessContext = DEFAULT_BUSINESS,
    ) -> ReviewResponse:
        """解析业务配置并执行审核；未配置规则时安全降级。"""

        response, _ = await self.assist_with_batch_async(
            request, on_progress=on_progress, business=business
        )
        return response

    @with_review_log_context
    async def assist_with_batch_async(
        self,
        request: ReviewRequest,
        on_progress: ReviewProgressCallback | None = None,
        initial_batch: RecognitionBatchResult | None = None,
        *,
        business: BusinessContext = DEFAULT_BUSINESS,
    ) -> tuple[ReviewResponse, RecognitionBatchResult]:
        """执行审核并复用 URL 任务已完成的图片提取结果。"""

        profile = self.resolve_profile(request, business=business)

        async def handle_recognition_progress(batch: RecognitionBatchResult) -> None:
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
            batch = initial_batch or RecognitionBatchResult(total_count=len(request.images))
            response = self._build_unconfigured_response(request, profile)
            if on_progress:
                callback_result = on_progress(response, batch)
                if inspect.isawaitable(callback_result):
                    await callback_result
            return response, batch

        response, batch = await self.workflow.run(
            request,
            profile,
            handle_recognition_progress,
            initial_batch=initial_batch,
        )
        if on_progress:
            callback_result = on_progress(response, batch.model_copy(deep=True))
            if inspect.isawaitable(callback_result):
                await callback_result
        return response, batch

    async def _extract_documents(
        self,
        request: ReviewRequest,
        batch_callback: Callable[[RecognitionBatchResult], Any] | None = None,
        profile: BusinessProfile | None = None,
        initial_report: MaterialCompletenessReport | None = None,
    ) -> RecognitionBatchResult:
        business = (
            BusinessContext.from_profile(profile) if profile is not None else DEFAULT_BUSINESS
        )
        resolved_profile = profile or self.resolve_profile(request, business=business)
        business = BusinessContext.from_profile(resolved_profile)
        if not resolved_profile.rules_configured:
            return RecognitionBatchResult(total_count=len(request.images))
        if (
            not request.images
            and resolved_profile.material_policy is None
            and not resolved_profile.required_fields
        ):
            return RecognitionBatchResult()
        if self.recognition:

            async def report_progress(batch: RecognitionBatchResult) -> None:
                if batch.material_completeness is not None:
                    batch.material_completeness.presence = evaluate_presence(
                        request,
                        resolved_profile.material_policy,
                        batch,
                    )
                if batch_callback is not None:
                    progress_result = batch_callback(batch)
                    if inspect.isawaitable(progress_result):
                        await progress_result

            return await self.recognition.extract_async(
                request.images,
                report_progress,
                retry_policy=resolved_profile.retry_policy,
                initial_report=initial_report,
                # 材料类型候选限制在本业务声明的集合内。
                allowed_document_types=declared_material_types(business),
                business=business,
            )

        unavailable_image_ids = [
            str(image.image_id or image.index)
            for image in request.images
            if image.collection_error or not image.recognition_source
        ]
        return RecognitionBatchResult(
            total_count=len(request.images),
            failed_count=len(unavailable_image_ids),
            failed_image_ids=unavailable_image_ids,
            limitations=["DashScope 模型服务未配置：缺少 DASHSCOPE_API_KEY"],
        )

    def resolve_profile(
        self, request: ReviewRequest, *, business: BusinessContext = DEFAULT_BUSINESS
    ) -> BusinessProfile:
        validate_request_route(request, self.registry.active_profiles, business=business)
        return self.registry.resolve(
            request.business_type,
            request.region,
            request.profile_version,
            page_id=request.page_id,
        )

    def _build_response(
        self,
        request: ReviewRequest,
        batch: RecognitionBatchResult,
        profile: BusinessProfile | None = None,
        *,
        qr_checks: list[QrCheck] | None = None,
        business_checks: list | None = None,
        defer_advice: bool = False,
    ) -> ReviewResponse:
        business = (
            BusinessContext.from_profile(profile) if profile is not None else DEFAULT_BUSINESS
        )
        resolved_profile = profile or self.resolve_profile(request, business=business)
        business = BusinessContext.from_profile(resolved_profile)
        if not resolved_profile.rules_configured:
            return self._build_unconfigured_response(request, resolved_profile)
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
            defer_advice,
        )
        # 页面动作由注册能力（如 verify_invoice）产出并经主图汇入
        # response.page_actions；本函数不再注入任何字段级动作。
        return response

    @with_review_log_context
    async def extract_image_async(
        self,
        request: ReviewRequest,
        image: ImageInput,
        *,
        business: BusinessContext = DEFAULT_BUSINESS,
    ) -> RecognitionBatchResult:
        """识别 URL 清单中的一张图片，最终规则汇总阶段复用此结果。"""

        profile = self.resolve_profile(request, business=business)
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
        batch: RecognitionBatchResult,
        profile: BusinessProfile | None = None,
    ) -> list[QrCheck]:
        resolved_profile = profile or self.resolve_profile(request)
        preferred, fallback = qr_candidates(request, batch, resolved_profile)
        return await self.qr_workflow.run(
            batch,
            preferred,
            fallback,
            resolved_profile.retry_policy.qr_web_retry_count,
            decoder=self.qr_decoder,
            verifier=self.qr_web,
        )
