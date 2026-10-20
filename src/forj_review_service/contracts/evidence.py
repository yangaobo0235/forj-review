"""原始来源事实；不保存规则结论或人工处置状态。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class DifferenceRange(BaseModel):
    kind: Literal["REPLACE", "EXTRA", "MISSING"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    page_start: int = Field(ge=0)
    page_end: int = Field(ge=0)
    page_text: str


class EvidenceFact(BaseModel):
    source: str
    source_field: str | None = None
    source_id: str | None = None
    field: str | None = None
    uncertain: bool = False
    comparison_status: Literal["MATCH", "CONFLICT", "INSUFFICIENT"] | None = None
    image_index: int | None = None
    detail: str | None = None
    image_id: str | None = None
    business_scope: str | None = None
    group_title: str | None = None
    group_order: int | None = None
    document_type: str | None = None
    value: Any = None
    normalized_value: str | None = None
    derived_from: str | None = None
    evidence_region: list[float] | None = None
    conflicting: bool = False
    differences: list[DifferenceRange] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    page_number: int | None = Field(default=None, ge=1)

    def __getitem__(self, key: str):
        """Temporary mapping-style compatibility for legacy rule consumers."""
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        """Read a field using the legacy mapping convention at the boundary."""
        return getattr(self, key, default)


class FieldObservation(BaseModel):
    """One independently observed value and its review-document source."""

    field: str
    source_field: str | None = None
    source_type: str
    source_id: str
    value: Any = None
    derived_from: str | None = None
    evidence_region: list[float] | None = None
    uncertain: bool = False
    # 该证据所属的图片已经过至少一次定向重读，结果仍然不确定。
    retried: bool = False
    document_type: str | None = None
    image_index: int | None = None
    image_id: str | None = None
    business_scope: str | None = None
    group_title: str | None = None
    group_order: int | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

