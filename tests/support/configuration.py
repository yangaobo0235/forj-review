"""Synthetic configuration workflow setup shared by tests, never by application code."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from forj_review_config_service.config.settings import Settings
from forj_review_config_service.contracts.models import SessionRecord
from forj_review_config_service.integrations.review_kernel.snapshot import SnapshotKernel
from tests.support.postgres import read_page_state

FIXTURES = Path(__file__).parents[1] / "fixtures"


SNAPSHOT = {
    "snapshot_id": "snapshot",
    "page_instance_id": "page",
    "page_url": "https://example.test/permit",
    "structure_digest": "structure",
    "title": "Synthetic permit",
    "fields": [],
    "samples": [],
}


class FakeModel:
    def __init__(self, replies=None):
        self.replies = iter(replies or [{"role": "assistant", "content": "已读取要求，草稿待配置"}])

    async def complete(self, messages, tools):
        return next(self.replies)


def call(name, args=None, call_id="call1"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args or {})},
            }
        ],
    }


class OfflineSettings(Settings):
    pages_dir: Path = Path("pages")


@pytest.fixture
def settings(tmp_path, database):
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "permit.json").write_text(
        (FIXTURES / "permit.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    cases = tmp_path / "cases"
    cases.mkdir()
    from forj_review_service.integrations.storage.configuration import publish_state

    with database.connect() as db:
        publish_state(db, read_page_state(pages), operation_id="permitfixture")
    return OfflineSettings(
        state_dir=tmp_path / "state",
        pages_dir=pages,
        cases_dir=cases,
        database_url=database.url,
        database_schema=database.schema,
        review_url="http://127.0.0.1:1",
    )


def headers():
    return {"origin": "chrome-extension://" + "a" * 32}


def create(client, auth, request_id="create-1"):
    response = client.post(
        "/api/review-config/sessions",
        headers=auth,
        json={"client_request_id": request_id, "snapshot": SNAPSHOT, "page_id": "example_permit"},
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def wait(client, sid, auth, status=None):

    async def check():
        for _ in range(200):
            record = await client.app.state.service.store.get(sid)
            if record.turn_status not in {"QUEUED", "RUNNING"}:
                return record
            await asyncio.sleep(0.02)
        raise AssertionError("graph did not stop")

    record = client.portal.call(check)
    if status:
        assert record.turn_status == status, record.error
    return record


def candidate(settings):
    from forj_review_service.integrations.database import Database
    from forj_review_service.integrations.storage.configuration import read_state

    with Database(settings.database_url, settings.database_schema).connect() as db:
        state = read_state(db)
    kernel = SnapshotKernel(
        state, settings.cases_dir, settings.review_url
    )
    draft = kernel.context("example_permit")["editable"]
    draft["version"] = "2.0"
    record = SessionRecord(
        session_id="test", snapshot=SNAPSHOT, baseline_digest=kernel.baseline(), draft=draft
    )
    record.report = {**kernel.validate(record), "report_id": uuid4().hex,
                     "revision": record.revision, "ok": True, "blockers": [], "replays": []}
    return (kernel, record)


async def database_candidate(kernel, store, *, sid="test", rollback=None):
    """Synthetic completed evidence for storage tests, never a browser-proof test."""
    draft = (await kernel.rollback_draft("example_permit", rollback) if rollback
             else (await kernel.context("example_permit"))["editable"])
    if not rollback:
        draft["version"] = "2.0"
    record = SessionRecord(session_id=sid, snapshot=SNAPSHOT, draft=draft,
                           activation_only=bool(rollback), baseline_digest=await kernel.baseline())
    await store.put(record)
    record.report = {**(await kernel.validate(record)), "report_id": uuid4().hex,
                     "revision": record.revision, "ok": True, "blockers": [], "replays": []}
    record.pending = {"id": uuid4().hex, "kind": "confirmation", "confirmed": True,
                      "candidate_digest": record.report["candidate_digest"]}
    await store.put(record)
    return record


class HappyModel:
    """The model is scripted; compilation, graph replay, browser binding and commits are real."""

    def __init__(self):
        self.step = 0

    async def complete(self, messages, tools):
        self.step += 1
        context = json.loads(messages[0]["content"].split("当前工作状态：", 1)[1])
        if self.step == 1:
            return call(
                "edit_draft",
                {
                    "expected_revision": 0,
                    "operations": [
                        {
                            "op": "upsert_field",
                            "value": {"key": "permit.vin", "label": "Synthetic updated VIN"},
                        }
                    ],
                },
                "edit",
            )
        if self.step == 2:
            rid = context["requirements"][0]["requirement_id"]
            return call(
                "record_decision",
                {
                    "requirement_id": rid,
                    "status": "RESOLVED",
                    "reason": "按明确要求修改标题",
                    "evidence_refs": [rid],
                },
                "decision",
            )
        if self.step == 3:
            return call("request_collection_trial", call_id="browser")
        if self.step == 4:
            return call("run_replay", {"case_set_id": "local/permit-v2"}, "replay")
        if self.step == 5:
            return call("submit_candidate", call_id="submit")
        raise AssertionError("unexpected extra model call")


def browser_evidence(kernel, record):
    from forj_review_config_service.application.validation import trial_binding

    preview = kernel.validate(record)
    return {
        "kind": "collection_trial",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        "ok": True,
        "revision": record.revision,
        "candidate_digest": preview["candidate_digest"],
        "binding": trial_binding(record),
        "facts": {
            "fields": {f["key"]: 1 for f in preview["manifest"]["fields"]},
            "control_kinds": {
                key: "text" for key in preview["manifest"].get("writable_fields", [])
            },
            "samples": {},
            "matched_page_id": record.draft["page_id"],
            "diagnostics": [],
        },
    }


class NoModel:
    async def complete(self, *args):
        raise AssertionError("manual maintenance must never call the model")

    async def analyze(self, *args):
        raise AssertionError("manual maintenance must never analyze images with a model")
