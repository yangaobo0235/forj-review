"""Persist task requirements, execution rounds and version-bound interactions."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select

from forj_review_config_service.contracts.models import ConfigurationError
from forj_review_database.models import TaskEvent, TaskInteraction, TaskRequirement, TaskRun


def working_context(record):
    # These are typed working references and recoverable presentation projections.
    # Core task, requirement, run, message and interaction facts have dedicated owners.
    names = {
        "schema_version",
        "pending_messages",
        "reference_fields",
        "reference_samples",
        "activation_only",
        "archive",
        "preview",
        "activation",
        "adoption_task",
        "adoption",
        "child_tasks",
        "capture_transition",
        "investigated_tools",
        "error",
    }
    data = record.model_dump(mode="json", include=names)
    data["mapping_decisions"] = [d for d in record.decisions if not d.get("requirement_id")]
    data["capability_gaps"] = [g.model_dump(mode="json") for g in record.capability_gaps]
    return data


async def load_task_children(db, row):
    requirements = (
        await db.scalars(
            select(TaskRequirement)
            .where(TaskRequirement.task_id == row.id)
            .order_by(TaskRequirement.created_at, TaskRequirement.id)
        )
    ).all()
    run = await db.get(TaskRun, row.current_run_id) if row.current_run_id else None
    pending = await db.scalar(
        select(TaskInteraction).where(
            TaskInteraction.task_id == row.id, TaskInteraction.status == "PENDING"
        )
    )
    decisions = list(row.context.get("mapping_decisions", []))
    assessments = []
    items = []
    for r in requirements:
        decisions.extend(r.assessment.get("decisions", []))
        if r.status in {"ACTIVE", "PENDING"} and r.assessment.get("route"):
            assessments.append(r.assessment["route"])
        items.append(
            dict(
                requirement_id=r.id,
                text=r.text,
                status=r.status,
                source_event_id=r.source_event_id,
                last_changed_event_id=r.last_changed_event_id,
                requirement_revision=r.requirement_revision,
                target_refs=r.target_refs,
                replaces=r.replaces,
                depends_on=r.depends_on,
                acceptance=r.acceptance,
                route_assessment=r.assessment.get("route"),
                assertions=r.assessment.get("assertions", []),
            )
        )
    priority = {"UNDECIDED": 0, "CONFIG": 1, "LOCAL_CODE": 2, "HUMAN_HANDOFF": 3}
    assessment = max(assessments, key=lambda a: priority[a["route"]]) if assessments else None
    route_event = await db.scalar(
        select(TaskEvent)
        .where(TaskEvent.task_id == row.id, TaskEvent.kind == "ROUTE_ASSESSMENT")
        .order_by(TaskEvent.seq.desc())
        .limit(1)
    )
    if assessment is None and route_event:
        route_data = route_event.payload
        assessment = route_data.get("assessment")
    else:
        route_data = {}
    results = (
        await db.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == row.id, TaskEvent.kind == "WORKING_RESULT")
            .order_by(TaskEvent.seq)
        )
    ).all()
    local = await db.scalar(
        select(TaskEvent)
        .where(TaskEvent.task_id == row.id, TaskEvent.kind == "LOCAL_DELIVERY")
        .order_by(TaskEvent.seq.desc())
        .limit(1)
    )
    return dict(
        requirements=items,
        decisions=decisions,
        local_report=local.payload if local else None,
        route=assessment["route"] if assessment else route_data.get("route", "UNDECIDED"),
        route_assessment=assessment,
        waiting_reason=row.wait_reason,
        parent_task_id=row.parent_task_id,
        turn_id=run.id if run else None,
        turn_status=run.status if run else "COMPLETED",
        model_id=run.model_id if run else "",
        max_model_calls=run.budget["model_calls"] if run else 24,
        error_code=run.error_code if run else None,
        model_calls=run.usage.get("model_calls", 0) if run else 0,
        tool_calls=run.usage.get("tool_calls", 0) if run else 0,
        usage=run.usage.get("tokens", {}) if run else {},
        failure_counts=run.usage.get("failure_counts", {}) if run else {},
        pending=pending.payload if pending else None,
        tool_results={r.payload["key"]: r.payload["value"] for r in results},
    )


async def save_task_children(db, record, row, trigger_event_id):
    now = datetime.now(UTC)
    if record.turn_id:
        run = await db.get(TaskRun, record.turn_id)
        if run is None:
            number = await db.scalar(
                select(func.coalesce(func.max(TaskRun.run_no), 0)).where(TaskRun.task_id == row.id)
            )
            run = TaskRun(
                id=record.turn_id,
                task_id=row.id,
                run_no=number + 1,
                trigger_event_id=trigger_event_id,
                input_intent_revision=record.intent_revision,
                input_draft_revision=record.revision,
                input_evidence_revision=record.evidence_revision,
                model_id=record.model_id,
                budget={"model_calls": record.max_model_calls, "tool_calls": 120},
                usage={},
                status=record.turn_status,
            )
            db.add(run)
        elif run.task_id != row.id:
            raise ConfigurationError("RUN_OWNERSHIP", "执行轮次不属于当前任务")
        run.status = record.turn_status
        run.usage = dict(
            model_calls=record.model_calls,
            tool_calls=record.tool_calls,
            tokens=record.usage,
            failure_counts=record.failure_counts,
        )
        run.error_code = record.error_code
        run.ended_at = (
            now
            if record.turn_status in {"COMPLETED", "INTERRUPTED", "CANCELLED", "FAILED"}
            else None
        )
    route_event = await db.scalar(
        select(TaskEvent)
        .where(TaskEvent.task_id == row.id, TaskEvent.kind == "ROUTE_ASSESSMENT")
        .order_by(TaskEvent.seq.desc())
        .limit(1)
    )
    route_payload = {"route": record.route, "assessment": record.route_assessment}
    if not route_event or route_event.payload != route_payload:
        db.add(
            TaskEvent(
                task_id=row.id,
                run_id=record.turn_id,
                kind="ROUTE_ASSESSMENT",
                payload_schema_version="1.0",
                payload=route_payload,
            )
        )
    for item in record.requirements:
        requirement = await db.get(TaskRequirement, item["requirement_id"])
        if requirement is None:
            requirement = TaskRequirement(id=item["requirement_id"], task_id=row.id)
            db.add(requirement)
        elif requirement.task_id != row.id:
            raise ConfigurationError("REQUIREMENT_OWNERSHIP", "目标不属于当前任务")
        requirement.text = item["text"]
        requirement.status = item.get("status", "ACTIVE")
        requirement.requirement_revision = item.get("requirement_revision", record.intent_revision)
        requirement.source_event_id = item.get("source_event_id")
        requirement.last_changed_event_id = item.get("last_changed_event_id")
        requirement.target_refs = item.get("target_refs", [])
        requirement.replaces = item.get("replaces", [])
        requirement.depends_on = item.get("depends_on", [])
        requirement.acceptance = item.get("acceptance", [])
        requirement.assessment = {
            "assertions": item.get("assertions", []),
            "decisions": [
                d for d in record.decisions if d.get("requirement_id") == item["requirement_id"]
            ],
            "route": item.get("route_assessment"),
        }
        requirement.updated_at = now
    previous = await db.scalar(
        select(TaskInteraction).where(
            TaskInteraction.task_id == row.id, TaskInteraction.status == "PENDING"
        )
    )
    if previous and (not record.pending or record.pending["id"] != previous.id):
        previous.status, previous.completed_at = "SUPERSEDED", now
        await db.flush()
    if record.pending:
        pending = record.pending
        interaction = await db.get(TaskInteraction, pending["id"])
        if interaction is None:
            interaction = TaskInteraction(
                id=pending["id"],
                task_id=row.id,
                run_id=record.turn_id,
                kind=pending["kind"],
                status="PENDING",
                intent_revision=record.intent_revision,
                draft_revision=record.revision,
                evidence_revision=record.evidence_revision,
                candidate_digest=pending.get(
                    "candidate_digest", (record.preview or {}).get("candidate_digest")
                ),
                validation_run_id=row.current_validation_run_id,
                payload_schema_version="1.0",
                payload=pending,
                expires_at=datetime.fromisoformat(pending["expires_at"])
                if pending.get("expires_at")
                else None,
            )
            db.add(interaction)
        elif interaction.task_id != row.id or (
            interaction.intent_revision,
            interaction.draft_revision,
            interaction.evidence_revision,
        ) != (record.intent_revision, record.revision, record.evidence_revision):
            raise ConfigurationError("STALE_INTERACTION", "交互的目标、草稿或现场已更新")
        interaction.payload = dict(pending)
        if (
            any(key in pending for key in ("answer", "result", "confirmed"))
            and interaction.answer_event_id is None
        ):
            response_id = uuid4().hex
            db.add(
                TaskEvent(
                    id=response_id,
                    task_id=row.id,
                    run_id=record.turn_id,
                    kind="INTERACTION_RESPONSE",
                    payload_schema_version="1.0",
                    payload={
                        "interaction_id": pending["id"],
                        **{
                            k: pending[k] for k in ("answer", "result", "confirmed") if k in pending
                        },
                    },
                )
            )
            interaction.answer_event_id = response_id
    if previous and not record.pending and previous.answer_event_id:
        previous.status = "ANSWERED"
    if record.local_report:
        delivery = await db.scalar(
            select(TaskEvent)
            .where(TaskEvent.task_id == row.id, TaskEvent.kind == "LOCAL_DELIVERY")
            .order_by(TaskEvent.seq.desc())
            .limit(1)
        )
        if delivery is None or delivery.payload != record.local_report:
            db.add(
                TaskEvent(
                    task_id=row.id,
                    run_id=record.turn_id,
                    kind="LOCAL_DELIVERY",
                    payload_schema_version="1.0",
                    payload=record.local_report,
                )
            )
    model_results = {m["tool_call_id"] for m in record.messages if m.get("role") == "tool"}
    stored = (
        await db.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == row.id, TaskEvent.kind == "WORKING_RESULT")
            .order_by(TaskEvent.seq)
        )
    ).all()
    current = {r.payload["key"]: r.payload["value"] for r in stored}
    for key, value in record.tool_results.items():
        if key not in model_results and (key not in current or current[key] != value):
            db.add(
                TaskEvent(
                    id=uuid4().hex,
                    task_id=row.id,
                    run_id=record.turn_id,
                    kind="WORKING_RESULT",
                    payload_schema_version="1.0",
                    payload={"key": key, "value": value},
                )
            )
