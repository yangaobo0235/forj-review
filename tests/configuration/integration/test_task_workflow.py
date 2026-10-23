"""Task routes and saved-scene recovery use the same durable workflow as publication."""

import json

import pytest
from fastapi.testclient import TestClient

from forj_review_config_service.bootstrap.application import create_app
from forj_review_config_service.contracts.models import ConfigurationError
from tests.support.configuration import SNAPSHOT, FakeModel, call, wait


def test_create_requires_scene_and_archive_restore_preserves_it(settings):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        rejected = client.post(
            "/api/review-config/tasks",
            json={"client_request_id": "missing", "page_id": "example_permit"},
        )
        assert rejected.status_code == 422 and rejected.json()["code"] == "CAPTURE_REQUIRED"
        sid = client.post(
            "/api/review-config/tasks",
            json={
                "client_request_id": "no-scene",
                "page_id": "example_permit",
                "snapshot": SNAPSHOT,
            },
        ).json()["task_id"]
        base = f"/api/review-config/tasks/{sid}"
        session = client.get(base).json()
        assert (
            session["snapshot"]["snapshot_id"] == SNAPSHOT["snapshot_id"] and session["capture_id"]
        )
        report = client.post(
            base + "/validate", json={"client_request_id": "validate", "expected_revision": 0}
        ).json()
        assert not report["ok"] and any("试采集" in b for b in report["blockers"])
        assert (
            client.post(
                base + "/archive", json={"client_request_id": "archive", "expected_revision": 0}
            ).status_code
            == 200
        )
        archived = client.get(base).json()
        assert archived["state"] == "CLOSED" and archived["revision"] == 0
        assert (
            client.post(
                base + "/restore", json={"client_request_id": "restore", "expected_revision": 0}
            ).status_code
            == 200
        )
        restored = client.get(base).json()
        assert (
            restored["state"] == "DRAFTING"
            and restored["waiting_reason"] is None
            and restored["snapshot"] == session["snapshot"]
        )
        assert restored["draft"] == session["draft"]


def test_code_route_requires_actual_investigation_and_exports_no_scene(settings):
    class InvestigatingModel(FakeModel):
        async def complete(self, messages, tools):
            reply = await super().complete(messages, tools)
            for c in reply.get("tool_calls", []):
                args = json.loads(c["function"]["arguments"])
                if c["function"]["name"] in {"assess_route", "record_decision"} and args[
                    "evidence_refs"
                ] == [
                    "caps",
                    "config",
                ]:
                    args["evidence_refs"] = [
                        call["id"]
                        for m in messages
                        for call in m.get("tool_calls", [])
                        if call["function"]["name"]
                        in {"get_configuration_capabilities", "get_configuration"}
                    ]
                    c["function"]["arguments"] = json.dumps(args)
            return reply

    model = InvestigatingModel(
        [
            call(
                "assess_route",
                {"route": "LOCAL_CODE", "reason": "没有 Shell", "evidence_refs": ["made-up"]},
                "bad-route",
            ),
            call("get_configuration_capabilities", call_id="caps"),
            call("get_configuration", {"page_id": "example_permit"}, "config"),
            call(
                "assess_route",
                {
                    "route": "LOCAL_CODE",
                    "reason": "需要已部署内核没有的新规范化算法",
                    "evidence_refs": ["caps", "config"],
                },
                "route",
            ),
            call(
                "record_decision",
                {
                    "status": "UNSUPPORTED",
                    "reason": "部署内核缺少该算法",
                    "evidence_refs": ["caps", "config"],
                },
                "gap-after-route",
            ),
            {"role": "assistant", "content": "配置无法表达该算法，交开发本地继续。"},
        ]
    )
    with TestClient(create_app(settings, model=model)) as client:
        sid = client.post(
            "/api/review-config/tasks",
            json={"client_request_id": "code", "page_id": "example_permit", "snapshot": SNAPSHOT},
        ).json()["task_id"]
        base = f"/api/review-config/tasks/{sid}"
        assert client.get(base + "/handoff").status_code == 409
        client.post(
            base + "/messages",
            json={
                "client_request_id": "message",
                "expected_revision": 0,
                "text": "增加新规范化能力",
            },
        )
        record = wait(client, sid, {})
        assert record.route == "LOCAL_CODE"
        assert record.waiting_reason == "LOCAL_EXECUTION"
        assert any(
            r.get("code") == "INVESTIGATION_REQUIRED"
            for r in record.tool_results.values()
            if isinstance(r, dict)
        )
        package = client.get(base + "/handoff").json()
        assert "snapshot" not in package and "messages" not in package and package["digest"]
        assert package["verification"]["configuration_publishable"] is False


def test_candidate_tools_return_actual_changes_and_distinguish_compile_from_publish(settings):
    model = FakeModel(
        [
            call(
                "edit_draft",
                {
                    "expected_revision": 0,
                    "operations": [
                        {"op": "upsert_field", "value": {"key": "permit.vin", "label": "车辆VIN"}},
                    ],
                },
            ),
            call("inspect_candidate"),
            {"role": "assistant", "content": "候选已保留，待补现场和独立验收。"},
        ]
    )
    with TestClient(create_app(settings, model=model)) as client:
        sid = client.post(
            "/api/review-config/tasks",
            json={
                "client_request_id": "compact-tools",
                "page_id": "example_permit",
                "snapshot": SNAPSHOT,
            },
        ).json()["task_id"]
        client.post(
            f"/api/review-config/tasks/{sid}/messages",
            json={
                "client_request_id": "message",
                "expected_revision": 0,
                "text": "只改VIN标签，先保留候选",
            },
        )
        record = wait(client, sid, {})
        assert record.turn_status == "COMPLETED" and record.route == "CONFIG"
        results = list(record.tool_results.values())
        updated = next(r for r in results if r.get("status") == "DRAFT_UPDATED")
        preview = next(r for r in results if r.get("status") == "COMPILED")
        assert "draft" not in updated and "manifest" not in preview
        assert any(
            c["path"] == "/fields/0/label" and c["after"] == "车辆VIN" for c in preview["changes"]
        )
        assert (
            preview["publishable"] is False
            and preview["waiting_reason"] is None
            and "试采集" in preview["next_action"]
        )
        assert record.report is None and record.receipt is None


@pytest.mark.asyncio
async def test_bounded_history_appends_without_overwriting_old_messages(settings, async_database):
    from forj_review_config_service.contracts.models import TaskRecord
    from forj_review_config_service.integrations.configuration_store.async_postgres import (
        AsyncTaskStore,
    )
    from forj_review_config_service.integrations.review_kernel.postgres import PostgresReviewKernel

    store = AsyncTaskStore(async_database)
    kernel = PostgresReviewKernel(
        async_database, settings.cases_dir, settings.review_url, settings.state_dir / "cases"
    )
    draft = (await kernel.context("example_permit"))["editable"]
    draft["version"] = "history"
    r = TaskRecord(
        task_id="history",
        draft=draft,
        baseline_digest=await kernel.baseline("example_permit"),
        messages=[{"role": "user", "content": str(i)} for i in range(300)],
    )
    await store.put(r)
    window = await store.get("history")
    assert len(window.messages) == 256 and window.messages[0]["content"] == "44"
    window.messages.append({"role": "user", "content": "latest"})
    await store.put(window)
    first = await store.messages("history", 0, 100)
    assert first[0]["content"] == "0"
    window.messages[0]["content"] = "overwrite"
    with pytest.raises(ConfigurationError) as rejected:
        await store.put(window)
    assert rejected.value.code == "STALE_MESSAGES"


@pytest.mark.asyncio
async def test_oversized_validation_evidence_has_explicit_limit(settings, async_database):
    from forj_review_config_service.integrations.configuration_store.async_postgres import (
        AsyncTaskStore,
    )
    from forj_review_config_service.integrations.review_kernel.postgres import PostgresReviewKernel
    from tests.support.configuration import database_candidate

    store = AsyncTaskStore(async_database)
    kernel = PostgresReviewKernel(
        async_database, settings.cases_dir, settings.review_url, settings.state_dir / "cases"
    )
    record = await database_candidate(kernel, store, sid="oversized-evidence")
    record.report["report_id"] = "oversized-report"
    record.report["verification_context"] = {"schema_version": "1.0", "requirements": "大" * 30000}
    with pytest.raises(ConfigurationError) as rejected:
        await store.put(record)
    assert rejected.value.code == "VALIDATION_CONTEXT_LIMIT"
    assert (await store.get(record.task_id)).report["report_id"] != "oversized-report"


@pytest.mark.asyncio
async def test_large_tool_result_is_durable_paged_and_not_copied_into_context(
    settings, async_database
):
    from sqlalchemy import delete

    from forj_review_config_service.application.context import model_messages
    from forj_review_config_service.contracts.models import TaskRecord
    from forj_review_config_service.integrations.configuration_store.async_postgres import (
        AsyncTaskStore,
    )
    from forj_review_config_service.integrations.review_kernel.postgres import PostgresReviewKernel
    from forj_review_database.models import TaskEvent

    store = AsyncTaskStore(async_database)
    kernel = PostgresReviewKernel(
        async_database, settings.cases_dir, settings.review_url, settings.state_dir / "cases"
    )
    draft = (await kernel.context("example_permit"))["editable"]
    value = {"configuration": "真实配置" * 50000}
    record = TaskRecord(
        task_id="large-result",
        draft=draft,
        baseline_digest=await kernel.baseline("example_permit"),
        messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "large-call",
                        "function": {"name": "get_configuration", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "large-call",
                "content": json.dumps(value, ensure_ascii=False),
            },
        ],
    )
    await store.put(record)
    loaded = await store.get(record.task_id)
    assert loaded.tool_results["large-call"] == value
    assert len(loaded.messages[-1]["content"]) < 5000
    assert len(json.dumps(model_messages(loaded), ensure_ascii=False)) < 10000
    assert await store.tool_result(record.task_id, "large-call") == value
    events = await store.events(record.task_id, 0)
    assert [e["type"] for e in events] == ["message", "message", "progress"]
    assert len(json.dumps(events, ensure_ascii=False)) < 10000
    assert await store.events(record.task_id, events[-1]["seq"]) == []
    loaded.messages.extend({"role": "user", "content": str(i)} for i in range(300))
    await store.put(loaded)
    assert "large-call" not in (await store.get(record.task_id)).tool_results
    assert await store.tool_result(record.task_id, "large-call") == value
    async with async_database.session(write=True) as db:
        await db.execute(
            delete(TaskEvent).where(
                TaskEvent.task_id == record.task_id,
                TaskEvent.kind == "TOOL_RESULT_CHUNK",
            )
        )
    with pytest.raises(ConfigurationError) as rejected:
        await store.tool_result(record.task_id, "large-call")
    assert rejected.value.code == "TOOL_RESULT_INCOMPLETE"
