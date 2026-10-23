"""Durable task data, independent of a model's conversation protocol."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from typing_extensions import NotRequired, TypedDict

if TYPE_CHECKING:
    from forj_review_config_service.contracts.models import TaskRecord

Route = Literal["UNDECIDED", "CONFIG", "LOCAL_CODE", "HUMAN_HANDOFF"]


class Requirement(TypedDict):
    requirement_id: str
    text: str
    status: NotRequired[Literal["PENDING", "ACTIVE", "SUPERSEDED", "WITHDRAWN", "QUERY"]]
    acceptance: NotRequired[list[str]]
    assertions: NotRequired[list[dict]]
    source_event_id: NotRequired[str | None]
    last_changed_event_id: NotRequired[str | None]
    requirement_revision: NotRequired[int]
    target_refs: NotRequired[list[str]]
    depends_on: NotRequired[list[str]]
    replaces: NotRequired[list[str]]
    route_assessment: NotRequired[dict | None]


class RouteAssessment(TypedDict):
    requirement_ids: NotRequired[list[str]]
    operations: NotRequired[list[dict]]
    route: Route
    reason: str
    evidence_refs: list[str]
    intent_revision: int
    revision: int
    baseline_digest: str
    capability_digest: str


def active_requirements(record: TaskRecord) -> list[Requirement]:
    return [r for r in record.requirements if r.get("status", "ACTIVE") in {"ACTIVE", "PENDING"}]


def intent_digest(record: TaskRecord) -> str:
    value = {"revision": record.intent_revision, "requirements": active_requirements(record)}
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
