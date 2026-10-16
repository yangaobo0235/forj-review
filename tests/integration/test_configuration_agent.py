"""Synthetic-only fixtures exercise the standalone app and actual durable graph."""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forj_configuration_agent.bootstrap.application import create_app
from forj_configuration_agent.config.settings import Settings
from forj_configuration_agent.contracts.models import AgentError, SessionRecord
from forj_configuration_agent.integrations.review_kernel.library import ReviewKernel

EXAMPLES = Path(__file__).parents[2] / "docs/示例/页面配置"
SNAPSHOT = {"snapshot_id": "snapshot", "page_instance_id": "page", "page_url": "https://example.test/permit",
            "structure_digest": "structure", "title": "Synthetic permit", "fields": [], "samples": []}


@pytest.mark.parametrize("snapshot", [
    {**SNAPSHOT, "fields": [{"candidate_id": "same", "label": "A"}, {"candidate_id": "same", "label": "B"}]},
    {**SNAPSHOT, "samples": [{"sample_id": "same", "url": "https://example.test/a"}] * 2},
    {**SNAPSHOT, "page_url": "javascript:alert(1)"},
    {**SNAPSHOT, "samples": [{"sample_id": "a", "url": "file:///private.png"}]},
    {**SNAPSHOT, "samples": [{"sample_id": "a", "url": "https://user:secret@example.test/a"}]},
])
def test_capture_rejects_ambiguous_ids_and_non_http_sources(settings, snapshot):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        response = client.post("/api/configuration-agent/captures", json={
            "client_request_id": "invalid-capture", "extension_id": "a" * 32, "snapshot": snapshot})
        assert response.status_code == 422
        assert client.app.state.service.store.list_captures() == []


def test_cancel_serializes_with_following_session_commands(settings):
    from forj_configuration_agent.contracts.models import MessageCommand

    with TestClient(create_app(settings, model=FakeModel())) as client:
        sid = create(client, headers())
        service = client.app.state.service

        async def scenario():
            stopping, release = asyncio.Event(), asyncio.Event()

            async def worker():
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    stopping.set()
                    await release.wait()
                    raise

            service.tasks[sid] = asyncio.create_task(worker())
            await asyncio.sleep(0)
            cancelling = asyncio.create_task(service.cancel(sid))
            await stopping.wait()
            following = asyncio.create_task(service.command(sid, "message", MessageCommand(
                client_request_id="after-cancel", expected_revision=0, text="重新核对")))
            try:
                await asyncio.sleep(0)
                assert not following.done(), "a new turn must wait until cancellation finishes"
            finally:
                release.set()
                await cancelling
                await following

        client.portal.call(scenario)


class FakeModel:
    def __init__(self, replies=None):
        self.replies = iter(replies or [{"role": "assistant", "content": "已读取要求，草稿待配置"}])

    async def complete(self, messages, tools):
        return next(self.replies)


def call(name, args=None, call_id="call1"):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {})}}]}


@pytest.fixture
def settings(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "permit.json").write_text((EXAMPLES / "permit.json").read_text(encoding="utf-8"), encoding="utf-8")
    cases = tmp_path / "cases"
    cases.mkdir()
    return Settings(state_dir=tmp_path / "state", pages_dir=pages, cases_dir=cases)


def headers():
    return {"origin": "chrome-extension://" + "a" * 32}


def create(client, auth, request_id="create-1"):
    response = client.post("/api/configuration-agent/sessions", headers=auth,
        json={"client_request_id": request_id, "snapshot": SNAPSHOT, "page_id": "example_permit"})
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def wait(client, sid, auth, status=None):
    async def check():
        for _ in range(200):
            record = client.app.state.service.store.get(sid)
            if record.turn_status not in {"QUEUED", "RUNNING"}:
                return record
            await asyncio.sleep(.02)
        raise AssertionError("graph did not stop")
    record = client.portal.call(check)
    if status:
        assert record.turn_status == status, record.error
    return record


def test_local_access_without_pairing_and_creation_idempotency(settings):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        assert client.get("/health").json()["service"] == "configuration-agent"
        assert client.get("/api/configuration-agent/sessions").status_code == 200
        assert not (settings.state_dir / "access.json").exists()
        auth = headers()
        assert client.get("/api/configuration-agent/sessions", headers={**auth, "origin": "https://evil.test"}).status_code == 403
        assert client.get("/api/configuration-agent/sessions", headers={**auth, "host": "evil.test"}).status_code == 403
        for origin in ("chrome-extension://" + "b" * 32, "http://localhost:5173", "http://127.0.0.1:8011"):
            response = client.get("/api/configuration-agent/sessions", headers={"origin": origin})
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin
            preflight = client.options("/api/configuration-agent/sessions", headers={"origin": origin,
                "access-control-request-method": "POST", "access-control-request-headers": "content-type"})
            assert preflight.status_code == 200
            assert preflight.headers["access-control-allow-headers"] == "content-type"
            assert preflight.headers["vary"] == "Origin"
        for origin in ("null", "http://localhost.evil.test", "chrome-extension://invalid",
                       "http://127.0.0.1:8011@evil.test"):
            assert client.options("/api/configuration-agent/sessions", headers={"origin": origin}).status_code == 403
        first = create(client, auth)
        assert create(client, auth) == first
        response = client.post("/api/configuration-agent/sessions", headers=auth,
            json={"client_request_id": "create-1", "snapshot": {**SNAPSHOT, "title": "changed"}})
        assert response.status_code == 409
        assert not hasattr(client.app.state, "review_jobs")


def test_question_survives_restart_and_uses_native_tool_result_protocol(settings):
    question = call("ask_user", {"question": "注册日期还是发证日期？", "options": ["注册日期", "发证日期"]})
    with TestClient(create_app(settings, model=FakeModel([question]))) as client:
        auth = headers()
        sid = create(client, auth)
        command = {"client_request_id": "msg1", "expected_revision": 0, "text": "审核日期"}
        assert client.post(f"/api/configuration-agent/sessions/{sid}/messages", headers=auth, json=command).status_code == 200
        record = wait(client, sid, auth, "WAITING")
        assert record.pending["question"]
        question_id = record.pending["id"]
    with TestClient(create_app(settings, model=FakeModel())) as client:
        record = client.app.state.service.store.get(sid)
        assert record.pending["id"] == question_id
        command = {"client_request_id": "answer1", "expected_revision": 0, "answer": "注册日期"}
        path = f"/api/configuration-agent/sessions/{sid}/interactions/{question_id}/answer"
        assert client.post(path, headers=auth, json=command).status_code == 200
        record = wait(client, sid, auth, "COMPLETED")
        assert record.pending is None
        index = next(i for i, m in enumerate(record.messages) if m.get("tool_calls"))
        assert record.messages[index + 1]["role"] == "tool"
        assert "注册日期" in record.messages[index + 1]["content"]
        assert client.post(path, headers=auth, json=command).status_code == 200
        history = client.get(f"/api/configuration-agent/sessions/{sid}").json()["chat_messages"]
        assert [m["content"] for m in history] == ["审核日期", "注册日期还是发证日期？", "注册日期", "已读取要求，草稿待配置"]
    with TestClient(create_app(settings, model=FakeModel())) as client:
        assert client.get(f"/api/configuration-agent/sessions/{sid}").json()["chat_messages"] == history


def test_edit_invalidates_report_and_detects_stale_revision(settings):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        auth = headers()
        sid = create(client, auth)
        payload = {"client_request_id": "edit1", "expected_revision": 0,
                   "operations": [{"op": "set_page_metadata", "value": {"title": "新标题"}}]}
        path = f"/api/configuration-agent/sessions/{sid}/draft"
        assert client.post(path, headers=auth, json=payload).status_code == 200
        assert client.post(path, headers=auth, json=payload).status_code == 200
        payload["client_request_id"] = "edit2"
        assert client.post(path, headers=auth, json=payload).status_code == 409
        record = client.app.state.service.store.get(sid)
        assert record.revision == 1 and record.report is None
        report = client.app.state.service.validate_record(record)
        assert not report["ok"]
        assert len(report["blockers"]) >= 2


def candidate(settings):
    kernel = ReviewKernel(settings.pages_dir, settings.cases_dir, settings.review_url)
    draft = json.loads((settings.pages_dir / "permit.json").read_text(encoding="utf-8"))
    draft["version"] = "2.0"
    record = SessionRecord(session_id="test", snapshot=SNAPSHOT, baseline_digest=kernel.baseline(), draft=draft)
    record.report = kernel.validate(record)
    return kernel, record


def test_commit_is_idempotent_preserves_history_and_blocks_drift(settings):
    kernel, record = candidate(settings)
    first = kernel.commit(record, "abc123")
    assert kernel.commit(record, "abc123") == first
    assert kernel.context("example_permit")["reference"]["version"] == "2.0"
    assert (settings.pages_dir / ".history/versions.json").exists()
    with pytest.raises(AgentError, match="改变"):
        kernel.validate(record)


def test_crash_during_multifile_commit_blocks_loading_and_can_recover(settings, monkeypatch):
    from forj_review_assistant.runtime.configuration.service import validated_configuration
    kernel, record = candidate(settings)
    original = kernel._install
    def partial(target, previous):
        name = next(iter(target))
        (settings.pages_dir / name).write_text(target[name], encoding="utf-8")
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(kernel, "_install", partial)
    with pytest.raises(OSError):
        kernel.commit(record, "abc123")
    with pytest.raises(ValueError, match="COMMIT_INCOMPLETE"):
        validated_configuration(settings.pages_dir)
    monkeypatch.setattr(kernel, "_install", original)
    assert kernel.recover()["status"] == "ROLLED_BACK"
    assert kernel.context("example_permit")["reference"]["version"] == "1.0"


def test_candidate_version_is_required_and_dependency_errors_are_not_hidden(settings):
    kernel, record = candidate(settings)
    record.draft["version"] = "1.0"
    with pytest.raises(ValueError, match="新版本号"):
        kernel.validate(record)
    record.draft["version"] = "2.0"
    record.draft["materials"] = []
    with pytest.raises(ValueError):
        kernel.validate(record)


class HappyModel:
    """The model is scripted; compilation, graph replay, browser binding and commits are real."""
    def __init__(self):
        self.step = 0

    async def complete(self, messages, tools):
        self.step += 1
        context = json.loads(messages[0]["content"].split("当前工作状态：", 1)[1])
        if self.step == 1:
            return call("edit_draft", {"expected_revision": 0, "operations": [
                {"op": "set_page_metadata", "value": {"version": "2.0", "title": "Synthetic updated permit"}}]}, "edit")
        if self.step == 2:
            rid = context["requirements"][0]["requirement_id"]
            return call("record_decision", {"requirement_id": rid, "status": "RESOLVED", "reason": "按明确要求修改标题",
                "evidence_refs": [rid]}, "decision")
        if self.step == 3:
            return call("request_collection_trial", call_id="browser")
        if self.step == 4:
            return call("run_replay", {"case_set_id": "local/permit-v2"}, "replay")
        if self.step == 5:
            return call("submit_candidate", call_id="submit")
        raise AssertionError("unexpected extra model call")


def test_chat_to_trial_real_replay_confirmation_save_and_activation_without_review_process(settings):
    suite = json.loads((EXAMPLES / "回放样例/permit-cases.json").read_text(encoding="utf-8"))
    for case in suite["cases"]:
        case["request"]["profile_version"] = "2.0"
    with TestClient(create_app(settings, model=HappyModel())) as client:
        auth = headers()
        sid = create(client, auth)
        assert client.post("/api/configuration-agent/case-sets", headers=auth,
            json={"case_set_id": "permit-v2", "suite": suite}).status_code == 200
        command = {"client_request_id": "message", "expected_revision": 0, "text": "修改标题为 Synthetic updated permit，版本 2.0"}
        assert client.post(f"/api/configuration-agent/sessions/{sid}/messages", headers=auth, json=command).status_code == 200
        record = wait(client, sid, auth, "WAITING")
        assert record.pending["kind"] == "browser"
        pending = record.pending
        browser = {"client_request_id": "browser", "expected_revision": 1,
                   **{key: pending[key] for key in ["snapshot_id", "page_instance_id", "page_url", "structure_digest", "candidate_digest"]},
                   "fields": {"permit.vin": 1}, "control_kinds": {"permit.vin": "text"}}
        browser["matched_page_id"] = "example_permit"
        path = f'/api/configuration-agent/sessions/{sid}/browser-tasks/{pending["id"]}/result'
        stale = {**browser, "page_instance_id": "different-page", "client_request_id": "stale"}
        assert client.post(path, headers=auth, json=stale).status_code == 409
        assert client.post(path, headers=auth, json=browser).status_code == 200
        record = wait(client, sid, auth, "WAITING")
        assert record.state == "READY_FOR_CONFIRMATION", record.error or record.report
        assert all(r["ok"] for r in record.report["replays"])
        confirm = {"client_request_id": "confirm", "expected_revision": 1,
                   "report_id": record.report["report_id"], "candidate_digest": record.report["candidate_digest"]}
        path = f"/api/configuration-agent/sessions/{sid}/confirm"
        assert client.post(path, headers=auth, json=confirm).status_code == 200
        record = wait(client, sid, auth, "COMPLETED")
        assert record.receipt and record.state == "SAVED_PENDING_RESTART"
        assert client.post(path, headers=auth, json=confirm).status_code == 200
        activation = client.get(f"/api/configuration-agent/sessions/{sid}/activation", headers=auth)
        assert activation.status_code == 200
        assert activation.json()["state"] == "SAVED_PENDING_RESTART"


def test_model_budget_is_interrupted_and_cancelled_question_does_not_resurrect(settings):
    from dataclasses import replace
    settings = replace(settings, max_calls=1)
    with TestClient(create_app(settings, model=FakeModel([call("find_templates")]))) as client:
        auth = headers()
        sid = create(client, auth)
        client.post(f"/api/configuration-agent/sessions/{sid}/messages", headers=auth,
                    json={"client_request_id": "message", "expected_revision": 0, "text": "检查配置"})
        record = wait(client, sid, auth, "INTERRUPTED")
        assert "预算" in record.error
        assert record.state != "READY_FOR_CONFIRMATION"


def test_agent_architecture_enforces_the_single_adapter_boundary(tmp_path):
    from scripts.ci.check_architecture import check_configuration_agent
    folder = tmp_path / "application"
    folder.mkdir()
    (folder / "bad.py").write_text("from forj_review_assistant.runtime.review_service import ReviewService\n", encoding="utf-8")
    assert len(check_configuration_agent(tmp_path)) == 1


def test_new_page_and_rollback_reuse_one_schema_and_history(settings):
    kernel, record = candidate(settings)
    record.draft["page_id"] = "brand_new_page"
    record.draft["paths"] = ["/brand-new-page"]
    record.report = kernel.validate(record)
    receipt = kernel.commit(record, "newpage123")
    assert receipt["page_id"] == "brand_new_page"
    assert kernel.context("example_permit")["reference"]["version"] == "1.0"
    # Save a second version of the existing page, then activate its unchanged historical source.
    record.draft["page_id"] = "example_permit"
    record.draft["paths"] = ["/example-permit-review"]
    record.baseline_digest = kernel.baseline()
    record.report = kernel.validate(record)
    kernel.commit(record, "update123")
    rollback = SessionRecord(session_id="rollback", snapshot=SNAPSHOT, activation_only=True,
        baseline_digest=kernel.baseline(), draft=kernel.rollback_draft("example_permit", "1.0"))
    rollback.report = kernel.validate(rollback)
    kernel.commit(rollback, "rollback123")
    assert kernel.context("example_permit")["reference"]["version"] == "1.0"
    assert json.loads((settings.pages_dir / "permit.json").read_text(encoding="utf-8"))["version"] == "1.0"


def test_deleted_local_extension_field_does_not_become_unknown_template_removal(settings):
    from forj_configuration_agent.application.drafts import apply_operations
    from forj_configuration_agent.contracts.models import DraftOperation
    _, record = candidate(settings)
    record.draft["extends"] = {"page_id": "example_permit", "version": "1.0"}
    apply_operations(record, [DraftOperation(op="upsert_field", value={"key": "permit.local", "label": "Local"}),
                             DraftOperation(op="remove_field", value={"key": "permit.local"})],
                     {"fields": ["permit.vin"]})
    assert "permit.local" not in record.draft.get("remove_fields", [])
    apply_operations(record, [DraftOperation(op="remove_field", value={"key": "permit.vin"})],
                     {"fields": ["permit.vin"]})
    assert record.draft["remove_fields"] == ["permit.vin"]


def test_completed_file_commit_with_missing_session_receipt_can_be_reconciled(settings):
    kernel, record = candidate(settings)
    receipt = kernel.commit(record, "completed123")
    assert kernel.commit_receipt("completed123") == receipt
    assert kernel.commit_receipt("unknown123") is None


def test_confirmed_save_keeps_operation_until_resume_reconciles_file_receipt(settings):
    kernel, record = candidate(settings)
    receipt = kernel.commit(record, "reconcile123")
    record.pending = {"id": "confirmed", "kind": "confirmation", "confirmed": True,
                      "operation_id": "reconcile123", "candidate_digest": record.report["candidate_digest"]}
    record.turn_status = "INTERRUPTED"
    record.state = "READY_FOR_CONFIRMATION"
    with TestClient(create_app(settings, model=FakeModel(), kernel=kernel)) as client:
        auth = headers()
        service = client.app.state.service
        service.store.put(record)
        base = f"/api/configuration-agent/sessions/{record.session_id}"
        command = {"client_request_id": "retry", "expected_revision": record.revision}
        commands = {
            "/messages": {**command, "text": "修改标题"},
            "/draft": {**command, "operations": [{"op": "set_page_metadata", "value": {"title": "新标题"}}]},
            "/confirm": {**command, "report_id": "old", "candidate_digest": "old"},
            "/validate": command,
            "/cancel": {},
        }
        for path, payload in commands.items():
            response = client.post(base + path, headers=auth, json=payload)
            assert response.status_code == 409, (path, response.text)
            assert response.json()["code"] == "COMMIT_PENDING"
        assert client.delete(base, headers=auth).status_code == 409
        assert service.store.get(record.session_id).pending["operation_id"] == "reconcile123"
        assert client.post(base + "/resume", headers=auth, json=command).status_code == 200
        saved = wait(client, record.session_id, auth, "COMPLETED")
        assert saved.receipt == receipt and saved.pending is None
        assert saved.state == "SAVED_PENDING_RESTART"


def test_only_human_can_refresh_scope_and_old_browser_task_becomes_stale(settings):
    with TestClient(create_app(settings, model=FakeModel([call("request_collection_trial", call_id="browser")]))) as client:
        auth = headers()
        sid = create(client, auth)
        client.post(f"/api/configuration-agent/sessions/{sid}/messages", headers=auth,
                    json={"client_request_id": "message", "expected_revision": 0, "text": "核对配置"})
        wait(client, sid, auth, "WAITING")
        response = client.post(f"/api/configuration-agent/sessions/{sid}/page", headers=auth,
            json={"client_request_id": "refresh", "expected_revision": 0,
                  "snapshot": {**SNAPSHOT, "snapshot_id": "new"}})
        assert response.status_code == 200
        record = client.app.state.service.store.get(sid)
        assert record.revision == 1 and record.pending is None and record.report is None
        assert record.snapshot.snapshot_id == "new"


def test_capture_handoff_is_durable_idempotent_and_bound_to_session(settings):
    command = {"client_request_id": "capture1", "extension_id": "a" * 32, "snapshot": SNAPSHOT}
    with TestClient(create_app(settings, model=FakeModel())) as client:
        response = client.post("/api/configuration-agent/captures", headers=headers(), json=command)
        assert response.status_code == 200
        capture_id = response.json()["capture_id"]
        assert client.post("/api/configuration-agent/captures", json=command).json()["capture_id"] == capture_id
        assert client.post("/api/configuration-agent/captures", json={**command, "extension_id": "b" * 32}).status_code == 409
        assert client.get("/api/configuration-agent/captures").json()[0]["field_count"] == 0
    with TestClient(create_app(settings, model=FakeModel())) as client:
        capture = client.get(f"/api/configuration-agent/captures/{capture_id}").json()
        assert capture["extension_id"] == "a" * 32 and capture["snapshot"]["snapshot_id"] == "snapshot"
        payload = {"client_request_id": "web-session", "snapshot": SNAPSHOT,
                   "capture_id": capture_id, "page_id": "example_permit"}
        mismatch = {**payload, "snapshot": {**SNAPSHOT, "snapshot_id": "different"}}
        assert client.post("/api/configuration-agent/sessions", json=mismatch).json()["code"] == "CAPTURE_MISMATCH"
        response = client.post("/api/configuration-agent/sessions", json=payload)
        assert response.status_code == 200
        sid = response.json()["session_id"]
        assert client.get(f"/api/configuration-agent/sessions/{sid}").json()["capture_id"] == capture_id
        assert client.delete(f"/api/configuration-agent/captures/{capture_id}").status_code == 409
        new_capture = client.post("/api/configuration-agent/captures", json={**command, "client_request_id": "capture2",
            "snapshot": {**SNAPSHOT, "snapshot_id": "snapshot2"}}).json()["capture_id"]
        refresh = {"client_request_id": "refresh", "expected_revision": 0,
                   "capture_id": new_capture, "snapshot": {**SNAPSHOT, "snapshot_id": "snapshot2"}}
        assert client.post(f"/api/configuration-agent/sessions/{sid}/page", json=refresh).status_code == 200
        assert client.get(f"/api/configuration-agent/sessions/{sid}").json()["capture_id"] == new_capture
        assert client.delete(f"/api/configuration-agent/captures/{capture_id}").status_code == 200
        assert client.get(f"/api/configuration-agent/captures/{capture_id}").status_code == 404


def test_capture_store_migrates_v1_without_losing_existing_sessions(settings):
    import sqlite3

    from forj_configuration_agent.integrations.configuration_store.sqlite import SessionStore

    kernel, record = candidate(settings)
    settings.state_dir.mkdir()
    path = settings.state_dir / "configuration.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY, body TEXT NOT NULL); PRAGMA user_version=1;")
        old = record.model_dump()
        old.pop("capture_id")
        db.execute("INSERT INTO sessions VALUES(?,?)", (record.session_id, json.dumps(old)))
    store = SessionStore(path)
    assert store.get(record.session_id).capture_id is None
    assert store.get(record.session_id).draft == record.draft
    assert store.list_captures() == []
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_recovery_does_not_overwrite_manual_changes(settings, monkeypatch):
    kernel, record = candidate(settings)
    original = kernel._install
    def failure(target, previous):
        raise OSError("synthetic interruption")
    monkeypatch.setattr(kernel, "_install", failure)
    with pytest.raises(OSError):
        kernel.commit(record, "pending123")
    monkeypatch.setattr(kernel, "_install", original)
    path = settings.pages_dir / "permit.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["title"] = "manual change outside transaction"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(AgentError, match="人工对账"):
        kernel.recover()
    assert json.loads(path.read_text(encoding="utf-8"))["title"] == value["title"]
    assert (settings.pages_dir / ".configuration-pending").exists()


def test_configuration_read_uses_cross_process_lock_and_checks_commit_marker(settings, tmp_path):
    import subprocess
    import sys
    import time

    from forj_review_assistant.config.configuration_io import configuration_lock
    ready = tmp_path / "reader-ready"
    script = """
import sys
from pathlib import Path
from forj_review_assistant.businesses.configuration.loader import load_page_profiles
Path(sys.argv[2]).write_text('ready')
try:
    load_page_profiles(Path(sys.argv[1]))
except ValueError as exc:
    print('blocked-incomplete' if 'COMMIT_INCOMPLETE' in str(exc) else 'unexpected')
"""
    with configuration_lock(settings.pages_dir):
        process = subprocess.Popen([sys.executable, "-c", script, str(settings.pages_dir), str(ready)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(.03)
        assert ready.exists()
        assert process.poll() is None
        (settings.pages_dir / ".configuration-pending").write_text("pending123")
    output, errors = process.communicate(timeout=20)
    assert process.returncode == 0, errors
    assert output.strip() == "blocked-incomplete"


def browser_evidence(kernel, record):
    from forj_configuration_agent.application.validation import trial_binding
    preview = kernel.validate(record)
    return {"kind": "collection_trial", "ok": True, "revision": record.revision,
            "candidate_digest": preview["candidate_digest"], "binding": trial_binding(record),
            "facts": {"fields": {f["key"]: 1 for f in preview["manifest"]["fields"]},
                      "control_kinds": {key: "text" for key in preview["manifest"].get("writable_fields", [])},
                      "samples": {}, "matched_page_id": record.draft["page_id"], "diagnostics": []}}


def test_refresh_rejects_old_trial_but_retains_history_and_rechecks_raw_facts(settings):
    kernel, record = candidate(settings)
    record.tool_results["old-trial"] = browser_evidence(kernel, record)
    with TestClient(create_app(settings, model=FakeModel(), kernel=kernel)) as client:
        service = client.app.state.service
        service.store.put(record)
        assert not any("试采集" in b for b in service.validate_record(record)["blockers"])
        path = f"/api/configuration-agent/sessions/{record.session_id}/page"
        assert client.post(path, json={"client_request_id": "new-page", "expected_revision": 0,
            "snapshot": {**SNAPSHOT, "snapshot_id": "new", "structure_digest": "changed"}}).status_code == 200
        current = service.store.get(record.session_id)
        assert "old-trial" in current.tool_results
        assert any("试采集" in b for b in service.validate_record(current)["blockers"])
        current.tool_results["new-trial"] = browser_evidence(kernel, current)
        assert not any("试采集" in b for b in service.validate_record(current)["blockers"])
        current.tool_results["new-trial"]["facts"]["fields"] = {}
        assert any("试采集" in b for b in service.validate_record(current)["blockers"])


def test_trial_requires_current_material_mapping_and_rejects_legacy_ok(settings):
    from forj_configuration_agent.application.validation import current_trial
    kernel, record = candidate(settings)
    preview = kernel.validate(record)
    assert not current_trial(record, preview, {"kind": "collection_trial", "ok": True,
        "candidate_digest": preview["candidate_digest"], "revision": 0})
    record.selected_samples = ["sample"]
    record.decisions = [{"sample_id": "sample", "material": "old", "status": "RESOLVED", "revision": 0}]
    proof = browser_evidence(kernel, record)
    proof["facts"]["samples"] = {"sample": "old"}
    assert current_trial(record, preview, proof)
    record.decisions[0]["material"] = "new"
    assert not current_trial(record, preview, proof)


@pytest.mark.parametrize("failure_stage", ["orphan", "partial", "rolled_back"])
def test_resume_recovers_failed_save_revokes_confirmation_and_allows_new_save(settings, monkeypatch, failure_stage):
    from forj_configuration_agent.integrations.review_kernel import library
    kernel, record = candidate(settings)
    operation_id = "failed123"
    install, write = kernel._install, library.atomic_write
    if failure_stage == "orphan":
        def fail_marker(path, text):
            if path.name == ".configuration-pending":
                raise OSError("synthetic marker failure")
            return write(path, text)
        monkeypatch.setattr(library, "atomic_write", fail_marker)
    else:
        def fail_install(target, previous):
            name = next(iter(target))
            write(settings.pages_dir / name, target[name])
            raise OSError("synthetic partial write")
        monkeypatch.setattr(kernel, "_install", fail_install)
    with pytest.raises(OSError):
        kernel.commit(record, operation_id)
    monkeypatch.setattr(library, "atomic_write", write)
    monkeypatch.setattr(kernel, "_install", install)
    if failure_stage == "rolled_back":
        assert kernel.recover()["status"] == "ROLLED_BACK"
    record.pending = {"id": "confirmation", "kind": "confirmation", "confirmed": True,
        "operation_id": operation_id, "candidate_digest": record.report["candidate_digest"]}
    record.state, record.turn_status = "READY_FOR_CONFIRMATION", "FAILED"
    with TestClient(create_app(settings, model=FakeModel(), kernel=kernel)) as client:
        service = client.app.state.service
        service.store.put(record)
        base = f"/api/configuration-agent/sessions/{record.session_id}"
        assert client.post(base + "/resume", json={"client_request_id": "recover", "expected_revision": 0}).status_code == 200
        current = wait(client, record.session_id, {}, "COMPLETED")
        assert current.pending is None and current.report is None and current.state == "DRAFTING"
        assert "回滚" in current.error
        assert kernel.baseline() == record.baseline_digest
        journal = settings.pages_dir / ".transactions" / operation_id / "journal.json"
        assert json.loads(journal.read_text(encoding="utf-8"))["status"] == "ROLLED_BACK"
        assert not (settings.pages_dir / ".configuration-pending").exists()
        # A fresh human confirmation gets a fresh operation, with real independent replay.
        suite = json.loads((EXAMPLES / "回放样例/permit-cases.json").read_text(encoding="utf-8"))
        for case in suite["cases"]:
            case["request"]["profile_version"] = "2.0"
        kernel.register_cases("recovered", suite)
        current.tool_results.update(trial=browser_evidence(kernel, current),
            replay=asyncio.run(kernel.replay(current, "recovered")))
        service.store.put(current)
        response = client.post(base + "/validate", json={"client_request_id": "verify", "expected_revision": 0})
        assert response.status_code == 200 and response.json()["ok"], response.text
        report = response.json()
        assert client.post(base + "/confirm", json={"client_request_id": "new-confirm", "expected_revision": 0,
            "candidate_digest": report["candidate_digest"], "report_id": report["report_id"]}).status_code == 200
        saved = wait(client, record.session_id, {}, "COMPLETED")
        assert saved.receipt and saved.receipt["operation_id"] != operation_id
        assert json.loads(journal.read_text(encoding="utf-8"))["status"] == "ROLLED_BACK"


def test_orphan_recovery_refuses_external_changes(settings, monkeypatch):
    from forj_configuration_agent.integrations.review_kernel import library
    kernel, record = candidate(settings)
    original = library.atomic_write
    def fail_marker(path, text):
        if path.name == ".configuration-pending":
            raise OSError("synthetic interruption")
        return original(path, text)
    monkeypatch.setattr(library, "atomic_write", fail_marker)
    with pytest.raises(OSError):
        kernel.commit(record, "orphan123")
    monkeypatch.setattr(library, "atomic_write", original)
    path = settings.pages_dir / "permit.json"
    original_text = path.read_text(encoding="utf-8") + "\n"
    path.write_text(original_text, encoding="utf-8")
    with pytest.raises(AgentError, match="人工对账"):
        kernel.recover()
    assert path.read_text(encoding="utf-8") == original_text


@pytest.mark.parametrize(("name", "args"), [
    ("ask_user", {"question": "日期？", "options": "注册日期"}),
    ("ask_user", {"question": "日期？", "options": [1]}),
    ("get_configuration", {"page_id": 1}),
    ("analyze_materials", {"sample_ids": "sample", "focus": ""}),
    ("edit_draft", {"expected_revision": True, "operations": []}),
    ("edit_draft", {"expected_revision": "0", "operations": [{"op": "remove_field", "value": {"key": "x"}}]}),
    ("run_replay", {"case_set_id": {"id": "x"}}),
    ("record_decision", {"status": "WRONG", "reason": "r", "evidence_refs": ["snapshot"]}),
    ("get_page_snapshot", {"unexpected": True}),
])
def test_tools_reject_invalid_types_before_any_side_effect(name, args):
    from forj_configuration_agent.tools.arguments import validate_arguments
    with pytest.raises(AgentError) as exc:
        validate_arguments(name, args)
    assert exc.value.code == "INVALID_ARGUMENTS"


def test_bad_question_returns_tool_error_then_model_can_correct_it(settings):
    replies = [call("ask_user", {"question": "日期？", "options": "注册日期"}),
               call("ask_user", {"question": "日期？", "options": ["注册日期"]})]
    with TestClient(create_app(settings, model=FakeModel(replies))) as client:
        sid = create(client, {})
        client.post(f"/api/configuration-agent/sessions/{sid}/messages",
            json={"client_request_id": "m", "expected_revision": 0, "text": "配置日期"})
        record = wait(client, sid, {}, "WAITING")
        assert record.pending["options"] == ["注册日期"]
        failures = [r for r in record.tool_results.values() if r.get("code") == "INVALID_ARGUMENTS"]
        assert len(failures) == 1
        events = client.app.state.service.store.events(sid, 0)
        assert any(e["type"] == "tool_completed" and e["data"].get("call_id")
                   and e["data"].get("code") == "INVALID_ARGUMENTS" for e in events)


def test_reused_provider_call_id_executes_distinct_operations_with_paired_responses(settings):
    replies = [call("get_page_snapshot"), call("edit_draft", {"expected_revision": 0,
        "operations": [{"op": "set_page_metadata", "value": {"title": "updated"}}]}),
        {"role": "assistant", "content": "草稿已更新"}]
    with TestClient(create_app(settings, model=FakeModel(replies))) as client:
        sid = create(client, {})
        client.post(f"/api/configuration-agent/sessions/{sid}/messages",
            json={"client_request_id": "m", "expected_revision": 0, "text": "改标题"})
        record = wait(client, sid, {}, "COMPLETED")
        assert record.revision == 1 and record.draft["title"] == "updated"
        calls = [call for m in record.messages for call in m.get("tool_calls", [])]
        assert len({c["id"] for c in calls}) == 2
        for c in calls:
            assert sum(m.get("tool_call_id") == c["id"] for m in record.messages) == 1


def test_crash_after_persisting_call_resumes_same_internal_identity_once(settings):
    from forj_configuration_agent.application.conversation import normalize_reply
    kernel, record = candidate(settings)
    message = normalize_reply(call("edit_draft", {"expected_revision": 0,
        "operations": [{"op": "set_page_metadata", "value": {"title": "resumed"}}]}))
    record.messages.append(message)
    call_id = message["tool_calls"][0]["id"]
    with TestClient(create_app(settings, model=FakeModel([{"role": "assistant", "content": "done"}] * 2), kernel=kernel)) as client:
        client.app.state.service.store.put(record)
        base = f"/api/configuration-agent/sessions/{record.session_id}/resume"
        client.post(base, json={"client_request_id": "resume1", "expected_revision": 0})
        first = wait(client, record.session_id, {}, "COMPLETED")
        assert first.revision == 1 and call_id in first.tool_results
        client.post(base, json={"client_request_id": "resume2", "expected_revision": 1})
        second = wait(client, record.session_id, {}, "COMPLETED")
        assert second.revision == 1
        assert sum(m.get("tool_call_id") == call_id for m in second.messages) == 1


def test_expired_proof_before_commit_revokes_confirmation_and_allows_edit(settings):
    kernel, record = candidate(settings)
    record.pending = {"id": "confirmed", "kind": "confirmation", "confirmed": True,
                      "operation_id": "expired123", "candidate_digest": record.report["candidate_digest"]}
    with TestClient(create_app(settings, model=FakeModel(), kernel=kernel)) as client:
        service = client.app.state.service
        service.store.put(record)
        base = f"/api/configuration-agent/sessions/{record.session_id}"
        client.post(base + "/resume", json={"client_request_id": "resume", "expected_revision": 0})
        current = wait(client, record.session_id, {}, "FAILED")
        assert current.pending is None and current.state == "DRAFTING"
        assert client.post(base + "/draft", json={"client_request_id": "edit", "expected_revision": 0,
            "operations": [{"op": "set_page_metadata", "value": {"title": "editable"}}]}).status_code == 200
        assert not (settings.pages_dir / ".transactions/expired123/journal.json").exists()


def test_legacy_question_options_are_migrated_without_changing_the_answer_protocol(settings):
    _, record = candidate(settings)
    old = record.model_dump(mode="json")
    old["pending"] = {"id": "legacy", "tool_call_id": "legacy", "kind": "question", "revision": 0,
                      "question": "日期？", "options": "注册日期"}
    migrated = SessionRecord.model_validate(old)
    assert migrated.pending["options"] == []
    assert migrated.pending["id"] == "legacy"


@pytest.mark.parametrize("reply", [
    {"role": "assistant", "tool_calls": [{}]},
    {"role": "assistant", "tool_calls": "broken"},
    {"role": "assistant", "content": {"text": "broken"}},
])
def test_malformed_model_call_is_rejected_before_persisting(reply):
    from forj_configuration_agent.application.conversation import normalize_reply
    with pytest.raises(AgentError) as exc:
        normalize_reply(reply)
    assert exc.value.code == "MODEL_FORMAT"


def test_question_projection_omits_cancelled_answers_and_deduplicates_question(settings):
    from forj_configuration_agent.application.conversation import chat_messages, tool_message
    _, record = candidate(settings)
    question = call("ask_user", {"question": "日期？", "options": []})
    question["content"] = "日期？"
    record.messages.append(question)
    tool_message(record, "call1", {"status": "CANCELLED"})
    assert chat_messages(record) == [{"role": "assistant", "content": "日期？"}]
    record.messages[-1]["content"] = json.dumps({"answer": "注册日期"}, ensure_ascii=False)
    assert chat_messages(record) == [{"role": "assistant", "content": "日期？"}, {"role": "user", "content": "注册日期"}]


def test_error_logs_keep_locations_and_call_id_without_private_payload(settings, caplog):
    import logging

    from forj_configuration_agent.application.diagnostics import log_failure
    private = "private-signed-url-or-certificate"
    with caplog.at_level(logging.ERROR):
        try:
            raise ValueError(private)
        except ValueError as exc:
            log_failure(logging.getLogger("forj_configuration_agent"), exc, call_id="internal123", node="execute_tools")
    assert private not in caplog.text
    assert "internal123" in caplog.text and "test_error_logs" in caplog.text


def test_offline_configuration_kernel_never_loads_review_model_credentials(settings, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline configuration must not load review service credentials")
    monkeypatch.setattr("forj_review_assistant.runtime.review_service.load_qwen_config", forbidden)
    kernel, record = candidate(settings)
    suite = json.loads((EXAMPLES / "回放样例/permit-cases.json").read_text(encoding="utf-8"))
    for case in suite["cases"]:
        case["request"]["profile_version"] = "2.0"
    kernel.register_cases("isolated", suite)
    assert asyncio.run(kernel.replay(record, "isolated"))["ok"]


def test_configuration_agent_receives_shared_decision_policy(settings):
    kernel = ReviewKernel(settings.pages_dir, settings.cases_dir, "http://127.0.0.1:8011")
    capabilities = kernel.capabilities()
    assert capabilities["decision_policy"]["version"] == "2"
    assert "必须为 true" in capabilities["decision_policy"]["uncertain_requires_review"]
    field = capabilities["schema"]["$defs"]["FieldConfig"]["properties"]["uncertain_requires_review"]
    assert field["default"] is True
    assert "必须为 true" in field["description"]
