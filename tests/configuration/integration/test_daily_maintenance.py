"""Configuration daily maintenance requirements."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from forj_review_config_service.bootstrap.application import create_app
from forj_review_config_service.contracts.models import ConfigurationError, SessionRecord
from forj_review_config_service.integrations.review_kernel.snapshot import SnapshotKernel
from tests.support.configuration import (
    FIXTURES,
    SNAPSHOT,
    NoModel,
    candidate,
    create,
    wait,
)
from tests.support.postgres import read_page_state


def test_daily_maintenance_rejects_new_page_and_preserves_rollback(settings):
    kernel, record = candidate(settings)
    original = dict(record.draft)
    record.draft["page_id"] = "brand_new_page"
    with pytest.raises(ConfigurationError, match="已接入页面") as rejected:
        kernel.validate(record)
    assert (rejected.value.code, rejected.value.status) == ("INVALID_CONFIGURATION", 422)
    record.draft = original
    record.report = kernel.validate(record)
    kernel.commit(record, "update123")
    rollback = SessionRecord(
        session_id="rollback",
        snapshot=SNAPSHOT,
        activation_only=True,
        baseline_digest=kernel.baseline(),
        draft=kernel.rollback_draft("example_permit", "1.0"),
    )
    rollback.report = kernel.validate(rollback)
    kernel.commit(rollback, "rollback123")
    assert kernel.context("example_permit")["reference"]["version"] == "1.0"
    assert kernel.context("example_permit")["source"]["schema_version"] == "2.0"


def test_added_local_field_can_be_deleted_without_template_deltas(settings):
    from forj_review_config_service.application.drafts import apply_operations
    from forj_review_config_service.contracts.models import DraftOperation

    kernel, record = candidate(settings)
    apply_operations(
        record,
        [
            DraftOperation(
                op="upsert_field",
                value={
                    "key": "permit.local",
                    "label": "Local",
                    "mode": "PAGE_AUXILIARY",
                    "aliases": ["Local"],
                    "evidence": [
                        {"material": "permit_card", "field": "permit.local", "scope": "permit"}
                    ],
                },
            ),
            DraftOperation(op="remove_field", value={"key": "permit.local"}),
        ],
        kernel,
    )
    assert [f["key"] for f in record.draft["fields"]] == ["permit.vin"]
    assert not record.draft.get("remove_fields")
    with pytest.raises(ConfigurationError):
        apply_operations(
            record, [DraftOperation(op="remove_field", value={"key": "permit.vin"})], kernel
        )


def test_offline_configuration_kernel_never_loads_review_model_credentials(settings, monkeypatch):

    def forbidden(*args, **kwargs):
        raise AssertionError("offline configuration must not load review service credentials")

    monkeypatch.setattr("forj_review_service.runtime.review_service.load_qwen_config", forbidden)
    kernel, record = candidate(settings)
    suite = json.loads((FIXTURES / "replay/permit-cases.json").read_text(encoding="utf-8"))
    for case in suite["cases"]:
        case["request"]["profile_version"] = "2.0"
    kernel.register_cases("isolated", suite)
    assert asyncio.run(kernel.replay(record, "isolated"))["ok"]


def test_configuration_service_receives_shared_decision_policy(settings):
    kernel = SnapshotKernel(
        read_page_state(settings.pages_dir), settings.cases_dir, "http://127.0.0.1:26102"
    )
    capabilities = kernel.capabilities()
    assert capabilities["decision_policy"]["version"] == "2"
    assert "必须为 true" in capabilities["decision_policy"]["uncertain_requires_review"]
    field = capabilities["schema"]["$defs"]["FieldConfig"]["properties"][
        "uncertain_requires_review"
    ]
    assert field["default"] is True
    assert "必须为 true" in field["description"]


@pytest.mark.parametrize("publisher", ["workbench", "cli"])
def test_manual_field_trial_replay_cli_export_and_save_are_model_free(
    settings, publisher, monkeypatch, capsys
):
    from copy import deepcopy

    from forj_review_service.runtime.configuration.offline import (
        prepare_maintenance,
        save_maintenance,
        verify_maintenance,
    )

    with TestClient(create_app(settings, model=NoModel())) as client:
        sid = create(client, {})
        base = f"/api/review-config/sessions/{sid}"
        assert (
            client.post(
                base + "/draft",
                json={
                    "client_request_id": "edit",
                    "expected_revision": 0,
                    "operations": [
                        {
                            "op": "upsert_field",
                            "value": {"key": "permit.vin", "label": "人工维护车辆识别码"},
                        }
                    ],
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                base + "/mappings",
                json={
                    "client_request_id": "map",
                    "expected_revision": 1,
                    "fields": {},
                    "materials": {},
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                base + "/trial", json={"client_request_id": "trial", "expected_revision": 2}
            ).status_code
            == 200
        )
        record = client.portal.call(client.app.state.service.store.get, sid)
        assert record.model_calls == 0 and record.pending["manual"]
        pending = record.pending
        facts = {
            key: pending[key]
            for key in (
                "snapshot_id",
                "page_instance_id",
                "page_url",
                "structure_digest",
                "candidate_digest",
            )
        }
        facts.update(
            fields={"permit.vin": 1},
            control_kinds={},
            samples={},
            matched_page_id="example_permit",
            diagnostics=[],
        )
        assert (
            client.post(
                base + f"/browser-tasks/{pending['id']}/result",
                json={"client_request_id": "facts", "expected_revision": 2, **facts},
            ).status_code
            == 200
        )
        record = client.portal.call(client.app.state.service.store.get, sid)
        assert record.pending is None and record.model_calls == 0
        suite = json.loads((FIXTURES / "replay/permit-cases.json").read_text(encoding="utf-8"))
        for case in suite["cases"]:
            case["request"]["profile_version"] = record.draft["version"]
        assert (
            client.post(
                "/api/review-config/case-sets", json={"case_set_id": "manual", "suite": suite}
            ).status_code
            == 200
        )
        assert (
            client.post(
                base + "/replay",
                json={
                    "client_request_id": "replay",
                    "expected_revision": 2,
                    "case_set_id": "local/manual",
                },
            ).status_code
            == 200
        )
        response = client.post(
            base + "/validate", json={"client_request_id": "validate", "expected_revision": 2}
        )
        assert response.status_code == 200 and response.json()["ok"], response.text
        report = response.json()
        bundle = client.get(base + "/export").json()
        files = client.portal.call(client.app.state.service.kernel.pages.get)
        plan = prepare_maintenance(files, "example_permit", candidate=bundle["candidate"])
        staged, receipt = asyncio.run(
            save_maintenance(files, plan, bundle["evidence"], suite, "offline1")
        )
        assert receipt["state"] == "PUBLISHED" and receipt["source"] == "manual_cli"
        assert asyncio.run(
            save_maintenance(staged, plan, bundle["evidence"], suite, "offline1")
        ) == (staged, receipt)
        for mutate in (
            lambda e: e["proof"]["facts"]["fields"].update({"permit.vin": True}),
            lambda e: e["proof"].update(expires_at="2020-01-01T00:00:00+00:00"),
            lambda e: e["proof"].update(candidate_digest="different"),
            lambda e: e["proof"]["facts"].update(page_instance_id="different"),
        ):
            invalid = deepcopy(bundle["evidence"])
            mutate(invalid)
            with pytest.raises(ValueError):
                asyncio.run(verify_maintenance(files, plan, invalid, suite))
        if publisher == "cli":
            from types import SimpleNamespace

            from scripts.dev import page_tools

            directory = settings.state_dir / "manual-export"
            page_tools.export_state(directory, files)
            (directory / "plan.json").write_text(json.dumps(plan), encoding="utf8")
            (directory / "evidence.json").write_text(json.dumps(bundle), encoding="utf8")
            (directory / "cases.json").write_text(json.dumps(suite), encoding="utf8")
            monkeypatch.setattr(
                page_tools,
                "load_connection_settings",
                lambda: SimpleNamespace(conninfo=lambda: settings.database_url),
            )
            monkeypatch.setattr(
                "sys.argv",
                [
                    "page_tools",
                    "publish",
                    "--schema",
                    settings.database_schema,
                    "--directory",
                    str(directory),
                    "--backup-dir",
                    str(directory / "before"),
                    "--trial-json",
                    str(directory / "evidence.json"),
                    "--cases",
                    str(directory / "cases.json"),
                    "--operation-id",
                    "manualcli1",
                    "--expected-revision",
                    str(files.revision),
                ],
            )
            for _ in range(2):
                page_tools.main()
                result = json.loads(capsys.readouterr().out)
                assert result["state"] == "PUBLISHED" and result["release_id"]
            context = client.portal.call(client.app.state.service.kernel.context, "example_permit")
            assert context["source"]["fields"][0]["label"] == "人工维护车辆识别码"
            return
        assert (
            client.post(
                base + "/confirm",
                json={
                    "client_request_id": "confirm",
                    "expected_revision": 2,
                    "report_id": report["report_id"],
                    "candidate_digest": report["candidate_digest"],
                },
            ).status_code
            == 200
        )
        saved = wait(client, sid, {}, "COMPLETED")
        assert saved.receipt and saved.model_calls == 0 and saved.state == "PUBLISHED"
        assert (
            client.post(
                base + "/draft",
                json={
                    "client_request_id": "again",
                    "expected_revision": 2,
                    "operations": [{"op": "remove_field", "value": {"key": "permit.vin"}}],
                },
            ).status_code
            == 409
        )


def test_session_creation_and_daily_operations_reject_developer_scope(settings):
    with TestClient(create_app(settings, model=NoModel())) as client:
        response = client.post(
            "/api/review-config/sessions",
            json={"client_request_id": "missing", "snapshot": SNAPSHOT},
        )
        assert response.status_code == 422 and response.json()["code"] == "EXISTING_PAGE_REQUIRED"
        sid = create(client, {})
        base = f"/api/review-config/sessions/{sid}"
        for operation in (
            {"op": "set_page_metadata", "value": {"page_id": "new"}},
            {"op": "set_page_options", "value": {"unsupported_algorithm": "new"}},
            {"op": "upsert_field", "value": {"key": "permit.vin", "writable": True}},
        ):
            result = client.post(
                base + "/draft",
                json={
                    "client_request_id": json.dumps(operation),
                    "expected_revision": 0,
                    "operations": [operation],
                },
            )
            assert result.status_code == 422, result.text
        assert client.get(base).json()["revision"] == 0
