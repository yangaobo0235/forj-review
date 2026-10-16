"""审核领域与 API 模型。

主要职责：定义请求、响应、证据、比较和任务状态契约。
修改日期：2026-08-26
"""

from enum import StrEnum
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    model_validator,
)

from forj_review_assistant.contracts.checks import CheckResultValue
from forj_review_assistant.contracts.decisions import ReasonCode
from forj_review_assistant.contracts.evidence import (  # noqa: F401 - public export
    EvidenceFact,
    FieldObservation,
)
from forj_review_assistant.contracts.recognition import (
    AgentAdvice,
    CheckResult,
    MaterialCompletenessReport,
    RetrySummary,
)


class FieldStatus(StrEnum):
    MATCH = "MATCH"
    CONFLICT = "CONFLICT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class Recommendation(StrEnum):
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class JobStatus(StrEnum):
    RUNNING = "RUNNING"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ReviewJobPhase(StrEnum):
    PREPARING = "PREPARING"
    RECOGNIZING = "RECOGNIZING"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BusinessType(StrEnum):
    SCRAP_REPLACEMENT = "scrap_replacement"
    VEHICLE_SOURCE = "vehicle_source"
    TRANSFER = "transfer"
    CONSISTENCY = "consistency"


class Region(StrEnum):
    DEFAULT = "default"
    QINGDAO = "qingdao"
    CHANGCHUN = "changchun"


# Known identifiers retain their enum instances for existing integrations; new
# identifiers are validated strings and are resolved against the loaded registry.
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,79}$")]


def business_identifier(value: object) -> object:
    try:
        return BusinessType(value)
    except (ValueError, TypeError):
        return value


def region_identifier(value: object) -> object:
    try:
        return Region(value)
    except (ValueError, TypeError):
        return value


BusinessId = Annotated[BusinessType | Identifier, BeforeValidator(business_identifier)]
RegionId = Annotated[Region | Identifier, BeforeValidator(region_identifier)]


class SelectionMode(StrEnum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


class ReviewDisplayTarget(StrEnum):
    PAGE_FIELD = "PAGE_FIELD"
    ASSISTANT = "ASSISTANT"


class ImageInput(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    index: int = Field(ge=0)
    src: str
    group: str = "未分类"
    image_id: str | None = Field(
        default=None, validation_alias=AliasChoices("image_id", "imageId")
    )
    category_hint: str = Field(
        default="unknown",
        validation_alias=AliasChoices("category_hint", "categoryHint"),
    )
    business_scope: str = Field(
        default="unknown",
        validation_alias=AliasChoices("business_scope", "businessScope"),
    )
    group_title: str = Field(
        default="未分类资料",
        validation_alias=AliasChoices("group_title", "groupTitle"),
    )
    group_order: int | None = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices("group_order", "groupOrder"),
    )
    document_type_hint: str = Field(
        default="unknown",
        validation_alias=AliasChoices("document_type_hint", "documentTypeHint"),
    )
    collection_error: str | None = Field(
        default=None,
        validation_alias=AliasChoices("collection_error", "collectionError"),
    )
    alt: str = ""
    page_position: str | None = Field(
        default=None,
        validation_alias=AliasChoices("page_position", "pagePosition"),
    )

    @property
    def recognition_source(self) -> str | None:
        """模型可读取的 HTTP(S) 图片地址。"""
        try:
            parsed = urlparse(self.src)
            _ = parsed.port
        except ValueError:
            return None
        if (parsed.scheme in {"http", "https"} and parsed.hostname
                and parsed.username is None and parsed.password is None):
            return self.src
        return None

    @model_validator(mode="before")
    @classmethod
    def derive_legacy_business_scope(cls, value: Any) -> Any:
        if (
            not isinstance(value, dict)
            or "business_scope" in value
            or "businessScope" in value
        ):
            return value
        category = value.get("category_hint", value.get("categoryHint", "unknown"))
        # **这份名单和 `routing.scope_hint_types` 不是同一个集合，不要合并。**
        # 那边回答的是「哪些材料属于旧车区」，用于决定二维码扫哪些原图 URL；
        # 这里回答的是「只看这个分类名，该把图片划到哪个
        # 分区」——是在 `business_scope` 缺失时猜一个分区。行驶证和登记证书两个
        # 分区都有，拿它们猜分区本身就是不成立的，所以这里既不列 `vehicle_license`
        # 也不该按材料声明推导：改了会让旧客户端提交的图片落到另一个分区，
        # 而这是一条纯兼容路径。
        old_types = {"scrap_certificate", "old_vehicle", "registration_certificate"}
        new_types = {"new_vehicle", "invoice"}
        scope = (
            "old_vehicle"
            if category in old_types
            else "new_vehicle"
            if category in new_types
            else "unknown"
        )
        return {**value, "business_scope": scope}




class FieldComparison(BaseModel):
    field: str
    left_value: Any = None
    right_value: Any = None
    status: FieldStatus
    source: str = "rule_engine"
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[EvidenceFact] = Field(default_factory=list)
    message: str = ""
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    differences: list[int] = Field(default_factory=list)


class QrCheck(BaseModel):
    image_index: int | None = None
    raw_value: str | None = None
    url: str | None = None
    domain_valid: bool | None = None
    accessible: bool | None = None
    page_fields: dict[str, Any] = Field(default_factory=dict)
    status: FieldStatus = FieldStatus.REVIEW_REQUIRED
    message: str = ""
    # 二维码内容的来源和本地解码诊断。保留为可选字段，兼容历史审核结果。
    decoder: Literal["model", "zxing", "opencv", "none"] | None = None
    decode_error: str | None = None
    decode_elapsed_ms: int | None = Field(default=None, ge=0)


class PageFillAction(BaseModel):
    field: str
    target_label: str
    owner_type: str


class PageActionIntent(BaseModel):
    """Backend-proposed reversible page action; execution stays in the adapter."""

    action_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    requires_authorization: bool = True


class CapabilityResult(BaseModel):
    capability_id: str
    status: Literal["READY", "SKIPPED", "BLOCKED", "NOT_CONFIGURED", "SUCCEEDED", "FAILED"]
    checks: list[CheckResult] = Field(default_factory=list)
    evidence: list[EvidenceFact] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    page_actions: list[PageFillAction] = Field(default_factory=list)
    page_action_intents: list[PageActionIntent] = Field(default_factory=list)
    qr_checks: list[QrCheck] = Field(default_factory=list)
    display_items: list[str] = Field(default_factory=list)
    error_code: str | None = None
    attempts: int = 0


class CapabilityPlanEntry(BaseModel):
    """Serializable execution plan entry exposed by ReviewResponse."""

    capability_id: str
    status: Literal["READY", "SKIPPED", "BLOCKED", "NOT_CONFIGURED"]
    stage: str
    reason: str = ""
    dependencies: list[str] = Field(default_factory=list)
    missing_dependencies: list[str] = Field(default_factory=list)


class ReviewTask(BaseModel):
    """一个已经实际执行、可按顺序展示的审核步骤。"""

    step_id: str
    visible: bool = True
    check_ids: list[str] = Field(default_factory=list)
    sequence: int = Field(ge=1)
    # MATERIAL 仅兼容旧响应；当前展示路由不再生成该类任务。
    category: Literal["FIELD", "EXTERNAL", "BUSINESS_RULE", "MATERIAL"]
    display_target: ReviewDisplayTarget
    page_field: str | None = None
    page_target_field: str | None = None
    page_target_fields: list[str] = Field(default_factory=list)
    page_value: Any = None
    page_values: list[CheckResultValue] = Field(default_factory=list)
    control_type: str | None = None
    writable: bool = False
    requires_reviewer_action: bool
    label: str
    result_status: Literal["MATCH", "CONFLICT", "INSUFFICIENT"]
    reason: str
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    values: list[CheckResultValue] = Field(default_factory=list)
    evidence: list[EvidenceFact] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    evidence_count: int = 0
    evidence_mode: str | None = None
    evidence_sources: list[str] = Field(default_factory=list)
    not_found_sources: list[str] = Field(default_factory=list)
    normalized_page_value: str | None = None

    @model_validator(mode="after")
    def validate_display_contract(self) -> "ReviewTask":
        if self.display_target is ReviewDisplayTarget.PAGE_FIELD and not self.page_field:
            raise ValueError("PAGE_FIELD review steps require page_field")
        # Assistant tasks use page_target_field for controlled DOM location and
        # write-back. ``page_field`` remains reserved for PAGE_FIELD display.
        if self.display_target is ReviewDisplayTarget.ASSISTANT and self.page_field:
            raise ValueError("ASSISTANT review steps must not bind page_field; use page_target_field")
        if self.result_status == "MATCH" and self.requires_reviewer_action:
            raise ValueError("MATCH review steps cannot require reviewer action")
        if self.result_status != "MATCH" and not self.requires_reviewer_action:
            raise ValueError("non-MATCH review steps must require reviewer action")
        return self


# Backward-compatible import for clients that still use the pre-v2 name.
# The object is the same validated contract; new code should use ReviewTask.
ReviewStep = ReviewTask



class ReviewFieldSnapshot(BaseModel):
    """One editable logical form control collected from the current page DOM."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    field: str | None = None
    label: str
    value: Any = None
    control_type: str = Field(
        default="text", validation_alias=AliasChoices("control_type", "controlType")
    )
    editable: bool = True
    order: int = Field(default=1, ge=1)
    section: str = "unknown"
    operation_only: bool = Field(
        default=False,
        validation_alias=AliasChoices("operation_only", "operationOnly"),
    )


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    page_id: str | None = Field(default=None, max_length=160, validation_alias=AliasChoices("page_id", "pageId"))

    page_url: HttpUrl | str = Field(
        validation_alias=AliasChoices("page_url", "pageUrl")
    )
    business_type: BusinessId = Field(
        default=BusinessType.SCRAP_REPLACEMENT,
        validation_alias=AliasChoices("business_type", "businessType"),
    )
    region: RegionId = Region.DEFAULT
    profile_version: str = Field(
        default="1.0",
        validation_alias=AliasChoices("profile_version", "profileVersion"),
    )
    selection_mode: SelectionMode = Field(
        default=SelectionMode.AUTO,
        validation_alias=AliasChoices("selection_mode", "selectionMode"),
    )
    workflow_stage: str = Field(
        default="scrap_replacement",
        validation_alias=AliasChoices("workflow_stage", "workflowStage"),
    )
    application_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("application_id", "applicationId"),
    )
    page_title: str = Field(
        default="",
        validation_alias=AliasChoices("page_title", "pageTitle"),
    )
    page_fields: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("page_fields", "pageFields"),
    )
    review_fields: list[ReviewFieldSnapshot] = Field(
        default_factory=list,
        validation_alias=AliasChoices("review_fields", "reviewFields"),
    )
    images: list[ImageInput] = Field(default_factory=list)
    collection_diagnostics: "CollectionDiagnostics" = Field(
        default_factory=lambda: CollectionDiagnostics(),
        validation_alias=AliasChoices(
            "collection_diagnostics", "collectionDiagnostics"
        ),
    )
    trace_id: str | None = Field(default=None, validation_alias=AliasChoices("trace_id", "traceId"))

    def validate_images(self) -> None:
        """同步审核与后台任务共用的输入约束，也检查构造后被修改的请求。"""
        if len(self.images) > 16:
            raise ValueError("单次审核最多允许 16 张图片")
        image_ids = [str(image.image_id or image.index) for image in self.images]
        if len(set(image_ids)) != len(image_ids):
            raise ValueError("图片清单包含重复标识")
        if len({image.index for image in self.images}) != len(self.images):
            raise ValueError("图片清单包含重复序号")
        for image in self.images:
            if not image.collection_error and not image.recognition_source:
                raise ValueError("图片必须提供可由模型访问的 HTTP(S) 地址")


class CollectionDiagnostics(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    scanned_controls: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("scanned_controls", "scannedControls"),
    )
    matched_fields: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("matched_fields", "matchedFields"),
    )
    review_field_count: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("review_field_count", "reviewFieldCount"),
    )
    unmatched_labels: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("unmatched_labels", "unmatchedLabels"),
    )
    candidate_count: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("candidate_count", "candidateCount"),
    )
    ambiguous_fields: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("ambiguous_fields", "ambiguousFields"),
    )
    image_success_count: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("image_success_count", "imageSuccessCount"),
    )
    image_failure_count: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("image_failure_count", "imageFailureCount"),
    )
    scanned_images: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("scanned_images", "scannedImages"),
    )
    selected_images: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("selected_images", "selectedImages"),
    )
    image_overflow: bool = Field(
        default=False, validation_alias=AliasChoices("image_overflow", "imageOverflow")
    )
    collection_issues: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("collection_issues", "collectionIssues"),
    )


class ResultSection(BaseModel):
    id: str
    title: str
    fields: list[str] = Field(default_factory=list)


class ReviewResponse(BaseModel):
    capability_plan: list[CapabilityPlanEntry] = Field(default_factory=list)
    capability_results: list[CapabilityResult] = Field(default_factory=list)
    evidence_facts: list[EvidenceFact] = Field(default_factory=list)
    protocol_version: Literal["2.0"] = "2.0"
    trace_id: str = ""
    presentation: Literal["FIELD_WORKBENCH", "MANUAL_REVIEW"] = "MANUAL_REVIEW"
    business_type: BusinessId = BusinessType.SCRAP_REPLACEMENT
    region: RegionId = Region.DEFAULT
    profile_version: str = "1.0"
    recommendation: Recommendation
    risk_level: str
    summary: str
    comparisons: list[FieldComparison] = Field(default_factory=list)
    qr_checks: list[QrCheck] = Field(default_factory=list)
    cross_checks: list[CheckResult] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    sections: list[ResultSection] = Field(default_factory=list)
    context_summary: dict[str, Any] = Field(default_factory=dict)
    agent_advice: AgentAdvice | None = None
    material_completeness: MaterialCompletenessReport | None = None
    retry_summary: RetrySummary | None = None
    page_fill_intent: list[PageFillAction] = Field(default_factory=list)
    page_actions: list[PageActionIntent] = Field(default_factory=list)
    review_tasks: list[ReviewTask] = Field(default_factory=list)


class ReviewProgress(BaseModel):
    total_count: int = 0
    submitted_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    timed_out_count: int = 0


class ReviewGroupProgress(ReviewProgress):
    status: JobStatus = JobStatus.RUNNING


class ReviewJobCreated(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.RUNNING
    created_at: str


class ReviewJobSnapshot(BaseModel):
    job_id: str
    status: JobStatus
    created_at: str
    progress: ReviewProgress
    failed_image_ids: list[str] = Field(default_factory=list)
    timed_out_image_ids: list[str] = Field(default_factory=list)
    groups: dict[str, ReviewGroupProgress] = Field(default_factory=dict)
    result: ReviewResponse | None = None
    message: str | None = None
    material_completeness: MaterialCompletenessReport | None = None
    retry_summary: RetrySummary | None = None
    phase: ReviewJobPhase = ReviewJobPhase.RECOGNIZING
