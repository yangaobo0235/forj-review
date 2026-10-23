"""Requirement changes invalidate decisions independently from draft changes."""

from uuid import uuid4

from forj_review_config_service.contracts.models import ConfigurationError, TaskRecord
from forj_review_config_service.contracts.tasks import Requirement


def invalidate_intent(record: TaskRecord, affected: set[str]) -> None:
    record.intent_revision += 1
    record.report = None
    record.pending = None
    record.route = "UNDECIDED"
    record.route_assessment = None
    record.state = "DRAFTING"
    record.waiting_reason = None
    # These are judgments about the previous intent. Keep their history, but do not
    # let withdrawn ambiguities or capability gaps block the next interpretation.
    for decision in record.decisions:
        if decision.get("requirement_id") in affected:
            decision["status"] = "SUPERSEDED"
    record.capability_gaps = [
        gap for gap in record.capability_gaps if gap.requirement_id not in affected
    ]
    for requirement in record.requirements:
        if requirement["requirement_id"] in affected:
            requirement["route_assessment"] = None


def receive_message(record: TaskRecord, text: str) -> dict:
    message_id = "message_" + uuid4().hex
    record.messages.append({"role": "user", "content": text, "_event_id": message_id})
    record.pending_messages.append(message_id)
    return {"message_id": message_id}


def manage_requirements(record: TaskRecord, args: dict) -> dict:
    message_id = args["message_id"]
    if message_id not in record.pending_messages:
        raise ConfigurationError("STALE_REQUIREMENT", "只能解释尚未处理的用户消息")
    message = next((m for m in record.messages if m.get("_event_id") == message_id), None)
    if message is None:
        raise ConfigurationError("MESSAGE_UNAVAILABLE", "消息不在当前调查窗口，请分段读取原始消息")
    items = {r["requirement_id"]: r for r in record.requirements}
    targets = args.get("requirement_ids", [])
    if len(targets) != len(set(targets)) or any(
        key not in items or items[key].get("status", "ACTIVE") != "ACTIVE" for key in targets
    ):
        raise ConfigurationError("INVALID_REQUIREMENT", "修改目标必须是已有有效需求")
    action = args["action"]
    if action in {"REPLACE", "WITHDRAW"} and not targets:
        raise ConfigurationError("INVALID_REQUIREMENT", "更正或撤回必须指定原需求")
    if action in {"ACCEPT", "QUERY"} and targets:
        raise ConfigurationError("INVALID_REQUIREMENT", "新增或查询不能隐式撤回其他需求")
    goals = args.get("goals", [])
    if action in {"QUERY", "WITHDRAW"} and goals:
        raise ConfigurationError("INVALID_REQUIREMENT", "查询或撤回不创建新目标")
    if action in {"ACCEPT", "REPLACE"} and not goals:
        goals = [{"text": message["content"], "acceptance": args.get("acceptance", [])}]
    for goal in goals:
        if set(goal.get("depends_on", [])) - items.keys() or any(
            items[key].get("status") != "ACTIVE" or key in targets
            for key in goal.get("depends_on", [])
        ):
            raise ConfigurationError("INVALID_DEPENDENCY", "新目标只能依赖仍有效的已有目标")
    if action != "QUERY":
        affected = set(targets)
        while True:
            expanded = affected | {
                r["requirement_id"]
                for r in record.requirements
                if set(r.get("depends_on", [])) & affected
            }
            if expanded == affected:
                break
            affected = expanded
        invalidate_intent(record, affected)
    for key in targets:
        items[key]["status"] = "SUPERSEDED" if action == "REPLACE" else "WITHDRAWN"
        items[key]["last_changed_event_id"] = message_id
        items[key]["requirement_revision"] = record.intent_revision
    created = []
    for goal in goals:
        item: Requirement = {
            "requirement_id": "req_" + uuid4().hex,
            "text": goal["text"],
            "status": "ACTIVE",
            "source_event_id": message_id,
            "last_changed_event_id": message_id,
            "requirement_revision": record.intent_revision,
            "acceptance": goal.get("acceptance", []),
            "assertions": goal.get("assertions", []),
            "target_refs": goal.get("target_refs", []),
            "depends_on": goal.get("depends_on", []),
            "replaces": targets if action == "REPLACE" else [],
        }
        record.requirements.append(item)
        created.append(item)
    record.pending_messages.remove(message_id)
    return {
        "intent_revision": record.intent_revision,
        "created": created,
        "requirements": record.requirements,
    }
