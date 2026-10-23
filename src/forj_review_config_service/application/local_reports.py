"""Import developer receipts as reported facts, never as deployment or publish approval."""

from pydantic import ValidationError

from forj_review_config_service.application.handoff import export_handoff
from forj_review_config_service.contracts.handoff import package_digest
from forj_review_config_service.contracts.local_reports import LocalReport
from forj_review_config_service.contracts.models import ConfigurationError


def import_local_report(record, report):
    try:
        report = LocalReport.model_validate(report).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise ConfigurationError(
            "INVALID_LOCAL_REPORT", "本地报告格式无效，请使用运行器生成的 report.json", 422
        ) from exc
    package = export_handoff(record)
    task = report.get("task", {})
    if (
        task.get("digest") != package.digest
        or task.get("task_id") != record.task_id
        or task.get("intent_digest") != package.intent_digest
    ):
        raise ConfigurationError(
            "STALE_LOCAL_REPORT", "本地报告与当前接续任务不一致，请重新导出任务"
        )
    delivery = report.get("delivery") or {}
    evidence = report.get("evidence", [])
    refs = set(delivery.get("evidence_refs", []))
    if report["status"] == "HUMAN_HANDOFF":
        investigated = {
            package_digest(e.get("result"))
            for e in evidence
            if e.get("id") in refs
            and e.get("tool") in {"read_file", "search"}
            and isinstance(e.get("result"), dict)
            and e["result"].get("status") != "FAILED"
        }
        if (
            delivery.get("status") != "HUMAN_HANDOFF"
            or not delivery.get("obstacles")
            or len(investigated) < 2
        ):
            raise ConfigurationError(
                "HANDOFF_EVIDENCE_REQUIRED", "人工接续需具体障碍及不同的实际调查依据"
            )
    # Keep only reviewable report facts. Do not persist arbitrary workspace state or messages.
    record.local_report = {
        k: report[k]
        for k in (
            "task",
            "status",
            "delivery",
            "changed_files",
            "patch_digest",
            "verification_scope",
            "notice",
            "evidence",
            "source_head",
        )
        if k in report
    }
    record.report, record.pending = None, None
    if report["status"] == "HUMAN_HANDOFF":
        record.route = "HUMAN_HANDOFF"
        assessment = {
            **record.route_assessment,
            "route": "HUMAN_HANDOFF",
            "reason": "；".join(delivery["obstacles"]),
        }
        record.route_assessment = assessment
        for requirement in record.requirements:
            if (requirement.get("route_assessment") or {}).get("route") == "LOCAL_CODE":
                requirement["route_assessment"] = assessment
        record.waiting_reason = None
