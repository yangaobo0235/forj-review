"""确定性检查结果，与材料事实和人工任务分离。"""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from forj_review_service.contracts.decisions import ReasonCode, default_reason_codes
from forj_review_service.contracts.evidence import DifferenceRange, EvidenceFact


class CheckResultValue(BaseModel):
    """确定性检查中参与比较的单个来源值。"""

    source: str
    # 仅供工作台区分可执行的系统结论和材料原始证据；不改变业务比对结果。
    fill_role: Literal["RECOMMENDATION", "EVIDENCE"] | None = None
    differences: list[DifferenceRange] = Field(default_factory=list)
    # 该值与页面侧取值是否不一致。None 表示"没有比对"（页面侧取不到值，
    # 或这条值本身就是页面侧），工作台据此逐条标注，审核员不必回头读理由
    # 才知道哪一个候选对不上。
    conflicting: bool | None = None
    comparison_status: Literal["MATCH", "CONFLICT", "INSUFFICIENT"] | None = None
    # 产生这条值的检查写下的比对说明（“页面车型马力 430 与材料推导值 473、
    # 480 不一致”）。字段装配在投影时填入，工作台把它放进对应的候选框里，
    # 一条检查下的多条候选共用一个说明。
    check_reason: str | None = None
    value: Any = None
    source_id: str | None = None
    image_id: str | None = None
    image_index: int | None = None
    document_type: str | None = None
    detail: str | None = None
    derived_from: str | None = None
    evidence_region: list[float] | None = None
    uncertain: bool = False
    group_title: str | None = None


class CheckResult(BaseModel):
    """一项确定性审核检查的状态、原因和证据。"""

    check_id: str
    label: str
    status: Literal["MATCH", "CONFLICT", "INSUFFICIENT"]
    reason: str
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    values: list[CheckResultValue] = Field(default_factory=list)
    evidence: list[EvidenceFact] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def explain_status(self):
        if not self.reason_codes:
            self.reason_codes = default_reason_codes(self.status)
        return self

