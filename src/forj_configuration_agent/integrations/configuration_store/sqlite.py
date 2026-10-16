"""One atomic session update includes its events and idempotency receipt."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from forj_configuration_agent.contracts.models import AgentError, CapturedPage, SessionRecord

logger = logging.getLogger(__name__)


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1, 2}:
                raise AgentError("STORE_VERSION", "会话数据库版本不兼容，请使用匹配版本的程序或备份恢复", 503)
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(scope TEXT, id TEXT, fingerprint TEXT,
                    result TEXT, PRIMARY KEY(scope,id));
                CREATE TABLE IF NOT EXISTS captures(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                PRAGMA user_version=2;
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def get(self, session_id: str) -> SessionRecord:
        with self.connection() as db:
            row = db.execute("SELECT body FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise AgentError("NOT_FOUND", "配置会话不存在", 404)
        return SessionRecord.model_validate_json(row[0])

    def get_capture(self, capture_id: str) -> CapturedPage:
        with self.connection() as db:
            row = db.execute("SELECT body FROM captures WHERE id=?", (capture_id,)).fetchone()
        if not row:
            raise AgentError("CAPTURE_NOT_FOUND", "页面采集不存在，请回到业务页面重新采集", 404)
        return CapturedPage.model_validate_json(row[0])

    def list_captures(self) -> list[dict]:
        with self.connection() as db:
            return [{"capture_id": c.capture_id, "title": c.snapshot.title, "created_at": c.created_at,
                     "field_count": len(c.snapshot.fields), "sample_count": len(c.snapshot.samples)}
                    for (body,) in db.execute("SELECT body FROM captures ORDER BY rowid DESC LIMIT 30")
                    for c in [CapturedPage.model_validate_json(body)]]

    def put_capture(self, capture: CapturedPage, request_id: str, fingerprint: str):
        result = {"capture_id": capture.capture_id}
        with self.connection() as db:
            db.execute("INSERT INTO captures VALUES(?,?)", (capture.capture_id, capture.model_dump_json()))
            db.execute("INSERT INTO requests VALUES(?,?,?,?)",
                       ("capture", request_id, fingerprint, json.dumps(result)))
        return result

    def delete_capture(self, capture_id: str):
        self.get_capture(capture_id)
        with self.connection() as db:
            if db.execute("SELECT 1 FROM sessions WHERE json_extract(body,'$.capture_id')=? LIMIT 1",
                          (capture_id,)).fetchone():
                raise AgentError("CAPTURE_IN_USE", "该采集仍关联配置会话，请先清理相应会话")
            db.execute("DELETE FROM requests WHERE scope='capture' AND json_extract(result,'$.capture_id')=?", (capture_id,))
            db.execute("DELETE FROM captures WHERE id=?", (capture_id,))

    def list(self) -> list[dict]:
        with self.connection() as db:
            return [{"session_id": r.session_id, "state": r.state,
                     "title": r.snapshot.title, "updated_at": r.updated_at}
                    for (body,) in db.execute("SELECT body FROM sessions ORDER BY rowid DESC")
                    for r in [SessionRecord.model_validate_json(body)]]

    def put(self, record: SessionRecord, event: str = "progress", data: dict | None = None,
            request: tuple[str, str, str, dict] | None = None):
        record.updated_at = datetime.now(UTC).isoformat()
        with self.connection() as db:
            db.execute("INSERT INTO sessions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                       (record.session_id, record.model_dump_json()))
            db.execute("INSERT INTO events(session_id,kind,body) VALUES(?,?,?)",
                       (record.session_id, event, json.dumps(data or {}, ensure_ascii=False)))
            if request:
                scope, request_id, fingerprint, result = request
                db.execute("INSERT INTO requests VALUES(?,?,?,?)",
                           (scope, request_id, fingerprint, json.dumps(result)))
        logger.info("event=%s session_id=%s turn_id=%s revision=%d state=%s status=%s",
                    event, record.session_id, record.turn_id, record.revision, record.state, record.turn_status)

    def cached(self, scope: str, request_id: str, fingerprint: str):
        with self.connection() as db:
            row = db.execute("SELECT fingerprint,result FROM requests WHERE scope=? AND id=?",
                             (scope, request_id)).fetchone()
        if row and row[0] != fingerprint:
            raise AgentError("IDEMPOTENCY_CONFLICT", "同一个请求 ID 不能用于不同内容")
        return json.loads(row[1]) if row else None

    def events(self, session_id: str, after: int) -> list[dict]:
        with self.connection() as db:
            return [{"seq": seq, "type": kind, "data": json.loads(body)}
                    for seq, kind, body in db.execute(
                        "SELECT seq,kind,body FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT 200",
                        (session_id, after))]

    def interrupt_running(self):
        for item in self.list():
            record = self.get(item["session_id"])
            if record.turn_status in {"QUEUED", "RUNNING"}:
                record.turn_status = "INTERRUPTED"
                record.error = "上次执行因服务退出中断，可以恢复；模型调用可能产生额外用量"
                self.put(record, "turn_stopped", {"status": "INTERRUPTED"})

    def delete(self, session_id: str):
        record = self.get(session_id)
        if (record.turn_status in {"RUNNING", "QUEUED"} or record.receipt
                or (record.pending and record.pending.get("confirmed"))):
            raise AgentError("SESSION_IN_USE", "请先完成或取消执行；已确认保存的会话需保留对账依据")
        with self.connection() as db:
            db.execute("DELETE FROM events WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM requests WHERE scope LIKE ?", (session_id + ":%",))
            db.execute("DELETE FROM requests WHERE json_extract(result,'$.session_id')=?", (session_id,))
            db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
