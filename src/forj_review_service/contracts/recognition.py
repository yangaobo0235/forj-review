"""Qwen 提取结果、批处理状态和审核建议的数据模型。

模型负责校验外部模型响应和 识别流程内部交换数据，不承载确定性审核规则。
"""

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from forj_review_service.contracts.checks import CheckResult
from forj_review_service.contracts.evidence import FieldObservation


def valid_normalized_box(box: Any) -> bool:
    """Whether a location can safely identify a region of the original image."""
    return (
        isinstance(box, list)
        and len(box) == 4
        and all(
            isinstance(n, (int, float))
            and not isinstance(n, bool)
            and math.isfinite(n)
            and 0 <= n <= 1000
            for n in box
        )
        and box[0] < box[2]
        and box[1] < box[3]
    )


class MaterialFact(BaseModel):
    """Single model reading, projected into raw facts and configured standard fields."""

    field: str | None = Field(default=None, min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)
    section: str = Field(default="", max_length=200)
    value: Any
    uncertain: bool = False
    box: Any = None


class MaterialReading(BaseModel):
    """One visible labelled value; it is not an approved comparison source."""

    label: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=4000)
    section: str = Field(default="", max_length=200)
    box: list[float] = Field(default_factory=list, max_length=4)
    uncertain: bool = False


class RecognizedDocument(BaseModel):
    target_id: str
    image_index: int | None = None
    document_type: str
    business_scope: str = "unknown"
    covered_pages: list[int] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)
    uncertain_values: dict[str, Any] = Field(default_factory=dict)
    # At most two bounded readings can be retained after a semantic retry.
    raw_fields: list[MaterialReading] = Field(default_factory=list, max_length=600)
    # 兼容历史识别结果；官网核验只消费专用解码器结果。
    qr_codes: list[str] = Field(default_factory=list)


class DocumentClassification(BaseModel):
    """已确认的材料类型，独立于后续字段提取的成败。"""

    target_id: str
    document_type: str
    business_scope: str = "unknown"
    identity_sides: list[Literal["FRONT", "BACK"]] = Field(default_factory=list)


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
    reason_code: (
        Literal[
            "material_missing",
            "image_unreadable",
            "recognition_failed",
            "recognition_uncertain",
            "evidence_not_extracted",
            "page_field_missing",
        ]
        | None
    ) = None
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
    presence: "MaterialPresenceReport | None" = None


class MaterialPresenceGroupResult(BaseModel):
    key: str
    display_name: str
    status: Literal["PRESENT", "MISSING", "UNCERTAIN"]
    alternatives: list[list[MaterialChecklistItem]] = Field(default_factory=list)


class MaterialPresenceReport(BaseModel):
    status: Literal["COMPLETE", "INCOMPLETE", "UNCERTAIN"]
    groups: list[MaterialPresenceGroupResult] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


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


class QwenExtraction(BaseModel):
    """单张材料图片的结构化字段提取结果。"""

    document_type: str = "unknown"
    fields: dict[str, Any] = Field(default_factory=dict)
    raw_fields: list[MaterialReading] = Field(default_factory=list, max_length=300)
    confidence: float | None = Field(default=None, ge=0, le=1)
    type_confidence: float | None = Field(default=None, ge=0, le=1)
    identity_sides: list[Literal["FRONT", "BACK"]] = Field(default_factory=list)
    field_alternatives: dict[str, list[Any]] = Field(default_factory=dict)
    field_confidences: dict[str, float | None] = Field(default_factory=dict)
    location_warnings: list[str] = Field(default_factory=list)
    evidence_regions: list[EvidenceRegion] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)
    # 兼容旧模型响应；当前提示词要求返回空数组。
    qr_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def isolate_raw_location_errors(cls, value: Any) -> Any:
        """Retain valid raw text and explicitly report unusable optional coordinates."""
        if not isinstance(value, dict) or not isinstance(value.get("raw_fields"), list):
            return value
        rows = []
        warnings = list(value.get("location_warnings", []))
        for index, row in enumerate(value["raw_fields"]):
            if isinstance(row, dict) and row.get("box"):
                if not valid_normalized_box(row["box"]):
                    row = {**row, "box": []}
                    warnings.append(f"raw_fields.{index}.box")
            elif isinstance(row, dict) and row.get("box") is None:
                row = {**row, "box": []}
            rows.append(row)
        return {**value, "raw_fields": rows, "location_warnings": list(dict.fromkeys(warnings))}

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
                if (
                    isinstance(boxes, list)
                    and boxes
                    and all(isinstance(item, (int, float)) for item in boxes)
                ):
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


class RecognitionBatchResult(BaseModel):
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
    document_classifications: list[DocumentClassification] = Field(default_factory=list)
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
