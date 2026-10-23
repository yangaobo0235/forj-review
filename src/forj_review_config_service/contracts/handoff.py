"""Portable task facts. A package digest detects changes; it grants no execution authority."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from forj_review_config_service.contracts.tasks import Requirement, RouteAssessment


def package_digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class HandoffPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    task_id: str
    page_id: str
    intent_revision: int = Field(ge=0)
    intent_digest: str
    candidate_revision: int = Field(ge=0)
    baseline_digest: str
    candidate: dict
    requirements: list[Requirement]
    assessment: RouteAssessment
    gaps: list[dict]
    verification: dict
    digest: str = ""

    def verified(self):
        if self.digest != package_digest(self.model_dump(exclude={"digest"})):
            raise ValueError("任务包摘要不匹配")
        if self.assessment["intent_revision"] != self.intent_revision:
            raise ValueError("任务包需求版本与路线评估不一致")
        return self
