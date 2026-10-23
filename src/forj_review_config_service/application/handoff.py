"""Export only task/configuration facts, never captures, model payloads or material URLs."""

from forj_review_config_service.contracts.handoff import HandoffPackage, package_digest
from forj_review_config_service.contracts.models import ConfigurationError
from forj_review_config_service.contracts.tasks import active_requirements, intent_digest


def export_handoff(record):
    assessment = record.route_assessment
    if (
        record.route != "LOCAL_CODE"
        or not assessment
        or assessment["intent_revision"] != record.intent_revision
        or assessment["revision"] != record.revision
        or assessment["baseline_digest"] != record.baseline_digest
    ):
        raise ConfigurationError(
            "ROUTE_ASSESSMENT_REQUIRED", "请先完成配置与能力调查并记录本地代码路线"
        )
    package = HandoffPackage(
        task_id=record.task_id,
        page_id=record.draft["page_id"],
        intent_revision=record.intent_revision,
        intent_digest=intent_digest(record),
        candidate_revision=record.revision,
        baseline_digest=record.baseline_digest,
        candidate=record.draft,
        requirements=active_requirements(record),
        assessment=assessment,
        gaps=[g.model_dump() for g in record.capability_gaps],
        verification={
            "status": "REQUIRES_LOCAL_VERIFICATION",
            "configuration_publishable": False,
            "acceptance": [a for r in active_requirements(record) for a in r.get("acceptance", [])],
        },
    )
    package.digest = package_digest(package.model_dump(exclude={"digest"}))
    return package
