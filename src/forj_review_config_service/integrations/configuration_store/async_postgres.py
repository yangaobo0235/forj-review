"""Asynchronous session and page storage; every mutation is executor-fenced."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from forj_review_config_service.contracts.models import (
    CapturedPage,
    ConfigurationError,
    SessionRecord,
)
from forj_review_config_service.integrations.async_database import AsyncDatabase


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

    async def get(self, session_id: str) -> SessionRecord:
        async with self.database.connect() as db:
            row = await (
                await db.execute("SELECT body FROM sessions WHERE id=%s", (session_id,))
            ).fetchone()
        if row is None:
            raise ConfigurationError("NOT_FOUND", "配置会话不存在", 404)
        return SessionRecord.model_validate(row[0])

    async def get_capture(self, capture_id: str) -> CapturedPage:
        async with self.database.connect() as db:
            row = await (
                await db.execute("SELECT body FROM captures WHERE id=%s", (capture_id,))
            ).fetchone()
        if row is None:
            raise ConfigurationError("CAPTURE_NOT_FOUND", "页面采集不存在，请回到业务页面重新采集", 404)
        return CapturedPage.model_validate(row[0])

    async def list_captures(self) -> list[dict]:
        async with self.database.connect() as db:
            rows = await (
                await db.execute("SELECT body FROM captures ORDER BY position DESC LIMIT 30")
            ).fetchall()
        return [
            {
                "capture_id": c.capture_id,
                "title": c.snapshot.title,
                "created_at": c.created_at,
                "field_count": len(c.snapshot.fields),
                "sample_count": len(c.snapshot.samples),
            }
            for (body,) in rows
            for c in [CapturedPage.model_validate(body)]
        ]

    async def put_capture(self, capture: CapturedPage, request_id: str, fingerprint: str):
        result = {"capture_id": capture.capture_id}
        async with self.database.connect(write=True) as db:
            await db.execute(
                "INSERT INTO captures(id,body) VALUES(%s,%s)",
                (capture.capture_id, Jsonb(capture.model_dump(mode="json"))),
            )
            await db.execute(
                "INSERT INTO requests(scope,id,fingerprint,result) VALUES(%s,%s,%s,%s)",
                ("capture", request_id, fingerprint, Jsonb(result)),
            )
        return result

    async def delete_capture(self, capture_id: str):
        async with self.database.connect(write=True) as db:
            if not await (
                await db.execute("SELECT 1 FROM captures WHERE id=%s FOR UPDATE", (capture_id,))
            ).fetchone():
                raise ConfigurationError("CAPTURE_NOT_FOUND", "页面采集不存在", 404)
            if await (
                await db.execute(
                    "SELECT 1 FROM sessions WHERE capture_id=%s LIMIT 1", (capture_id,)
                )
            ).fetchone():
                raise ConfigurationError("CAPTURE_IN_USE", "该采集仍关联配置会话，请先清理相应会话")
            await db.execute("DELETE FROM requests WHERE capture_id=%s", (capture_id,))
            await db.execute("DELETE FROM captures WHERE id=%s", (capture_id,))

    async def list(self) -> list[dict]:
        async with self.database.connect() as db:
            rows = await (
                await db.execute("SELECT body FROM sessions ORDER BY position DESC")
            ).fetchall()
        return [
            {
                "session_id": r.session_id,
                "state": r.state,
                "title": r.snapshot.title,
                "updated_at": r.updated_at,
            }
            for (body,) in rows
            for r in [SessionRecord.model_validate(body)]
        ]

    async def put(
        self,
        record: SessionRecord,
        event: str = "progress",
        data: dict | None = None,
        request: tuple[str, str, str, dict] | None = None,
    ):
        record.updated_at = datetime.now(UTC).isoformat()
        async with self.database.connect(write=True) as db:
            await db.execute(
                "INSERT INTO sessions(id,body) VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (record.session_id, Jsonb(record.model_dump(mode="json"))),
            )
            await db.execute(
                "INSERT INTO events(session_id,kind,body) VALUES(%s,%s,%s)",
                (record.session_id, event, Jsonb(data or {})),
            )
            if request:
                scope, request_id, fingerprint, result = request
                await db.execute(
                    "INSERT INTO requests(scope,id,fingerprint,result) VALUES(%s,%s,%s,%s)",
                    (scope, request_id, fingerprint, Jsonb(result)),
                )

    async def cached(self, scope: str, request_id: str, fingerprint: str):
        async with self.database.connect() as db:
            row = await (
                await db.execute(
                    "SELECT fingerprint,result FROM requests WHERE scope=%s AND id=%s",
                    (scope, request_id),
                )
            ).fetchone()
        if row and row[0] != fingerprint:
            raise ConfigurationError("IDEMPOTENCY_CONFLICT", "同一个请求 ID 不能用于不同内容")
        return row[1] if row else None

    async def events(self, session_id: str, after: int) -> list[dict]:
        async with self.database.connect() as db:
            return [
                {"seq": seq, "type": kind, "data": body}
                for seq, kind, body in await (
                    await db.execute(
                        "SELECT seq,kind,body FROM events WHERE session_id=%s AND seq>%s ORDER BY seq LIMIT 200",
                        (session_id, after),
                    )
                ).fetchall()
            ]

    async def delete(self, session_id: str):
        async with self.database.connect(write=True) as db:
            row = await (
                await db.execute("SELECT body FROM sessions WHERE id=%s FOR UPDATE", (session_id,))
            ).fetchone()
            if not row:
                raise ConfigurationError("NOT_FOUND", "配置会话不存在", 404)
            record = SessionRecord.model_validate_json(json.dumps(row[0]))
            if (
                record.turn_status in {"RUNNING", "QUEUED"}
                or record.receipt
                or (record.pending and record.pending.get("confirmed"))
            ):
                raise ConfigurationError(
                    "SESSION_IN_USE", "请先完成或取消执行；已确认保存的会话需保留对账依据"
                )
            await db.execute("DELETE FROM events WHERE session_id=%s", (session_id,))
            await db.execute("DELETE FROM requests WHERE session_id=%s", (session_id,))
            await db.execute("DELETE FROM sessions WHERE id=%s", (session_id,))


class AsyncPageSnapshots:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def get_versioned(self) -> tuple[int, dict[str, str]]:
        async with self.database.connect() as db:
            row = await (
                await db.execute("SELECT revision,files FROM page_snapshots WHERE id=1")
            ).fetchone()
        if row is None:
            raise ValueError("PostgreSQL 页面配置快照缺失，请核对数据库与配置空间")
        return row[0], row[1]

    async def get(self) -> dict[str, str]:
        return (await self.get_versioned())[1]

    async def publish(self, expected_revision: int, files: dict[str, str]) -> None:
        """Publish prevalidated content with one short fenced compare-and-swap."""
        async with self.database.connect(write=True) as db:
            cursor = await db.execute(
                "UPDATE page_snapshots SET files=%s,revision=revision+1 WHERE id=1 AND revision=%s",
                (Jsonb(files), expected_revision),
            )
            if cursor.rowcount != 1:
                raise ConfigurationError("BASELINE_CHANGED", "配置基线发生变化，请重新验证候选")
