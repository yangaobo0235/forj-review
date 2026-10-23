"""Configuration publication requirements."""

import json

import pytest
from fastapi.testclient import TestClient

from forj_review_config_service.bootstrap.application import create_app
from forj_review_config_service.contracts.models import ConfigurationError
from tests.support.configuration import (
    FIXTURES,
    FakeModel,
    HappyModel,
    candidate,
    create,
    database_candidate,
    headers,
    wait,
)


def test_edit_invalidates_report_and_detects_stale_revision(settings):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        auth = headers()
        sid = create(client, auth)
        payload = {
            "client_request_id": "edit1",
            "expected_revision": 0,
            "operations": [
                {"op": "upsert_field", "value": {"key": "permit.vin", "label": "新标题"}}
            ],
        }
        path = f"/api/review-config/tasks/{sid}/draft"
        assert client.post(path, headers=auth, json=payload).status_code == 200
        assert client.post(path, headers=auth, json=payload).status_code == 200
        payload["client_request_id"] = "edit2"
        assert client.post(path, headers=auth, json=payload).status_code == 409
        record = client.portal.call(client.app.state.service.store.get, sid)
        assert record.revision == 1 and record.report is None
        report = client.portal.call(client.app.state.service.validate_record, record)
        assert not report["ok"]
        assert len(report["blockers"]) >= 2


def test_commit_is_idempotent_preserves_history_and_blocks_drift(settings):
    kernel, record = candidate(settings)
    first = kernel.commit(record, "abc123")
    assert kernel.commit(record, "abc123") == first
    assert kernel.context("example_permit")["reference"]["version"] == "2.0"
    assert kernel.configuration.source("example_permit", "1.0") is not None
    with pytest.raises(ConfigurationError, match="改变"):
        kernel.validate(record)


def test_candidate_version_is_required_and_dependency_errors_are_not_hidden(settings):
    kernel, record = candidate(settings)
    record.draft["version"] = "1.0"
    with pytest.raises(ConfigurationError, match="新版本号") as rejected:
        kernel.validate(record)
    assert (rejected.value.code, rejected.value.status) == ("INVALID_CONFIGURATION", 422)
    record.draft["version"] = "2.0"
    record.draft["materials"] = []
    with pytest.raises(ConfigurationError) as rejected:
        kernel.validate(record)
    assert (rejected.value.code, rejected.value.status) == ("INVALID_CONFIGURATION", 422)


def test_chat_to_trial_real_replay_confirmation_save_and_activation_without_review_process(
    settings,
):
    suite = json.loads((FIXTURES / "replay/permit-cases.json").read_text(encoding="utf-8"))
    for case in suite["cases"]:
        case["request"]["profile_version"] = "2.0"
    with TestClient(create_app(settings, model=HappyModel())) as client:
        auth = headers()
        sid = create(client, auth)
        version = client.get(f"/api/review-config/tasks/{sid}").json()["draft"]["version"]
        for case in suite["cases"]:
            case["request"]["profile_version"] = version
        assert (
            client.post(
                "/api/review-config/case-sets",
                headers=auth,
                json={"case_set_id": "permit-v2", "suite": suite},
            ).status_code
            == 200
        )
        command = {
            "client_request_id": "message",
            "expected_revision": 0,
            "text": "修改标题为 Synthetic updated permit，版本 2.0",
        }
        assert (
            client.post(
                f"/api/review-config/tasks/{sid}/messages", headers=auth, json=command
            ).status_code
            == 200
        )
        record = wait(client, sid, auth, "WAITING")
        assert record.pending["kind"] == "browser"
        pending = record.pending
        browser = {
            "client_request_id": "browser",
            "expected_revision": 1,
            **{
                key: pending[key]
                for key in [
                    "snapshot_id",
                    "page_instance_id",
                    "page_url",
                    "structure_digest",
                    "candidate_digest",
                ]
            },
            "fields": {"permit.vin": 1},
            "control_kinds": {"permit.vin": "text"},
        }
        browser["matched_page_id"] = "example_permit"
        path = f"/api/review-config/tasks/{sid}/browser-tasks/{pending['id']}/result"
        stale = {**browser, "page_instance_id": "different-page", "client_request_id": "stale"}
        assert client.post(path, headers=auth, json=stale).status_code == 409
        assert client.post(path, headers=auth, json=browser).status_code == 200
        record = wait(client, sid, auth, "WAITING")
        assert record.state == "READY_FOR_CONFIRMATION", record.error or record.report
        assert all((r["ok"] for r in record.report["replays"]))
        confirm = {
            "client_request_id": "confirm",
            "expected_revision": 1,
            "report_id": record.report["report_id"],
            "candidate_digest": record.report["candidate_digest"],
        }
        path = f"/api/review-config/tasks/{sid}/confirm"
        assert client.post(path, headers=auth, json=confirm).status_code == 200
        record = wait(client, sid, auth, "COMPLETED")
        assert record.receipt and record.state == "PUBLISHED"
        assert client.post(path, headers=auth, json=confirm).status_code == 200
        activation = client.get(f"/api/review-config/tasks/{sid}/activation", headers=auth)
        assert activation.status_code == 200
        assert activation.json()["state"] == "UNAVAILABLE"
        old_report = client.get(f"/api/review-config/tasks/{sid}").json()["report"]
        from tests.support.configuration import SNAPSHOT

        attached = client.post(
            f"/api/review-config/tasks/{sid}/page",
            json={
                "client_request_id": "adoption-scene",
                "expected_revision": 1,
                "snapshot": {**SNAPSHOT, "snapshot_id": "new-adoption-scene"},
            },
        )
        assert attached.status_code == 200, attached.text
        after = client.get(f"/api/review-config/tasks/{sid}").json()
        assert after["report"]["report_id"] == old_report["report_id"]
        assert after["report"]["revision"] == after["revision"] == 1
        assert after["evidence_revision"] == 1
        assert after["snapshot"]["snapshot_id"] == "new-adoption-scene"
        assert after["receipt"] == record.receipt


def test_completed_snapshot_commit_has_durable_receipt(settings):
    kernel, record = candidate(settings)
    receipt = kernel.commit(record, "completed123")
    assert kernel.commit_receipt("completed123") == receipt
    assert kernel.commit_receipt("unknown123") is None


def test_confirmed_save_reconciles_existing_snapshot_receipt(settings):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        auth = headers()
        service = client.app.state.service
        record = client.portal.call(database_candidate, service.kernel, service.store)
        record.pending.update(id="confirmed", operation_id="reconcile123")
        record.turn_status, record.state = "INTERRUPTED", "READY_FOR_CONFIRMATION"
        client.portal.call(service.store.put, record)
        # Simulate a crash after the release transaction, before workflow reconciliation.
        receipt = client.portal.call(service.kernel.commit, record, "reconcile123")
        base = f"/api/review-config/tasks/{record.task_id}"
        command = {"client_request_id": "retry", "expected_revision": record.revision}
        commands = {
            "/messages": {**command, "text": "修改标题"},
            "/draft": {
                **command,
                "operations": [
                    {"op": "upsert_field", "value": {"key": "permit.vin", "label": "新标题"}}
                ],
            },
            "/confirm": {**command, "report_id": "old", "candidate_digest": "old"},
            "/validate": command,
            "/cancel": {},
        }
        for path, payload in commands.items():
            response = client.post(base + path, headers=auth, json=payload)
            assert response.status_code == 409, (path, response.text)
            assert response.json()["code"] == (
                "SESSION_SAVED" if path in {"/validate", "/cancel"} else "COMMIT_PENDING"
            )
        assert (
            client.portal.call(service.store.get, record.task_id).pending["operation_id"]
            == "reconcile123"
        )
        assert client.post(base + "/resume", headers=auth, json=command).status_code == 200
        saved = wait(client, record.task_id, auth, "COMPLETED")
        assert saved.receipt == receipt and saved.pending is None
        assert saved.state == "PUBLISHED"
        assert client.delete(base, headers=auth).status_code == 200
        assert client.portal.call(service.kernel.commit_receipt, "reconcile123") == receipt
