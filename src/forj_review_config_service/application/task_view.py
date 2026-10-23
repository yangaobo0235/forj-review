"""Application read models for task detail, history and candidate export."""

from forj_review_config_service.application.conversation import chat_messages
from forj_review_config_service.application.progress import turn_summary
from forj_review_config_service.application.requirement_checks import requirement_checks
from forj_review_config_service.contracts.models import TaskRecord
from forj_review_config_service.contracts.tasks import active_requirements, intent_digest
from forj_review_config_service.tools.ports import ConfigurationKernel

WAITING_LABELS = {
    "CAPTURE": "等待业务页面现场",
    "LOCAL_EXECUTION": "等待开发人员本地接续",
    "CAPABILITY_INVESTIGATION": "正在调查能力缺口",
    "ARCHIVED": "已归档",
    "ACCEPTANCE": "等待确认独立验收预期",
}


def task_view(record: TaskRecord) -> dict:
    pending = record.pending or {}
    local_current = bool(record.local_report) and (record.local_report or {}).get("task", {}).get(
        "intent_digest"
    ) == intent_digest(record)
    requirements = active_requirements(record)
    checks = {c["requirement_id"]: c for c in requirement_checks(record)}
    coverage = [
        {
            **r,
            "route": (r.get("route_assessment") or {}).get("route", "UNDECIDED"),
            "resolved": checks[r["requirement_id"]]["decision_recorded"],
            "verified": checks[r["requirement_id"]]["ok"] and bool((record.report or {}).get("ok")),
        }
        for r in requirements
    ]
    stage, label = "understanding", "等待描述修改要求"
    if record.state == "CLOSED":
        stage, label = "archived", "已归档"
    elif record.receipt:
        complete = (record.activation or {}).get("state") == "EFFECTIVE" and bool(record.adoption)
        stage, label = (
            ("complete", "配置已生效") if complete else ("activation", "已发布，等待采用核对")
        )
    elif pending.get("confirmed"):
        stage, label = "reconciliation", "正在发布或核对原请求结果"
    elif record.route == "LOCAL_CODE":
        local = (record.local_report or {}) if local_current else {}
        stage, label = (
            "local",
            {
                "LOCAL_CHECKS_PASSED": "已交付本地补丁，待开发人员评审",
                "UNVERIFIED_PATCH": "本地补丁尚需验证",
                "WAITING_ENVIRONMENT": "本地检查等待环境恢复",
                "NEEDS_INPUT": "本地接续等待补充依据",
            }.get(local.get("status"), "等待开发人员本地接续"),
        )
    elif record.route == "HUMAN_HANDOFF":
        stage, label = "human", "需要人工设计决定"
    elif record.turn_status in {"CANCELLED", "INTERRUPTED", "FAILED"}:
        stage, label = "paused", "已暂停" if record.turn_status == "CANCELLED" else "需要恢复处理"
    elif pending.get("kind") == "question":
        stage, label = "question", "等待回答关键问题"
    elif pending.get("kind") == "browser":
        stage, label = "collection", "等待只读页面验证"
    elif (
        record.state == "READY_FOR_CONFIRMATION"
        and (record.report or {}).get("ok")
        and (record.report or {}).get("intent_digest", intent_digest(record))
        == intent_digest(record)
    ):
        stage, label = (
            ("review", "等待审阅并发布")
            if (record.report or {}).get("changes_required", True)
            else ("unchanged", "当前配置已满足要求，无需发布")
        )
    elif record.waiting_reason:
        stage, label = "waiting", WAITING_LABELS.get(record.waiting_reason, "等待补充资料")
    elif record.report:
        stage, label = "verification", "验证尚有项目需要处理"
    elif requirements:
        stage, label = (
            "preparation",
            "正在准备修改" if record.turn_status in {"RUNNING", "QUEUED"} else "候选已暂存",
        )
    return {
        "stage": stage,
        "progress_step": 4
        if stage in {"activation", "complete"}
        else 3
        if stage == "reconciliation"
        else 2
        if stage in {"verification", "collection", "review"}
        else 1,
        "label": label,
        "coverage": coverage,
        "can_publish": stage == "review"
        and not record.pending_messages
        and record.turn_status not in {"RUNNING", "QUEUED"},
        "complete": stage in {"complete", "unchanged"},
        "local_report_current": local_current,
    }


def task_detail(record: TaskRecord, kernel: ConfigurationKernel, environment: str) -> dict:
    return {
        **record.model_dump(mode="json"),
        "chat_messages": chat_messages(record),
        "execution_summary": turn_summary(record)
        if record.turn_id and record.turn_status not in {"RUNNING", "QUEUED"}
        else None,
        "page_options": kernel.page_options(record.draft),
        "field_dependencies": kernel.field_dependencies(record.draft),
        "comparison_configurable_fields": kernel.comparison_configurable_fields(record.draft),
        "task": task_view(record),
        "environment": environment,
        "execution_mode": "CONFIGURATION_SERVICE",
    }


def candidate_export(record: TaskRecord) -> dict:
    proof = next(
        (
            result
            for result in reversed(list(record.tool_results.values()))
            if isinstance(result, dict) and result.get("kind") == "collection_trial"
        ),
        None,
    )
    return {
        "candidate": record.draft,
        "evidence": {
            "schema_version": "2.0",
            **record.model_dump(
                mode="json",
                include={
                    "snapshot",
                    "capture_id",
                    "revision",
                    "intent_revision",
                    "evidence_revision",
                    "reference_fields",
                    "reference_samples",
                    "decisions",
                },
            ),
            "proof": proof,
        },
    }
