"""URL-only capture, independent history and relevant material failures in PostgreSQL."""

import pytest
from fastapi.testclient import TestClient

from forj_review_config_service.bootstrap.application import create_app
from forj_review_config_service.contracts.models import ConfigurationError
from tests.support.configuration import SNAPSHOT, FakeModel, call, wait

BASE = "/api/review-config"


def capture(client, *, key="scene", samples=True, diagnostics=None):
    snapshot = {
        **SNAPSHOT,
        "snapshot_id": key,
        "detected_page_id": "example_permit",
        "configuration_version": "old-version",
        "record_identity": {"application_id": "synthetic-42"},
        "fields": [{"candidate_id": "vin", "label": "VIN", "value": "synthetic-value"}],
        "diagnostics": diagnostics or [],
        "samples": [
            {
                "sample_id": "license",
                "url": "https://expired.example.test/image?expired=1",
                "label": "行驶证",
            }
        ]
        if samples
        else [],
    }
    response = client.post(
        BASE + "/captures",
        json={"client_request_id": key, "extension_id": "a" * 32, "snapshot": snapshot},
    )
    assert response.status_code == 200, response.text
    return response.json()["capture_id"]


def create_task(client, capture_id, key="task"):
    return client.post(
        BASE + "/tasks",
        json={"client_request_id": key, "page_id": "example_permit", "capture_id": capture_id},
    )


def test_url_capture_starts_without_contacting_or_downloading_images(settings):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        cid = capture(client)
        path = BASE + f"/captures/{cid}"
        saved = client.get(path).json()
        assert "images" not in saved and "ready" not in saved
        assert (
            saved["snapshot"]["samples"][0]["url"] == "https://expired.example.test/image?expired=1"
        )
        assert (
            client.post(path + "/images/license", content=b"no binary persistence").status_code
            == 404
        )
        assert client.get(path + "/images/license").status_code == 404
        created = create_task(client, cid)
        assert created.status_code == 200, created.text
        task = client.get(BASE + "/tasks/" + created.json()["task_id"]).json()
        assert task["baseline_version"] == "1.0"
        assert task["snapshot"]["configuration_version"] == "old-version"
        assert task["snapshot"]["record_identity"] == {"application_id": "synthetic-42"}
    with TestClient(create_app(settings, model=FakeModel())) as client:
        assert client.get(path).json()["snapshot"] == saved["snapshot"]
        assert create_task(client, cid, "second-task").status_code == 200


@pytest.mark.parametrize("diagnostics", [[], ["字段候选被截断，请核对相关区域"]])
def test_local_diagnostics_are_visible_without_blocking_investigation(settings, diagnostics):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        cid = capture(client, samples=False, diagnostics=diagnostics)
        saved = client.get(BASE + f"/captures/{cid}").json()
        assert saved["snapshot"]["diagnostics"] == diagnostics
        assert create_task(client, cid).status_code == 200


def test_old_capture_survives_replacement_without_incrementing_draft_revision(settings):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        first = capture(client)
        sid = create_task(client, first).json()["task_id"]
        before = client.get(BASE + f"/tasks/{sid}").json()
        second = capture(client, key="second", samples=False)
        source = client.get(BASE + f"/captures/{second}").json()
        response = client.post(
            BASE + f"/tasks/{sid}/page",
            json={
                "client_request_id": "replace",
                "expected_revision": 0,
                "capture_id": second,
                "snapshot": source["snapshot"],
            },
        )
        assert response.status_code == 200, response.text
        after = client.get(BASE + f"/tasks/{sid}").json()
        assert after["draft"] == before["draft"] and after["revision"] == 0
        assert after["evidence_revision"] == 1 and after["row_version"] > before["row_version"]
        retained = client.get(BASE + f"/tasks/{sid}/captures").json()
        assert {item["capture_id"] for item in retained} == {first, second}
        assert client.delete(BASE + f"/captures/{first}").json()["code"] == "CAPTURE_IN_USE"
        client.portal.call(client.app.state.service.retain)
        assert client.get(BASE + f"/captures/{first}").status_code == 200
        assert client.delete(BASE + f"/tasks/{sid}").status_code == 200
        assert client.delete(BASE + f"/captures/{first}").status_code == 200


def test_material_analysis_passes_only_requested_url_with_observation_provenance(settings):
    class Model(FakeModel):
        async def analyze(self, samples, focus):
            assert len(samples) == 1 and samples[0]["sample_id"] == "license"
            assert samples[0]["url"] == "https://expired.example.test/image?expired=1"
            assert "content_digest" not in samples[0]
            return {"observations": [{"sample_id": "license", "type": "vehicle_license"}]}

    model = Model(
        [
            call("analyze_materials", {"sample_ids": ["license"], "focus": "发动机号"}),
            {"role": "assistant", "content": "已读取材料，修改尚未发布"},
        ]
    )
    with TestClient(create_app(settings, model=model)) as client:
        cid = capture(client)
        sid = create_task(client, cid).json()["task_id"]
        client.post(
            BASE + f"/tasks/{sid}/messages",
            json={"client_request_id": "analyze", "expected_revision": 0, "text": "查看发动机号"},
        )
        result = wait(client, sid, {})
        assert result.turn_status == "COMPLETED", result.error
        assert result.reference_samples == ["license"]
        assert "required_samples" not in result.model_dump()
        observation = next(
            item for item in result.tool_results.values() if item.get("observations")
        )
        assert observation["sources"][0]["capture_id"] == cid
        assert observation["sources"][0]["observed_at"]
        assert "content_digest" not in observation["sources"][0]


def test_related_url_failure_retains_task_and_does_not_become_complexity_route(settings):
    class Model(FakeModel):
        async def analyze(self, samples, focus):
            raise ConfigurationError("MATERIAL_URL_UNAVAILABLE", "相关地址无法访问，请补采", 422)

    model = Model(
        [
            call("analyze_materials", {"sample_ids": ["license"], "focus": "登记日期"}),
            {"role": "assistant", "content": "相关材料待补采，已有目标和草稿保留"},
        ]
    )
    with TestClient(create_app(settings, model=model)) as client:
        sid = create_task(client, capture(client)).json()["task_id"]
        client.post(
            BASE + f"/tasks/{sid}/messages",
            json={"client_request_id": "analyze", "expected_revision": 0, "text": "检查登记日期"},
        )
        result = wait(client, sid, {})
        assert result.turn_status == "COMPLETED" and result.route != "HUMAN_HANDOFF"
        assert any(
            r.get("code") == "MATERIAL_URL_UNAVAILABLE" for r in result.tool_results.values()
        )
        assert result.draft and result.receipt is None


@pytest.mark.parametrize(
    "url",
    [
        "data:image/png;base64,c2VjcmV0",
        "blob:https://example.test/id",
        "https://user:secret@example.test/a",
    ],
)
def test_binary_temporary_and_credential_urls_never_enter_capture_storage(settings, url):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        result = client.post(
            BASE + "/captures",
            json={
                "client_request_id": "invalid",
                "extension_id": "a" * 32,
                "snapshot": {**SNAPSHOT, "samples": [{"sample_id": "bad", "url": url}]},
            },
        )
        assert result.status_code == 422
        assert client.get(BASE + "/captures").json() == []
