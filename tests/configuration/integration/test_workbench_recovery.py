"""Related tasks preserve goals and isolate new validation from historical facts."""

import asyncio
import time

from fastapi.testclient import TestClient

from forj_review_config_service.application.tasks import manage_requirements, receive_message
from forj_review_config_service.bootstrap.application import create_app
from tests.support.configuration import SNAPSHOT, FakeModel, call, wait


def create(client):
    return client.post(
        "/api/review-config/tasks",
        json={
            "client_request_id": "new",
            "page_id": "example_permit",
            "snapshot": SNAPSHOT,
        },
    ).json()["task_id"]


def test_rebase_and_explicit_split_preserve_original_task_and_do_not_reuse_proofs(settings):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        sid = create(client)
        base = f"/api/review-config/tasks/{sid}"
        service = client.app.state.service
        record = client.portal.call(service.store.get, sid)
        for text in ("把 VIN 改为车辆识别号码（新名称）", "增加新算法"):
            goal = receive_message(record, text)
            manage_requirements(record, {"message_id": goal["message_id"], "action": "ACCEPT"})
        client.portal.call(service.store.put, record)
        edit = client.post(
            base + "/draft",
            json={
                "client_request_id": "edit",
                "expected_revision": 0,
                "operations": [
                    {
                        "op": "upsert_field",
                        "value": {"key": "permit.vin", "label": "车辆识别号码（新名称）"},
                    }
                ],
            },
        )
        assert edit.status_code == 200, edit.text
        before = client.get(base).json()
        preview = client.get(base + "/rebase").json()
        assert preview["conflicts"] == []
        stale_intent = client.post(
            base + "/split",
            json={
                "client_request_id": "old-window",
                "expected_revision": 1,
                "expected_intent_revision": 1,
                "requirement_ids": [before["requirements"][0]["requirement_id"]],
                "independence_reason": "旧窗口中的拆分理由",
            },
        )
        assert stale_intent.status_code == 409 and stale_intent.json()["code"] == "STALE_INTENT"
        invalid = client.post(
            base + "/rebase",
            json={
                "client_request_id": "wrong",
                "expected_revision": 1,
                "expected_intent_revision": 2,
                "baseline_digest": "old",
                "resolutions": {},
            },
        )
        assert invalid.status_code == 409
        body = {
            "client_request_id": "rebase",
            "expected_revision": 1,
            "expected_intent_revision": 2,
            "baseline_digest": preview["baseline_digest"],
            "resolutions": {},
        }
        response = client.post(base + "/rebase", json=body)
        assert response.status_code == 200, response.text
        child_id = response.json()["task_id"]
        assert client.post(base + "/rebase", json=body).json()["task_id"] == child_id
        child = client.get(f"/api/review-config/tasks/{child_id}").json()
        assert child["parent_task_id"] == sid
        assert sorted(r["text"] for r in child["requirements"]) == sorted(
            r["text"] for r in before["requirements"]
        )
        assert child["draft"]["fields"][0]["label"] == "车辆识别号码（新名称）"
        assert child["report"] is None and child["receipt"] is None
        assert (
            child["snapshot"] == before["snapshot"] and child["capture_id"] == before["capture_id"]
        )
        original = client.get(base).json()
        assert (
            original["draft"] == before["draft"]
            and original["requirements"] == before["requirements"]
        )
        split = client.post(
            base + "/split",
            json={
                "client_request_id": "split",
                "expected_revision": 1,
                "expected_intent_revision": 2,
                "requirement_ids": [before["requirements"][0]["requirement_id"]],
                "independence_reason": "名称不依赖新算法，同意先独立完成名称验收",
            },
        )
        assert split.status_code == 200, split.text
        part = client.get("/api/review-config/tasks/" + split.json()["task_id"]).json()
        assert len(part["requirements"]) == 1 and part["parent_task_id"] == sid
        assert part["draft"]["fields"][0]["label"] != "车辆识别号码（新名称）"
        assert len(client.get(base).json()["requirements"]) == 2
        assert all("task_record" not in s for s in client.get("/api/review-config/tasks").json())


def test_running_correction_cancels_old_model_before_new_candidate_is_saved(settings):
    class Model:
        calls = 0
        cancelled = False

        async def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return call(
                    "edit_draft",
                    {
                        "expected_revision": 0,
                        "operations": [
                            {
                                "op": "upsert_field",
                                "value": {"key": "permit.vin", "label": "旧名称"},
                            }
                        ],
                    },
                )
            if self.calls == 2:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            if self.calls == 3:
                return call(
                    "edit_draft",
                    {
                        "expected_revision": 1,
                        "operations": [
                            {
                                "op": "upsert_field",
                                "value": {"key": "permit.vin", "label": "最终名称"},
                            }
                        ],
                    },
                )
            return {"role": "assistant", "content": "已按更正保留候选，尚未发布。"}

    model = Model()
    with TestClient(create_app(settings, model=model)) as client:
        sid = create(client)
        base = f"/api/review-config/tasks/{sid}"
        client.post(
            base + "/messages",
            json={"client_request_id": "first", "expected_revision": 0, "text": "修改名称"},
        )
        for _ in range(200):
            if model.calls == 2:
                break
            time.sleep(0.01)
        assert model.calls == 2
        response = client.post(
            base + "/messages",
            json={
                "client_request_id": "correction",
                "expected_revision": 0,
                "expected_intent_revision": 0,
                "text": "更正为最终名称",
            },
        )
        assert response.status_code == 200, response.text
        record = wait(client, sid, {})
        assert model.cancelled and record.revision == 2
        assert record.draft["fields"][0]["label"] == "最终名称"
        assert any("更正为最终名称" in m.get("content", "") for m in record.messages)
        assert record.receipt is None
