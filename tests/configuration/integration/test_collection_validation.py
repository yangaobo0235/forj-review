"""Configuration collection validation requirements."""

from fastapi.testclient import TestClient

from forj_review_config_service.bootstrap.application import create_app
from tests.support.configuration import (
    SNAPSHOT,
    FakeModel,
    browser_evidence,
    candidate,
    wait,
)
from tests.support.kernel import AsyncSnapshotKernel


def test_refresh_rejects_old_trial_but_retains_history_and_rechecks_raw_facts(settings):
    kernel, record = candidate(settings)
    with TestClient(
        create_app(settings, model=FakeModel(), kernel=AsyncSnapshotKernel(kernel))
    ) as client:
        service = client.app.state.service
        client.portal.call(service.store.put, record)
        record.tool_results["old-trial"] = browser_evidence(kernel, record)
        client.portal.call(service.store.put, record)
        assert not any(
            ("试采集" in b for b in client.portal.call(service.validate_record, record)["blockers"])
        )
        path = f"/api/review-config/sessions/{record.session_id}/page"
        assert (
            client.post(
                path,
                json={
                    "client_request_id": "new-page",
                    "expected_revision": 0,
                    "snapshot": {**SNAPSHOT, "snapshot_id": "new", "structure_digest": "changed"},
                },
            ).status_code
            == 200
        )
        current = client.portal.call(service.store.get, record.session_id)
        assert "old-trial" in current.tool_results
        assert any(
            (
                "试采集" in b
                for b in client.portal.call(service.validate_record, current)["blockers"]
            )
        )
        current.tool_results["new-trial"] = browser_evidence(kernel, current)
        assert not any(
            (
                "试采集" in b
                for b in client.portal.call(service.validate_record, current)["blockers"]
            )
        )
        current.tool_results["new-trial"]["facts"]["fields"] = {}
        assert any(
            (
                "试采集" in b
                for b in client.portal.call(service.validate_record, current)["blockers"]
            )
        )


def test_trial_requires_current_material_mapping_and_rejects_legacy_ok(settings):
    from forj_review_config_service.application.validation import current_trial

    kernel, record = candidate(settings)
    preview = kernel.validate(record)
    assert not current_trial(
        record,
        preview,
        {
            "kind": "collection_trial",
            "ok": True,
            "candidate_digest": preview["candidate_digest"],
            "revision": 0,
        },
        kernel,
    )
    record.selected_samples = ["sample"]
    record.decisions = [
        {"sample_id": "sample", "material": "old", "status": "RESOLVED", "revision": 0}
    ]
    proof = browser_evidence(kernel, record)
    proof["facts"]["samples"] = {"sample": "old"}
    assert current_trial(record, preview, proof, kernel)
    record.decisions[0]["material"] = "new"
    assert not current_trial(record, preview, proof, kernel)


def test_expired_proof_before_commit_revokes_confirmation_and_allows_edit(settings):
    kernel, record = candidate(settings)
    record.pending = {
        "id": "confirmed",
        "kind": "confirmation",
        "confirmed": True,
        "operation_id": "expired123",
        "candidate_digest": record.report["candidate_digest"],
    }
    with TestClient(
        create_app(settings, model=FakeModel(), kernel=AsyncSnapshotKernel(kernel))
    ) as client:
        service = client.app.state.service
        client.portal.call(service.store.put, record)
        base = f"/api/review-config/sessions/{record.session_id}"
        client.post(base + "/resume", json={"client_request_id": "resume", "expected_revision": 0})
        current = wait(client, record.session_id, {}, "FAILED")
        assert current.pending is None and current.state == "DRAFTING"
        assert (
            client.post(
                base + "/draft",
                json={
                    "client_request_id": "edit",
                    "expected_revision": 0,
                    "operations": [
                        {"op": "upsert_field", "value": {"key": "permit.vin", "label": "editable"}}
                    ],
                },
            ).status_code
            == 200
        )
        assert not (settings.pages_dir / ".transactions/expired123/journal.json").exists()
