"""Fenced workflow storage. Events own messages; reports and releases own evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, func, select

from forj_review_config_service.contracts.models import (
    CapturedPage,
    ConfigurationError,
    SessionRecord,
)
from forj_review_config_service.integrations.async_database import AsyncDatabase
from forj_review_config_service.integrations.review_kernel.storage import (
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
    ConfigurationSession,
    Head,
    Idempotency,
    Page,
    Release,
    ReleaseItem,
    SessionEvent,
    ValidationRun,
)
from forj_review_database.validation import ValidationReport

SNAPSHOT_COLUMNS = {"snapshot_id", "page_instance_id", "page_url", "title", "structure_digest"}


def request_scope(scope):
    return tuple(reversed(scope.rsplit(":", 1))) if ":" in scope else (scope, "global")


def capture_from_row(row):
    return CapturedPage.model_validate(
        dict(
            capture_id=row.id,
            extension_id=row.extension_id,
            snapshot={
                **row.snapshot,
                **{k: getattr(row, k) for k in SNAPSHOT_COLUMNS},
                "schema_version": row.snapshot_schema_version,
            },
            created_at=row.created_at.isoformat(),
        )
    )


def idempotency_filter(operation, scope_key, request_id):
    return (
        Idempotency.operation_type == operation,
        Idempotency.scope_key == scope_key,
        Idempotency.request_id == request_id,
    )


def event_message(row):
    return {
        **(row.payload or {}),
        "role": row.role.lower(),
        "content": row.content or "",
        **({"tool_call_id": row.tool_call_id} if row.tool_call_id else {}),
    }


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


class AsyncSessionStore:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def interrupt_running(self):
        for item in await self.list():
            record = await self.get(item["session_id"])
            if record.turn_status in {"QUEUED", "RUNNING"}:
                record.turn_status = "INTERRUPTED"
                record.error = "上次执行因服务退出中断，可以恢复；模型调用可能产生额外用量"
                await self.put(record, "turn_stopped", {"status": "INTERRUPTED"})

    async def _record(self, db, row):
        capture = await db.get(Capture, row.capture_id) if row.capture_id else None
        if capture is None:
            raise ConfigurationError("CAPTURE_NOT_FOUND", "会话现场已过期，请重新采集", 410)
        messages = (
            await db.scalars(
                select(SessionEvent)
                .where(SessionEvent.session_id == row.id, SessionEvent.kind == "MESSAGE")
                .order_by(SessionEvent.seq)
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
        return SessionRecord.model_validate(
            {
                **row.workflow,
                "session_id": row.id,
                "capture_id": row.capture_id,
                "snapshot": capture_from_row(capture).snapshot,
                "baseline_digest": row.baseline_digest,
                "state": row.state,
                "turn_status": row.turn_status,
                "revision": row.revision,
                "draft": row.draft,
                "messages": [event_message(m) for m in messages],
                "report": report,
                "receipt": receipt,
                "updated_at": row.updated_at.isoformat(),
            }
        )

    async def get(self, session_id):
        async with self.database.session(readonly=True, repeatable=True) as db:
            row = await db.get(ConfigurationSession, session_id)
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
            return capture_from_row(row)

    async def list_captures(self):
        async with self.database.session(readonly=True) as db:
            rows = (
                await db.execute(
                    select(
                        Capture.id,
                        Capture.title,
                        Capture.created_at,
                        func.jsonb_array_length(Capture.snapshot["fields"]),
                        func.jsonb_array_length(Capture.snapshot["samples"]),
                    )
                    .order_by(Capture.created_at.desc(), Capture.id.desc())
                    .limit(30)
                )
            ).all()
            return [
                dict(
                    capture_id=r[0],
                    title=r[1],
                    created_at=r[2].isoformat(),
                    field_count=r[3],
                    sample_count=r[4],
                )
                for r in rows
            ]

    def _capture(self, capture):
        data = capture.snapshot.model_dump(mode="json")
        metadata = {k: data.pop(k) for k in SNAPSHOT_COLUMNS}
        schema = data.pop("schema_version")
        return Capture(
            id=capture.capture_id,
            extension_id=capture.extension_id,
            **metadata,
            snapshot_schema_version=schema,
            snapshot=data,
            created_by="local",
            created_at=datetime.fromisoformat(capture.created_at),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )

    async def put_capture(self, capture, request_id, fingerprint):
        result = {"capture_id": capture.capture_id}
        async with self.database.session(write=True) as db:
            db.add(self._capture(capture))
            await self._request(db, ("capture", request_id, fingerprint, result))
        return result

    async def delete_capture(self, capture_id):
        async with self.database.session(write=True) as db:
            if await db.get(Capture, capture_id) is None:
                raise ConfigurationError("CAPTURE_NOT_FOUND", "页面采集不存在", 404)
            if await db.scalar(
                select(ConfigurationSession.id)
                .where(ConfigurationSession.capture_id == capture_id)
                .limit(1)
            ):
                raise ConfigurationError("CAPTURE_IN_USE", "该采集仍关联配置会话，请先清理相应会话")
            await db.execute(delete(Capture).where(Capture.id == capture_id))

    async def list(self):
        async with self.database.session(readonly=True) as db:
            rows = (
                await db.execute(
                    select(
                        ConfigurationSession.id,
                        ConfigurationSession.state,
                        Page.page_code,
                        ConfigurationSession.updated_at,
                    )
                    .join(Page, Page.id == ConfigurationSession.page_id)
                    .order_by(
                        ConfigurationSession.updated_at.desc(), ConfigurationSession.id.desc()
                    )
                    .limit(200)
                )
            ).all()
            return [
                dict(session_id=r[0], state=r[1], title=r[2], updated_at=r[3].isoformat())
                for r in rows
            ]

    async def _request(self, db, request):
        scope, request_id, fingerprint, result = request
        operation, scope_key = request_scope(scope)
        # Results are receipts, never another copy of the candidate/report.
        result = {
            k: v
            for k, v in result.items()
            if k
            in {
                "session_id",
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
                scope_key=scope_key,
                request_id=request_id,
                request_digest=fingerprint,
                result=result,
            )
        )

    async def _validation(self, db, record, row):
        if not record.report:
            return None
        if "report_id" not in record.report or "ok" not in record.report:
            raise ConfigurationError("INCOMPLETE_VALIDATION", "预览不是完成验证，不能作为发布证据")
        rid = record.report["report_id"]
        existing = await db.get(ValidationRun, rid)
        if existing:
            if (
                existing.candidate_revision != record.revision
                or existing.candidate_digest != record.report.get("candidate_digest")
                or existing.report["summary"] != compact_report(record)["summary"]
                or existing.scene_digest != digest(record.snapshot.model_dump(mode="json"))
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
                session_id=record.session_id,
                purpose="ROLLBACK" if record.activation_only else "PUBLISH",
                base_release_id=row.base_release_id,
                candidate_page_id=row.page_id,
                candidate_revision=record.revision,
                candidate_digest=record.report.get("candidate_digest"),
                manifest_digest=manifest,
                digest_version="manifest-v1",
                scene_digest=digest(record.snapshot.model_dump(mode="json")),
                kernel_version=KERNEL_VERSION,
                kernel_fingerprint=KERNEL_VERSION,
                report_schema_version="1.0",
                status="PASSED" if record.report["ok"] else "FAILED",
                report=compact_report(record),
                performed_by="local",
                started_at=now,
                completed_at=now,
                expires_at=now + timedelta(days=30),
            )
        )
        await db.flush()
        return rid

    async def put(self, record, event="progress", data=None, request=None):
        record.updated_at = datetime.now(UTC).isoformat()
        async with self.database.session(write=True) as db:
            row = await db.scalar(
                select(ConfigurationSession)
                .where(ConfigurationSession.id == record.session_id)
                .with_for_update()
            )
            if row and record.revision < row.revision:
                raise ConfigurationError("STALE_REVISION", "数据库草稿已变化，禁止旧检查点覆盖")
            if row and record.revision == row.revision and row.draft_digest != digest(record.draft):
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
            if row and row.baseline_digest != record.baseline_digest:
                raise ConfigurationError("BASELINE_CHANGED", "会话基线不可在保存时更换")
            if row and row.page_id != await db.scalar(
                select(Page.id).where(Page.page_code == record.draft.get("page_id"))
            ):
                raise ConfigurationError("PAGE_IDENTITY_CHANGED", "会话不能更换页面身份")
            captured = await db.get(Capture, record.capture_id) if record.capture_id else None
            if record.capture_id and captured is None:
                raise ConfigurationError("CAPTURE_NOT_FOUND", "关联的页面采集不存在", 404)
            if not captured or capture_from_row(captured).snapshot != record.snapshot:
                # Existing create API can supply an inline capture. Persist it once.
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
            body = record.model_dump(mode="json")
            workflow = {
                k: v
                for k, v in body.items()
                if k
                not in {
                    "session_id",
                    "capture_id",
                    "snapshot",
                    "baseline_digest",
                    "state",
                    "turn_status",
                    "revision",
                    "draft",
                    "messages",
                    "report",
                    "receipt",
                    "updated_at",
                }
            }
            if row is None:
                current = await read_state_async(db)
                if current.baseline(KERNEL_VERSION) != record.baseline_digest:
                    raise ConfigurationError("BASELINE_CHANGED", "创建会话时配置基线已变化")
                base = await db.scalar(
                    select(ReleaseItem.page_version_id)
                    .join(Head, Head.release_id == ReleaseItem.release_id)
                    .where(ReleaseItem.page_id == page_id)
                )
                row = ConfigurationSession(
                    id=record.session_id,
                    page_id=page_id,
                    base_page_version_id=base,
                    base_release_id=await db.scalar(select(Head.release_id).where(Head.id == 1)),
                    baseline_digest=record.baseline_digest,
                    created_by="local",
                    workflow_schema_version="1.0",
                )
                # Insert before message events and validation FK updates.
                row.capture_id = record.capture_id
                row.state = record.state
                row.turn_status = record.turn_status
                row.revision = record.revision
                row.draft = record.draft
                row.draft_digest = digest(record.draft)
                row.workflow = workflow
                db.add(row)
                await db.flush()
            row.capture_id = record.capture_id
            row.state = "PUBLISHED" if record.receipt else record.state
            row.turn_status = record.turn_status
            row.revision = record.revision
            row.draft = record.draft
            row.draft_digest = digest(record.draft)
            row.workflow = workflow
            row.updated_at = datetime.fromisoformat(record.updated_at)
            row.current_validation_run_id = await self._validation(db, record, row)
            row.published_release_id = record.receipt.get("release_id") if record.receipt else None
            if record.receipt or record.state == "CLOSED":
                row.closed_at = row.closed_at or datetime.now(UTC)
                row.expires_at = row.closed_at + timedelta(days=30)
            previous = (
                await db.scalars(
                    select(SessionEvent)
                    .where(
                        SessionEvent.session_id == record.session_id, SessionEvent.kind == "MESSAGE"
                    )
                    .order_by(SessionEvent.seq)
                )
            ).all()
            if [event_message(m) for m in previous] != record.messages[: len(previous)]:
                raise ConfigurationError("STALE_MESSAGES", "已有消息不可覆盖")
            for message in record.messages[len(previous) :]:
                db.add(
                    SessionEvent(
                        session_id=record.session_id,
                        kind="MESSAGE",
                        role=message["role"].upper(),
                        content=message.get("content") or "",
                        tool_call_id=message.get("tool_call_id"),
                        payload_schema_version="1.0",
                        payload={
                            k: v
                            for k, v in message.items()
                            if k not in {"role", "content", "tool_call_id"}
                        },
                    )
                )
            db.add(
                SessionEvent(
                    session_id=record.session_id,
                    kind="PROGRESS",
                    payload_schema_version="1.0",
                    payload={"event": event, "data": data or {}},
                )
            )
            if request:
                await self._request(db, request)

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
                and row.result.get("capture_id")
                and await db.get(Capture, row.result["capture_id"]) is None
            ):
                raise ConfigurationError(
                    "RESOURCE_EXPIRED", "原采集已过期，不会重复执行旧请求", 410
                )
            if (
                row
                and row.result.get("session_id")
                and await db.get(ConfigurationSession, row.result["session_id"]) is None
            ):
                raise ConfigurationError(
                    "RESOURCE_EXPIRED", "原会话已过期，不会重复执行旧请求", 410
                )
            return row.result if row else None

    async def events(self, session_id, after):
        async with self.database.session(readonly=True) as db:
            rows = (
                await db.scalars(
                    select(SessionEvent)
                    .where(SessionEvent.session_id == session_id, SessionEvent.seq > after)
                    .order_by(SessionEvent.seq)
                    .limit(200)
                )
            ).all()
            return [
                {"seq": r.seq, "type": r.payload["event"], "data": r.payload["data"]}
                if r.kind == "PROGRESS"
                else {"seq": r.seq, "type": "message", "data": event_message(r)}
                for r in rows
            ]

    async def messages(self, session_id, after=0, limit=100):
        async with self.database.session(readonly=True) as db:
            rows = (
                await db.scalars(
                    select(SessionEvent)
                    .where(
                        SessionEvent.session_id == session_id,
                        SessionEvent.kind == "MESSAGE",
                        SessionEvent.seq > after,
                    )
                    .order_by(SessionEvent.seq)
                    .limit(min(max(limit, 1), 200))
                )
            ).all()
            return [{"seq": r.seq, **event_message(r)} for r in rows]

    async def cleanup(self, saver):
        """Expire closed work without ever removing a published validation."""
        async with self.database.session(readonly=True) as db:
            ids = (
                await db.scalars(
                    select(ConfigurationSession.id)
                    .where(
                        ConfigurationSession.expires_at <= func.now(),
                        ConfigurationSession.state.in_(["PUBLISHED", "CLOSED"]),
                        ConfigurationSession.turn_status.not_in(["RUNNING", "QUEUED"]),
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
                    ~select(ConfigurationSession.id)
                    .where(ConfigurationSession.capture_id == Capture.id)
                    .exists(),
                )
            )
            await db.execute(
                delete(ValidationRun).where(
                    ValidationRun.expires_at <= func.now(),
                    ~select(Release.id)
                    .where(Release.validation_run_id == ValidationRun.id)
                    .exists(),
                    ~select(ConfigurationSession.id)
                    .where(ConfigurationSession.current_validation_run_id == ValidationRun.id)
                    .exists(),
                )
            )

    async def delete(self, session_id):
        async with self.database.session(write=True) as db:
            row = await db.get(ConfigurationSession, session_id, with_for_update=True)
            if row is None:
                raise ConfigurationError("NOT_FOUND", "配置会话不存在", 404)
            if row.turn_status in {"RUNNING", "QUEUED"} or (
                not row.published_release_id
                and (row.workflow.get("pending") or {}).get("confirmed")
            ):
                raise ConfigurationError("SESSION_IN_USE", "请先完成或取消执行")
            await db.execute(delete(SessionEvent).where(SessionEvent.session_id == session_id))
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
                row = await db.get(ConfigurationSession, record.session_id, with_for_update=True)
                if (
                    row is None
                    or row.revision != record.revision
                    or row.draft_digest != digest(record.draft)
                ):
                    raise ConfigurationError("STALE_REVISION", "发布候选已变化")
                proof = await db.get(ValidationRun, row.current_validation_run_id)
                if (
                    proof is None
                    or proof.candidate_digest != record.report["candidate_digest"]
                    or proof.candidate_revision != record.revision
                    or proof.scene_digest != digest(record.snapshot.model_dump(mode="json"))
                    or not record.pending
                    or not record.pending.get("confirmed")
                ):
                    raise ConfigurationError("STALE_VALIDATION", "发布需要精确候选验证及人工确认")
                rid = await publish_state_async(
                    db,
                    state,
                    operation_id=receipt["operation_id"],
                    source="ASSISTANT",
                    receipt=receipt,
                    operation_type="ROLLBACK" if record.activation_only else "PUBLISH",
                    source_session=record.session_id,
                    validation_id=proof.id,
                    confirmed_by="local-user",
                )
                row.published_release_id = rid
                row.state = "PUBLISHED"
            return {**receipt, "state": "PUBLISHED", "release_id": rid}
        except PublicationConflict as exc:
            raise ConfigurationError(exc.code, str(exc)) from exc
