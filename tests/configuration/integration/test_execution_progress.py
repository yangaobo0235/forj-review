"""Committed execution events can be paged and traced without leaking business content."""

from fastapi.testclient import TestClient

from forj_review_config_service.bootstrap.application import create_app
from forj_review_config_service.contracts.models import ConfigurationError
from tests.support.configuration import FakeModel, call, create, wait


def test_activity_history_and_console_share_committed_call_ids(settings, capsys):
    model = FakeModel(
        [
            call("read_draft", {}, "read"),
            call("read_draft", {}, "again"),
            {"role": "assistant", "content": "已读取草稿，未进行修改。"},
        ]
    )
    with TestClient(create_app(settings, model=model)) as client:
        sid = create(client, {})
        base = f"/api/review-config/tasks/{sid}"
        client.post(
            base + "/messages",
            json={
                "client_request_id": "inspect",
                "expected_revision": 0,
                "text": "查询草稿 private-contact-DO-NOT-LOG",
            },
        )
        record = wait(client, sid, {}, status="COMPLETED")
        assert record.revision == 0 and record.receipt is None
        all_events = client.get(base + "/events").json()
        tail = client.get(base + "/events?before_seq=0&limit=3").json()
        older = client.get(base + f"/events?before_seq={tail[0]['seq']}&limit=200").json()
        assert [e["seq"] for e in older + tail] == [e["seq"] for e in all_events]
        progress = client.get(base + "/events?include_messages=false").json()
        assert progress == [event for event in all_events if event["type"] != "message"]
        assert all("created_at" in event and "run_id" in event for event in all_events)
        completed = [e for e in all_events if e["type"] == "tool_completed"]
        assert len(completed) == 2 and completed[-1]["data"]["reused_from"]
        assert "不产生配置修改" in completed[0]["data"]["activity"]["detail"]
        view = client.get(base).json()
        assert view["execution_mode"] == "CONFIGURATION_SERVICE"
        assert view["environment"] == settings.database_schema
        assert "尚未生成配置修改" in view["execution_summary"]["detail"]
        assert client.get(base + "/events?limit=999").status_code == 422
    file_log = (settings.state_dir / "review-config-service.log").read_text(encoding="utf-8")
    console_log = capsys.readouterr().err
    for log in (file_log, console_log):
        assert "event=model_started" in log and "event=turn_stopped" in log
        assert sid in log and completed[0]["data"]["call_id"] in log
        assert "private-contact-DO-NOT-LOG" not in log


def test_failed_model_call_remains_traceable_and_preserves_unmodified_draft(settings):
    class LimitedModel:
        async def complete(self, messages, tools):
            raise ConfigurationError("MODEL_RATE_LIMITED", "模型暂时限流，请稍后继续", 503)

    with TestClient(create_app(settings, model=LimitedModel())) as client:
        sid = create(client, {})
        base = f"/api/review-config/tasks/{sid}"
        client.post(
            base + "/messages",
            json={"client_request_id": "limited", "expected_revision": 0, "text": "只改字段名称"},
        )
        record = wait(client, sid, {}, status="FAILED")
        events = client.get(base + "/events").json()
        started = next(event for event in events if event["type"] == "model_started")
        failed = next(event for event in events if event["type"] == "model_failed")
        assert started["data"]["model_call_id"] == failed["data"]["model_call_id"]
        assert failed["data"]["code"] == "MODEL_RATE_LIMITED" and failed["data"]["duration_ms"] >= 0
        assert record.revision == 0 and record.error_code == "MODEL_RATE_LIMITED"
