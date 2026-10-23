"""Reviewable subset of a developer's local continuation report."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ReportPart(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class ReportTask(ReportPart):
    task_id: str
    digest: str
    intent_digest: str


class LocalDelivery(ReportPart):
    status: str = ""
    summary: str = ""
    obstacles: list[str] = Field(default_factory=list, max_length=100)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)


class LocalEvidence(ReportPart):
    id: str = ""
    tool: str = ""
    result: dict[str, JsonValue]


class LocalReport(ReportPart):
    task: ReportTask
    status: Literal[
        "LOCAL_CHECKS_PASSED",
        "UNVERIFIED_PATCH",
        "WAITING_ENVIRONMENT",
        "BUDGET_EXHAUSTED",
        "INTERRUPTED",
        "NEEDS_INPUT",
        "HUMAN_HANDOFF",
        "RECONCILIATION_REQUIRED",
    ]
    delivery: LocalDelivery | None = None
    evidence: list[LocalEvidence] = Field(default_factory=list, max_length=1000)
    changed_files: list[str] = Field(default_factory=list, max_length=1000)
    patch_digest: str = ""
    verification_scope: str = "登记的本地检查；不包含生产部署"
    notice: str = "本地报告需由开发人员评审，不构成配置发布或部署证明。"
    source_head: str = ""
