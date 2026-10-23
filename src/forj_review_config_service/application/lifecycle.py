"""Archival suspends work while preserving its saved evidence and requirements."""

from datetime import UTC, datetime

from forj_review_config_service.application.conversation import tool_message
from forj_review_config_service.contracts.models import ConfigurationError


def archive(record):
    if record.state == "CLOSED":
        return
    if (
        record.receipt
        or record.turn_status in {"RUNNING", "QUEUED"}
        or (record.pending or {}).get("confirmed")
    ):
        raise ConfigurationError("SESSION_IN_USE", "请先停止执行或完成发布对账后归档")
    if (record.pending or {}).get("tool_call_id"):
        result = {"status": "CANCELLED", "reason": "任务归档，现场证据已保留"}
        record.tool_results[record.pending["tool_call_id"]] = result
        tool_message(record, record.pending["tool_call_id"], result)
    record.archive = {
        "archived_at": datetime.now(UTC).isoformat(),
        "previous_state": record.state,
    }
    record.pending, record.report = None, None
    record.state, record.waiting_reason = "CLOSED", "ARCHIVED"
    record.route, record.route_assessment = "UNDECIDED", None


def restore(record):
    if record.state != "CLOSED":
        raise ConfigurationError("NOT_ARCHIVED", "任务当前未归档")
    record.state = "DRAFTING"
    record.waiting_reason = None if record.snapshot else "CAPTURE"
    record.error = (
        "任务已恢复，需求、草稿与现场证据保留；发布前需重新验证"
        if record.snapshot
        else "任务已恢复；旧任务缺少现场，请关联已保存的页面采集"
    )
