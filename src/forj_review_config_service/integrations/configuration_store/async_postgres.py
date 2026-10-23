"""Fenced workflow storage. Events own messages; reports and releases own evidence."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, func, select

from forj_review_config_service.contracts.models import (
    CapturedPage,
    ConfigurationError,
    TaskRecord,
)
from forj_review_config_service.contracts.tasks import intent_digest
from forj_review_config_service.contracts.tool_results import result_reference
from forj_review_config_service.integrations.async_database import AsyncDatabase
from forj_review_config_service.integrations.configuration_store.task_records import (
    load_task_children,
    save_task_children,
    working_context,
)
from forj_review_config_service.integrations.review_kernel.storage import (
    KERNEL_CONTRACT_VERSION,
    KERNEL_VERSION,
    PublicationConflict,
    digest,
    extract_receipt,
    publish_state_async,
    read_state_async,
    receipt_query,
)
from forj_review_database.models import (
    Capture,
    ConfigurationTask,
    Head,
    Idempotency,
    Page,
    PageVersion,
    Release,
    ReleaseItem,
    TaskCapture,
    TaskEvent,
    TaskRun,
    ValidationRun,
)
from forj_review_database.validation import ValidationReport

SNAPSHOT_COLUMNS = {
    "snapshot_id",
    "page_instance_id",
    "page_url",
    "title",
    "structure_digest",
    "content_digest",
    "fields",
    "samples",
    "diagnostics",
    "headings",
    "record_identity",
}


def request_scope(scope):
    return tuple(reversed(scope.rsplit(":", 1))) if ":" in scope else (scope, "global")


def capture_from_row(row):
    return CapturedPage.model_validate(
        dict(
            capture_id=row.id,
            extension_id=row.source_extension_id,
            snapshot={
                "detected_page_id": row.detected_page_id,
                "configuration_version": row.observed_page_version,
                **{k: getattr(row, k) for k in SNAPSHOT_COLUMNS},
                "schema_version": row.snapshot_schema_version,
            },
            created_at=row.captured_at.isoformat(),
        )
    )


def idempotency_filter(operation, scope_key, request_id):
    return (
        Idempotency.operation_type == operation,
        Idempotency.scope_id == scope_key,
        Idempotency.request_id == request_id,
    )


def event_message(row):
    return {
        **(row.payload or {}),
        "_run_id": row.run_id,
        "role": row.role.lower(),
        "content": row.content or "",
        **({"tool_call_id": row.tool_call_id} if row.tool_call_id else {}),
    }


async def tool_content(db, task_id, message):
    reference = message.get("_result_chunks")
    if not reference:
        return message["content"]
    chunks = (
        await db.scalars(
            select(TaskEvent)
            .where(
                TaskEvent.task_id == task_id,
                TaskEvent.kind == "TOOL_RESULT_CHUNK",
                TaskEvent.tool_call_id == message["tool_call_id"],
            )
            .order_by(TaskEvent.seq)
        )
    ).all()
    content = "".join(chunk.content for chunk in chunks)
    if (
        len(chunks) != reference["count"]
        or [chunk.payload["index"] for chunk in chunks] != list(range(reference["count"]))
        or digest(content) != reference["digest"]
    ):
        raise ConfigurationError("TOOL_RESULT_INCOMPLETE", "工具证据分片缺失或摘要不符")
    return content


def compact_report(record):
    report = record.report
    summary = {
        k: report[k]
        for k in (
            "candidate_digest",
            "effective_digest",
            "kernel_version",
            "revision",
            "ok",
            "blockers",
            "decision_summary",
            "diff",
            "page_id",
            "required_materials",
            "intent_digest",
            "intent_revision",
            "started_at",
            "completed_at",
            "verification_context",
            "checks",
            "evidence_revision",
            "requirement_checks",
            "changes_required",
        )
        if k in report
    }
    trials = [
        v
        for v in record.tool_results.values()
        if isinstance(v, dict)
        and v.get("kind") == "collection_trial"
        and v.get("revision") == record.revision
    ]
    trial = trials[-1] if trials else None
    collection = None
    if trial:
        facts = trial.get("facts", {})
        collection = {
            k: facts[k]
            for k in (
                "page_instance_id",
                "structure_digest",
                "fields",
                "control_kinds",
                "matched_page_id",
            )
            if k in facts
        }
        collection["proof_digest"] = digest(trial)
    return ValidationReport(
        checks=[
            dict(
                page_code=record.draft["page_id"],
                target_kind="PAGE",
                target_key=record.draft["page_id"],
                check_code="CONFIGURATION",
                status="PASSED" if report["ok"] else "FAILED",
                message="验证通过"
                if report["ok"]
                else "；".join(report.get("blockers", []))[:4000],
            )
        ],
        collection=collection,
        replays=report.get("replays", []),
        summary=summary,
    ).model_dump(mode="json")


class AsyncTaskStore:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def interrupt_running(self):
        for item in await self.list():
            record = await self.get(item["task_id"])
            if record.turn_status in {"QUEUED", "RUNNING"}:
                record.turn_status = "INTERRUPTED"
                record.error = "上次执行因服务退出中断，可以恢复；模型调用可能产生额外用量"
                await self.put(record, "turn_stopped", {"status": "INTERRUPTED"})

    async def _record(self, db, row):
        capture = await db.get(Capture, row.current_capture_id) if row.current_capture_id else None
        messages = (
            await db.scalars(
                select(TaskEvent)
                .where(TaskEvent.task_id == row.id, TaskEvent.kind == "MESSAGE")
                .order_by(TaskEvent.seq.desc())
                .limit(256)
            )
        ).all()
        proof = (
            await db.get(ValidationRun, row.current_validation_run_id)
            if row.current_validation_run_id
            else None
        )
        receipt = None
        if row.published_release_id:
            operation = await db.scalar(
                select(Release.operation_id).where(Release.id == row.published_release_id)
            )
            receipt = extract_receipt((await db.execute(receipt_query(operation))).first())
        report = (
            {**proof.report["summary"], "report_id": proof.id, "replays": proof.report["replays"]}
            if proof
            else None
        )
        message_count = await db.scalar(
            select(func.count())
            .select_from(TaskEvent)
            .where(TaskEvent.task_id == row.id, TaskEvent.kind == "MESSAGE")
        )
        history = [event_message(m) for m in reversed(messages)]
        # A bounded window must never start with orphaned tool results.
        while history and history[0]["role"] == "tool":
            history.pop(0)
        child = await load_task_children(db, row)
        record = TaskRecord.model_validate(
            {
                **{k: v for k, v in row.context.items() if k != "mapping_decisions"},
                **child,
                "task_id": row.id,
                "capture_id": row.current_capture_id,
                "snapshot": capture_from_row(capture).snapshot if capture else None,
                "baseline_digest": row.base_page_digest,
                "baseline_version": await db.scalar(
                    select(PageVersion.version_no).where(PageVersion.id == row.base_page_version_id)
                ),
                "state": row.status,
                "turn_status": child["turn_status"],
                "revision": row.draft_revision,
                "intent_revision": row.intent_revision,
                "evidence_revision": row.evidence_revision,
                "row_version": row.row_version,
                "route": child["route"],
                "draft": row.draft,
                "messages": history,
                "report": report,
                "receipt": receipt,
                "updated_at": row.updated_at.isoformat(),
            }
        )
        record._persisted_message_count = message_count
        record._persisted_window_size = len(history)
        record._persisted_window_digest = digest(history)
        for message in history:
            if message.get("role") == "tool":
                record.tool_results[message["tool_call_id"]] = json.loads(
                    await tool_content(db, row.id, message)
                )
        return record

    async def tool_result(self, task_id, call_id):
        async with self.database.session(readonly=True) as db:
            event = await db.scalar(
                select(TaskEvent)
                .where(
                    TaskEvent.task_id == task_id,
                    TaskEvent.kind == "MESSAGE",
                    TaskEvent.role == "TOOL",
                    TaskEvent.tool_call_id == call_id,
                )
                .order_by(TaskEvent.seq.desc())
                .limit(1)
            )
            if event is None:
                raise ConfigurationError("NOT_FOUND", "工具结果不存在", 404)
            return json.loads(await tool_content(db, task_id, event_message(event)))

    async def get(self, task_id):
        async with self.database.session(readonly=True, repeatable=True) as db:
            row = await db.get(ConfigurationTask, task_id)
            if row is None:
                raise ConfigurationError("NOT_FOUND", "配置会话不存在", 404)
            return await self._record(db, row)

    async def get_capture(self, capture_id):
        async with self.database.session(readonly=True) as db:
            row = await db.get(Capture, capture_id)
            if row is None:
                raise ConfigurationError(
                    "CAPTURE_NOT_FOUND", "页面采集不存在，请回到业务页面重新采集", 404
                )
            capture = capture_from_row(row)
            return capture

    async def list_captures(self, task_id=None):
        async with self.database.session(readonly=True) as db:
            query = select(Capture)
            if task_id:
                query = query.join(TaskCapture, TaskCapture.capture_id == Capture.id).where(
                    TaskCapture.task_id == task_id
                )
            rows = (
                await db.execute(
                    query.order_by(Capture.captured_at.desc(), Capture.id.desc()).limit(200)
                )
            ).all()
            items = []
            for (row,) in rows:
                capture = capture_from_row(row)
                items.append(
                    dict(
                        capture_id=row.id,
                        title=row.title,
                        created_at=row.captured_at.isoformat(),
                        page_id=capture.snapshot.detected_page_id,
                        page_url=row.page_url,
                        field_count=len(capture.snapshot.fields),
                        sample_count=len(capture.snapshot.samples),
                        diagnostics=capture.snapshot.diagnostics,
                    )
                )
            return items

    def _capture(self, capture):
        data = capture.snapshot.model_dump(mode="json")
        metadata = {k: data.pop(k) for k in SNAPSHOT_COLUMNS}
        schema = data.pop("schema_version")
        return Capture(
            id=capture.capture_id,
            source_extension_id=capture.extension_id,
            **metadata,
            snapshot_schema_version=schema,
            detected_page_id=data["detected_page_id"],
            observed_page_version=data["configuration_version"],
            captured_at=datetime.fromisoformat(capture.created_at),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )

    async def put_capture(self, capture, request_id, fingerprint):
        result = {"capture_id": capture.capture_id}
        async with self.database.session(write=True) as db:
            row = self._capture(capture)
            row.page_id = await db.scalar(
                select(Page.id).where(Page.page_code == capture.snapshot.detected_page_id)
            )
            db.add(row)
            await self._request(db, ("capture", request_id, fingerprint, result))
        return result

    async def delete_capture(self, capture_id):
        async with self.database.session(write=True) as db:
            if await db.get(Capture, capture_id) is None:
                raise ConfigurationError("CAPTURE_NOT_FOUND", "页面采集不存在", 404)
            if await db.scalar(
                select(TaskCapture.task_id).where(TaskCapture.capture_id == capture_id).limit(1)
            ):
                raise ConfigurationError("CAPTURE_IN_USE", "该采集仍关联配置会话，请先清理相应会话")
            await db.execute(delete(Capture).where(Capture.id == capture_id))

    async def list(self):
        async with self.database.session(readonly=True) as db:
            rows = (
                await db.execute(
                    select(
                        ConfigurationTask,
                        Page.page_code,
                        ValidationRun.report,
                    )
                    .join(Page, Page.id == ConfigurationTask.page_id)
                    .outerjoin(
                        ValidationRun,
                        ValidationRun.id == ConfigurationTask.current_validation_run_id,
                    )
                    .order_by(ConfigurationTask.updated_at.desc(), ConfigurationTask.id.desc())
                    .limit(200)
                )
            ).all()
            items = []
            for row, page_code, validation in rows:
                child = await load_task_children(db, row)
                task_record = {
                    **{k: v for k, v in row.context.items() if k != "mapping_decisions"},
                    **child,
                    "task_id": row.id,
                    "revision": row.draft_revision,
                    "intent_revision": row.intent_revision,
                    "state": row.status,
                    "turn_status": child["turn_status"],
                    "route": child["route"],
                    "baseline_digest": row.base_page_digest,
                    "draft": row.draft,
                    "report": validation["summary"] if validation else None,
                    "receipt": {"release_id": row.published_release_id}
                    if row.published_release_id
                    else None,
                }
                items.append(
                    dict(
                        task_id=row.id,
                        state=row.status,
                        page_id=page_code,
                        title=str(row.draft.get("title", page_code)),
                        task_record=task_record,
                        updated_at=row.updated_at.isoformat(),
                    )
                )
            return items

    async def _request(self, db, request):
        scope, request_id, fingerprint, result = request
        operation, scope_key = request_scope(scope)
        # Results are receipts, never another copy of the candidate/report.
        result = {
            k: v
            for k, v in result.items()
            if k
            in {
                "task_id",
                "capture_id",
                "revision",
                "event_seq",
                "state",
                "report_id",
                "ok",
                "status",
            }
        }
        await db.execute(
            delete(Idempotency).where(
                *idempotency_filter(operation, scope_key, request_id),
                Idempotency.expires_at <= func.now(),
            )
        )
        db.add(
            Idempotency(
                operation_type=operation,
                scope_id=scope_key,
                scope_type="GLOBAL" if scope_key == "global" else "TASK",
                request_id=request_id,
                request_digest=fingerprint,
                result_ref=result,
            )
        )

    async def _validation(self, db, record, row):
        if record.receipt and record.receipt.get("release_id"):
            # A new browser scene verifies adoption, not the historical publication.
            # Keep the immutable release proof instead of rebinding it to that scene.
            release = await db.get(Release, record.receipt["release_id"])
            if release is None or row.published_release_id != release.id:
                raise ConfigurationError("RELEASE_MISMATCH", "发布记录与任务不一致")
            return release.validation_run_id
        if not record.report:
            return None
        if "report_id" not in record.report or "ok" not in record.report:
            raise ConfigurationError("INCOMPLETE_VALIDATION", "预览不是完成验证，不能作为发布证据")
        rid = record.report["report_id"]
        verification_context = record.report.get(
            "verification_context", {"schema_version": "legacy"}
        )
        if len(json.dumps(verification_context, ensure_ascii=False).encode("utf8")) > 65536:
            raise ConfigurationError(
                "VALIDATION_CONTEXT_LIMIT", "验证依据超过保存上限，请拆分有效需求后重新验证", 422
            )
        existing = await db.get(ValidationRun, rid)
        if existing:
            if (
                existing.candidate_revision != record.revision
                or existing.candidate_digest != record.report.get("candidate_digest")
                or existing.report["summary"] != compact_report(record)["summary"]
                or existing.scene_digest
                != (digest(record.snapshot.model_dump(mode="json")) if record.snapshot else None)
                or existing.intent_digest != record.report.get("intent_digest")
            ):
                raise ConfigurationError("STALE_VALIDATION", "验证报告与草稿或现场不符")
            return rid
        state = await read_state_async(db)
        # A failed compile need not have a stageable candidate.
        try:
            _, staged = state.stage(record.draft, activation_only=record.activation_only)
            manifest = digest(
                {
                    page: digest(staged.source(page, version))
                    for page, version in sorted(staged.active.items())
                }
            )
        except ValueError:
            if record.report["ok"]:
                raise
            manifest = digest({"invalid_candidate": record.draft})
        now = datetime.now(UTC)
        db.add(
            ValidationRun(
                id=rid,
                session_id=record.task_id,
                purpose="ROLLBACK" if record.activation_only else "PUBLISH",
                base_release_id=row.base_release_id,
                candidate_page_id=row.page_id,
                candidate_revision=record.revision,
                candidate_digest=record.report.get("candidate_digest"),
                manifest_digest=manifest,
                digest_version="manifest-v1",
                scene_digest=digest(record.snapshot.model_dump(mode="json"))
                if record.snapshot
                else None,
                kernel_version=KERNEL_CONTRACT_VERSION,
                kernel_fingerprint=KERNEL_VERSION,
                intent_digest=record.report.get("intent_digest"),
                verification_context=verification_context,
                publish_valid_until=now + timedelta(minutes=15),
                report_schema_version="1.0",
                status="PASSED" if record.report["ok"] else "FAILED",
                report=compact_report(record),
                performed_by="local",
                started_at=datetime.fromisoformat(record.report["started_at"])
                if record.report.get("started_at")
                else now,
                completed_at=datetime.fromisoformat(record.report["completed_at"])
                if record.report.get("completed_at")
                else now,
                expires_at=now
                + (
                    timedelta(days=30)
                    if record.report.get("intent_digest")
                    else timedelta(minutes=15)
                ),
            )
        )
        await db.flush()
        return rid

    async def put(self, record, event="progress", data=None, request=None):
        record.updated_at = datetime.now(UTC).isoformat()
        async with self.database.session(write=True) as db:
            row = await db.scalar(
                select(ConfigurationTask)
                .where(ConfigurationTask.id == record.task_id)
                .with_for_update()
            )
            if row and record.row_version != row.row_version:
                raise ConfigurationError("STALE_TASK", "任务已更新，请重新读取后操作")
            if row and record.revision < row.draft_revision:
                raise ConfigurationError("STALE_REVISION", "数据库草稿已变化，禁止旧检查点覆盖")
            if (
                row
                and record.revision == row.draft_revision
                and row.draft_digest != digest(record.draft)
            ):
                raise ConfigurationError("STALE_REVISION", "修改草稿必须递增修订号")
            if (
                row
                and row.published_release_id
                and (
                    not record.receipt
                    or record.receipt.get("release_id") != row.published_release_id
                )
            ):
                raise ConfigurationError("SESSION_SAVED", "已发布记录不能被旧检查点覆盖")
            if row and row.base_page_digest != record.baseline_digest:
                raise ConfigurationError("BASELINE_CHANGED", "会话基线不可在保存时更换")
            if row and row.page_id != await db.scalar(
                select(Page.id).where(Page.page_code == record.draft.get("page_id"))
            ):
                raise ConfigurationError("PAGE_IDENTITY_CHANGED", "会话不能更换页面身份")
            captured = await db.get(Capture, record.capture_id) if record.capture_id else None
            if record.capture_id and captured is None:
                raise ConfigurationError("CAPTURE_NOT_FOUND", "关联的页面采集不存在", 404)
            if captured and record.snapshot != capture_from_row(captured).snapshot:
                raise ConfigurationError("CAPTURE_MISMATCH", "不可改写已保存现场，请关联新的采集")
            if record.snapshot is not None and not captured:
                # Persist an inline directory once; never rewrite an existing capture.
                capture = CapturedPage(
                    capture_id=uuid4().hex,
                    extension_id="local",
                    snapshot=record.snapshot,
                    created_at=record.updated_at,
                )
                db.add(self._capture(capture))
                await db.flush()
                record.capture_id = capture.capture_id
            page_id = await db.scalar(
                select(Page.id).where(Page.page_code == record.draft.get("page_id"))
            )
            if not page_id:
                raise ConfigurationError("EXISTING_PAGE_REQUIRED", "只能维护已接入页面", 422)
            context = working_context(record)
            if row is None:
                current = await read_state_async(db)
                if (
                    current.page_baseline(record.draft["page_id"], KERNEL_VERSION)
                    != record.baseline_digest
                ):
                    raise ConfigurationError("BASELINE_CHANGED", "创建会话时配置基线已变化")
                base = await db.scalar(
                    select(ReleaseItem.page_version_id)
                    .join(Head, Head.release_id == ReleaseItem.release_id)
                    .where(ReleaseItem.page_id == page_id)
                )
                row = ConfigurationTask(
                    id=record.task_id,
                    page_id=page_id,
                    base_page_version_id=base,
                    base_release_id=await db.scalar(select(Head.release_id).where(Head.id == 1)),
                    base_page_digest=record.baseline_digest,
                    kernel_fingerprint=KERNEL_VERSION,
                    row_version=record.row_version,
                    evidence_revision=record.evidence_revision,
                    intent_revision=record.intent_revision,
                    parent_task_id=record.parent_task_id,
                )
                # Insert before message events and validation FK updates.
                row.current_capture_id = record.capture_id
                row.status = record.state
                row.current_run_id = record.turn_id
                row.draft_revision = record.revision
                row.draft = record.draft
                row.draft_digest = digest(record.draft)
                row.context = context
                db.add(row)
                await db.flush()
            row.current_capture_id = record.capture_id
            if record.capture_id:
                association = await db.get(TaskCapture, (record.task_id, record.capture_id))
                if association is None:
                    association = TaskCapture(task_id=record.task_id, capture_id=record.capture_id)
                    db.add(association)
                association.bindings = {
                    "evidence_revision": record.evidence_revision,
                    "fields": {
                        d["candidate_id"]: d["field_key"]
                        for d in record.decisions
                        if d.get("candidate_id")
                        and d.get("field_key")
                        and d["status"] == "RESOLVED"
                        and d.get("evidence_revision", record.evidence_revision)
                        == record.evidence_revision
                    },
                    "materials": {
                        d["sample_id"]: d["material"]
                        for d in record.decisions
                        if d.get("sample_id")
                        and d.get("material")
                        and d["status"] == "RESOLVED"
                        and d.get("evidence_revision", record.evidence_revision)
                        == record.evidence_revision
                    },
                }
            row.status = "PUBLISHED" if record.receipt else record.state
            row.current_run_id = record.turn_id
            row.draft_revision = record.revision
            row.intent_revision = record.intent_revision
            row.draft = record.draft
            row.draft_digest = digest(record.draft)
            row.context = context
            row.wait_reason = record.waiting_reason
            row.evidence_revision = record.evidence_revision
            row.row_version += 1
            row.updated_at = datetime.fromisoformat(record.updated_at)
            row.current_validation_run_id = await self._validation(db, record, row)
            event_id = uuid4().hex
            await save_task_children(db, record, row, event_id)
            row.published_release_id = record.receipt.get("release_id") if record.receipt else None
            if record.receipt or record.state == "CLOSED":
                row.closed_at = row.closed_at or datetime.now(UTC)
                row.expires_at = row.closed_at + timedelta(days=30)
            else:
                row.closed_at, row.expires_at = None, None
            message_count = await db.scalar(
                select(func.count())
                .select_from(TaskEvent)
                .where(TaskEvent.task_id == record.task_id, TaskEvent.kind == "MESSAGE")
            )
            prefix = record.messages[: record._persisted_window_size]
            if message_count != record._persisted_message_count or (
                record._persisted_window_digest is not None
                and digest(prefix) != record._persisted_window_digest
            ):
                raise ConfigurationError("STALE_MESSAGES", "已有消息不可覆盖")
            new_messages = record.messages[record._persisted_window_size :]
            stored_messages = []
            for message in new_messages:
                stored = dict(message)
                stored["_run_id"] = record.turn_id
                content = stored.get("content") or ""
                if stored["role"] == "tool" and len(content) > 65536:
                    if len(content.encode("utf8")) > 4_000_000:
                        raise ConfigurationError(
                            "TOOL_RESULT_LIMIT", "工具结果超过工件上限，请缩小查询范围"
                        )
                    chunks = [content[i : i + 48000] for i in range(0, len(content), 48000)]
                    for index, chunk in enumerate(chunks):
                        db.add(
                            TaskEvent(
                                task_id=record.task_id,
                                kind="TOOL_RESULT_CHUNK",
                                tool_call_id=stored["tool_call_id"],
                                content=chunk,
                                payload_schema_version="1.0",
                                payload={"index": index},
                            )
                        )
                    stored["_result_chunks"] = {"count": len(chunks), "digest": digest(content)}
                    stored["content"] = result_reference(stored["tool_call_id"], content)
                stored_messages.append(stored)
                db.add(
                    TaskEvent(
                        id=message.get("_event_id", uuid4().hex),
                        task_id=record.task_id,
                        run_id=record.turn_id,
                        kind="MESSAGE",
                        role=message["role"].upper(),
                        content=stored.get("content") or "",
                        tool_call_id=message.get("tool_call_id"),
                        tool_name=next(
                            (
                                call["function"]["name"]
                                for item in record.messages
                                for call in item.get("tool_calls", [])
                                if call["id"] == message.get("tool_call_id")
                            ),
                            None,
                        ),
                        payload_schema_version="1.0",
                        payload={
                            k: v
                            for k, v in stored.items()
                            if k not in {"role", "content", "tool_call_id", "_run_id"}
                        },
                    )
                )
            db.add(
                TaskEvent(
                    id=event_id,
                    task_id=record.task_id,
                    run_id=record.turn_id,
                    kind="PROGRESS",
                    payload_schema_version="1.0",
                    payload={"event": event, "data": {"turn_id": record.turn_id, **(data or {})}},
                )
            )
            if request:
                await self._request(db, request)
        record.row_version = row.row_version
        # All entry points share this committed event boundary, including manual commands
        # and publication. Payload content and tool arguments never enter execution logs.
        safe = {
            key: data[key]
            for key in (
                "name",
                "call_id",
                "model_call_id",
                "status",
                "code",
                "duration_ms",
                "usage",
                "model_calls",
                "reused_from",
                "changes_count",
                "arguments_digest",
            )
            if data and key in data
        }
        logging.getLogger(__name__).info(
            "event=%s task_id=%s turn_id=%s revision=%s details=%s",
            event,
            record.task_id,
            record.turn_id,
            record.revision,
            safe,
        )
        record._persisted_message_count += len(new_messages)
        record.messages[record._persisted_window_size :] = stored_messages
        record._persisted_window_size = len(record.messages)
        record._persisted_window_digest = digest(record.messages)

    async def cached(self, scope, request_id, fingerprint):
        operation, scope_key = request_scope(scope)
        async with self.database.session(readonly=True) as db:
            row = await db.scalar(
                select(Idempotency).where(
                    *idempotency_filter(operation, scope_key, request_id),
                    Idempotency.expires_at > func.now(),
                )
            )
            if row and row.request_digest != fingerprint:
                raise ConfigurationError("IDEMPOTENCY_CONFLICT", "同一个请求 ID 不能用于不同内容")
            if (
                row
                and row.result_ref.get("capture_id")
                and await db.get(Capture, row.result_ref["capture_id"]) is None
            ):
                raise ConfigurationError(
                    "RESOURCE_EXPIRED", "原采集已过期，不会重复执行旧请求", 410
                )
            if (
                row
                and row.result_ref.get("task_id")
                and await db.get(ConfigurationTask, row.result_ref["task_id"]) is None
            ):
                raise ConfigurationError(
                    "RESOURCE_EXPIRED", "原会话已过期，不会重复执行旧请求", 410
                )
            return row.result_ref if row else None

    async def events(self, task_id, after=0, *, before=None, limit=200, include_messages=True):
        async with self.database.session(readonly=True) as db:
            rows = (
                await db.scalars(
                    select(TaskEvent)
                    .where(
                        TaskEvent.task_id == task_id,
                        TaskEvent.seq > after,
                        *([TaskEvent.seq < before] if before else []),
                        TaskEvent.kind.in_(
                            ["MESSAGE", "PROGRESS"] if include_messages else ["PROGRESS"]
                        ),
                    )
                    .order_by(TaskEvent.seq.desc() if before is not None else TaskEvent.seq)
                    .limit(limit)
                )
            ).all()
            if before is not None:
                rows = list(reversed(rows))
            return [
                {
                    "seq": r.seq,
                    "run_id": r.run_id,
                    "created_at": r.created_at.isoformat(),
                    "type": r.payload["event"],
                    "data": r.payload["data"],
                }
                if r.kind == "PROGRESS"
                else {
                    "seq": r.seq,
                    "run_id": r.run_id,
                    "created_at": r.created_at.isoformat(),
                    "type": "message",
                    "data": event_message(r),
                }
                for r in rows
            ]

    async def messages(self, task_id, after=0, limit=100):
        async with self.database.session(readonly=True) as db:
            rows = (
                await db.scalars(
                    select(TaskEvent)
                    .where(
                        TaskEvent.task_id == task_id,
                        TaskEvent.kind == "MESSAGE",
                        TaskEvent.seq > after,
                    )
                    .order_by(TaskEvent.seq)
                    .limit(min(max(limit, 1), 200))
                )
            ).all()
            return [{"seq": r.seq, **event_message(r)} for r in rows]

    async def cleanup(self, saver):
        """Expire closed work without ever removing a published validation."""
        async with self.database.session(readonly=True) as db:
            ids = (
                await db.scalars(
                    select(ConfigurationTask.id)
                    .where(
                        ConfigurationTask.expires_at <= func.now(),
                        ConfigurationTask.status.in_(["PUBLISHED", "CLOSED"]),
                        ~ConfigurationTask.current_run_id.in_(
                            select(TaskRun.id).where(TaskRun.status.in_(["RUNNING", "QUEUED"]))
                        ),
                    )
                    .limit(100)
                )
            ).all()
        for sid in ids:
            await saver.adelete_thread(sid)
            await self.delete(sid)
        async with self.database.session(write=True) as db:
            await db.execute(delete(Idempotency).where(Idempotency.expires_at <= func.now()))
            await db.execute(
                delete(Capture).where(
                    Capture.expires_at <= func.now(),
                    ~select(TaskCapture.task_id)
                    .where(TaskCapture.capture_id == Capture.id)
                    .exists(),
                )
            )
            await db.execute(
                delete(ValidationRun).where(
                    ValidationRun.expires_at <= func.now(),
                    ~select(Release.id)
                    .where(Release.validation_run_id == ValidationRun.id)
                    .exists(),
                    ~select(ConfigurationTask.id)
                    .where(ConfigurationTask.current_validation_run_id == ValidationRun.id)
                    .exists(),
                )
            )

    async def idle_sessions(self):
        async with self.database.session(readonly=True) as db:
            return list(
                await db.scalars(
                    select(ConfigurationTask.id)
                    .where(
                        ConfigurationTask.updated_at < datetime.now(UTC) - timedelta(days=30),
                        ConfigurationTask.status.not_in(["PUBLISHED", "CLOSED"]),
                        ~ConfigurationTask.current_run_id.in_(
                            select(TaskRun.id).where(TaskRun.status.in_(["RUNNING", "QUEUED"]))
                        ),
                    )
                    .limit(100)
                )
            )

    async def delete(self, task_id):
        async with self.database.session(write=True) as db:
            row = await db.get(ConfigurationTask, task_id, with_for_update=True)
            if row is None:
                raise ConfigurationError("NOT_FOUND", "配置会话不存在", 404)
            run = await db.get(TaskRun, row.current_run_id) if row.current_run_id else None
            interaction = (await load_task_children(db, row))["pending"]
            if (run and run.status in {"RUNNING", "QUEUED"}) or (
                not row.published_release_id and (interaction or {}).get("confirmed")
            ):
                raise ConfigurationError("SESSION_IN_USE", "请先完成或取消执行")
            await db.execute(delete(TaskEvent).where(TaskEvent.task_id == task_id))
            await db.delete(row)


class AsyncConfigurationStore:
    def __init__(self, database):
        self.database = database

    async def get(self):
        async with self.database.session(readonly=True, repeatable=True) as db:
            return await read_state_async(db)

    async def receipt(self, operation_id):
        async with self.database.session(readonly=True) as db:
            return extract_receipt((await db.execute(receipt_query(operation_id))).first())

    async def publish(self, state, receipt, record):
        try:
            async with self.database.session(write=True) as db:
                row = await db.get(ConfigurationTask, record.task_id, with_for_update=True)
                if (
                    row is None
                    or row.draft_revision != record.revision
                    or row.intent_revision != record.intent_revision
                    or row.draft_digest != digest(record.draft)
                ):
                    raise ConfigurationError("STALE_REVISION", "发布候选已变化")
                proof = await db.get(ValidationRun, row.current_validation_run_id)
                if (
                    proof is None
                    or proof.session_id != record.task_id
                    or proof.candidate_page_id != row.page_id
                    or (
                        proof.publish_valid_until is not None
                        and proof.publish_valid_until <= datetime.now(UTC)
                    )
                    or proof.candidate_digest != record.report["candidate_digest"]
                    or proof.candidate_revision != record.revision
                    or proof.scene_digest
                    != (
                        digest(record.snapshot.model_dump(mode="json")) if record.snapshot else None
                    )
                    or (
                        proof.intent_digest is not None
                        and proof.intent_digest != intent_digest(record)
                    )
                    or not record.pending
                    or not record.pending.get("confirmed")
                ):
                    raise ConfigurationError("STALE_VALIDATION", "发布需要精确候选验证及人工确认")
                # A different page may have been published during investigation. The
                # kernel has compiled the merged current manifest before this short
                # transaction; the head CAS still rejects a race after that snapshot.
                if proof.base_release_id != state.release_id:
                    refreshed = ValidationRun(
                        **{
                            column.name: getattr(proof, column.name)
                            for column in ValidationRun.__table__.columns
                            if column.name
                            not in {
                                "id",
                                "base_release_id",
                                "manifest_digest",
                                "verification_context",
                            }
                        },
                        id=uuid4().hex,
                        base_release_id=state.release_id,
                        manifest_digest=digest(
                            {p: digest(state.source(p, v)) for p, v in sorted(state.active.items())}
                        ),
                        verification_context={
                            **proof.verification_context,
                            "merged_from_validation": proof.id,
                        },
                    )
                    db.add(refreshed)
                    await db.flush()
                    proof = refreshed
                    row.current_validation_run_id = proof.id
                rid = await publish_state_async(
                    db,
                    state,
                    operation_id=receipt["operation_id"],
                    source="ASSISTANT",
                    receipt=receipt,
                    operation_type="ROLLBACK" if record.activation_only else "PUBLISH",
                    source_session=record.task_id,
                    validation_id=proof.id,
                    confirmed_by="local-user",
                )
                row.published_release_id = rid
                row.status = "PUBLISHED"
            return {**receipt, "state": "PUBLISHED", "release_id": rid}
        except PublicationConflict as exc:
            raise ConfigurationError(exc.code, str(exc)) from exc
