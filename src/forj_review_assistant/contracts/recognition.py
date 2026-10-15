"""Qwen 提取结果、批处理状态和 Agent 建议的数据模型。

模型负责校验外部模型响应和 Agent 内部交换数据，不承载确定性审核规则。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from forj_review_assistant.contracts.checks import CheckResult
from forj_review_assistant.contracts.evidence import FieldObservation


class RecognizedDocument(BaseModel):
    target_id: str
    image_index: int | None = None
    document_type: str
    business_scope: str = "unknown"
    covered_pages: list[int] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)
    uncertain_values: dict[str, Any] = Field(default_factory=dict)
    qr_codes: list[str] = Field(default_factory=list)


class MaterialFieldDetail(BaseModel):
    field: str
    field_label: str
    material_name: str
    value: Any = None
    image_id: str | None = None
    image_index: int | None = None


class MaterialCompletenessIssue(BaseModel):
    code: str
    field_details: list[MaterialFieldDetail] = Field(default_factory=list)
    reason_code: Literal[
        "material_missing",
        "image_unreadable",
        "recognition_failed",
        "recognition_uncertain",
        "evidence_not_extracted",
        "page_field_missing",
    ] | None = None
    reason_detail: str | None = None
    material_type: str | None = None
    business_scope: str | None = None
    field: str | None = None
    required_pages: list[int] = Field(default_factory=list)
    present_pages: list[int] = Field(default_factory=list)
    missing_pages: list[int] = Field(default_factory=list)
    missing_sources: list[str] = Field(default_factory=list)
    message: str
    suggested_action: str


class MaterialChecklistItem(BaseModel):
    """One configured material requirement and its observed state."""

    key: str
    display_name: str
    material_type: str
    business_scope: str
    status: Literal["PRESENT", "MISSING", "UNCERTAIN"]
    required_pages: list[int] = Field(default_factory=list)
    present_pages: list[int] = Field(default_factory=list)
    missing_pages: list[int] = Field(default_factory=list)
    image_ids: list[str] = Field(default_factory=list)
    reason: str


class MaterialCompletenessReport(BaseModel):
    phase: Literal["COLLECTED", "EXTRACTED"]
    status: Literal["COMPLETE", "INCOMPLETE", "UNCERTAIN"]
    enforced: bool = False
    issues: list[MaterialCompletenessIssue] = Field(default_factory=list)
    checklist: list[MaterialChecklistItem] = Field(default_factory=list)


# 重试原因码：模型自报"看不清"的字段走定向重读，原因里带上字段名，
# 提示词据此生成"回到这些字段在原图中的位置再读一次"的指令。
RETRYABLE_UNCERTAIN_PREFIX = "uncertain_field:"


class RetryAttempt(BaseModel):
    target_id: str
    stage: Literal["qwen", "qr_web"]
    attempt_number: int = Field(ge=1)
    reason_code: str
    strategy: str
    result: Literal["succeeded", "failed", "skipped"]
    duration_ms: int = Field(ge=0)


class RetrySummary(BaseModel):
    attempts: list[RetryAttempt] = Field(default_factory=list)

    @computed_field
    @property
    def qwen_retries(self) -> int:
        return sum(item.stage == "qwen" and item.attempt_number > 1 for item in self.attempts)

    @computed_field
    @property
    def qr_web_retries(self) -> int:
        return sum(item.stage == "qr_web" and item.attempt_number > 1 for item in self.attempts)

    @computed_field
    @property
    def total_retries(self) -> int:
        return self.qwen_retries + self.qr_web_retries


class EvidenceRegion(BaseModel):
    """单个字段在原始图片中的证据位置。"""

    field: str
    image_index: int | None = None
    box: list[float] = Field(default_factory=list)


class QwenClassification(BaseModel):
    """未知材料的类型分类结果。"""

    document_type: str
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


class QwenExtraction(BaseModel):
    """单张材料图片的结构化字段提取结果。"""

    document_type: str = "unknown"
    fields: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_regions: list[EvidenceRegion] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)
    qr_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_evidence_regions(cls, value: Any) -> Any:
        """兼容 Qwen 可能返回的字段到坐标映射，并过滤无效证据区域。"""
        if not isinstance(value, dict):
            return value
        regions = value.get("evidence_regions")
        if isinstance(regions, dict):
            # 旧格式以字段名为键，值可以是一个坐标框或多个坐标框。
            normalized: list[dict[str, Any]] = []
            for field, boxes in regions.items():
                if isinstance(boxes, list) and boxes and all(isinstance(item, (int, float)) for item in boxes):
                    boxes = [boxes]
                if not isinstance(boxes, list):
                    continue
                for box in boxes:
                    if isinstance(box, list):
                        normalized.append({"field": field, "box": box})
            value = {**value, "evidence_regions": normalized}
        elif isinstance(regions, list):
            # 标准列表格式只保留字段名有效且包含四个坐标值的项目。
            normalized = []
            for region in regions:
                # 直接以 EvidenceRegion 构造时不要丢掉：校验器只认 JSON 形态
                # 会让程序内构造的证据区域静默消失。
                if isinstance(region, EvidenceRegion):
                    normalized.append(region)
                    continue
                if not isinstance(region, dict):
                    continue
                field = region.get("field")
                box = region.get("box")
                if not isinstance(field, str) or not field.strip():
                    continue
                if (
                    not isinstance(box, list)
                    or len(box) != 4
                    or not all(isinstance(item, (int, float)) for item in box)
                ):
                    continue
                normalized.append(region)
            value = {**value, "evidence_regions": normalized}
        elif regions is None:
            value = {**value, "evidence_regions": []}
        return value


class AgentBatchResult(BaseModel):
    """一批图片的渐进式提取结果及完成、失败、超时统计。"""

    observations: list[FieldObservation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidences: list[float] = Field(default_factory=list)
    total_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    timed_out_count: int = 0
    completed_image_ids: list[str] = Field(default_factory=list)
    failed_image_ids: list[str] = Field(default_factory=list)
    timed_out_image_ids: list[str] = Field(default_factory=list)
    recognized_documents: list[RecognizedDocument] = Field(default_factory=list)
    material_completeness: MaterialCompletenessReport | None = None
    retry_summary: RetrySummary = Field(default_factory=RetrySummary)




class AgentAdvice(BaseModel):
    """面向审核人员的安全建议；不允许模型直接给出驳回结论。"""

    decision: Literal["PASS", "REVIEW_REQUIRED"] = "REVIEW_REQUIRED"
    title: str
    summary: str
    findings: list[CheckResult] = Field(default_factory=list)
    recognition_confidence: float | None = Field(default=None, ge=0, le=1)
    # 响应契约迁移期间保留旧 confidence 字段，以兼容尚未升级的扩展版本。
    confidence: float | None = Field(default=None, ge=0, le=1)
    basis: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
