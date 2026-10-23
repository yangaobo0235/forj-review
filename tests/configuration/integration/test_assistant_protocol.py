"""Configuration assistant protocol requirements."""

import json

import pytest
from fastapi.testclient import TestClient

from forj_review_config_service.bootstrap.application import create_app
from forj_review_config_service.contracts.models import ConfigurationError, TaskRecord
from tests.support.configuration import (
    FakeModel,
    call,
    candidate,
    create,
    headers,
    wait,
)
from tests.support.kernel import AsyncSnapshotKernel


def test_question_survives_restart_and_uses_native_tool_result_protocol(settings):
    question = call(
        "ask_user", {"question": "注册日期还是发证日期？", "options": ["注册日期", "发证日期"]}
    )
    with TestClient(create_app(settings, model=FakeModel([question]))) as client:
        auth = headers()
        sid = create(client, auth)
        command = {"client_request_id": "msg1", "expected_revision": 0, "text": "审核日期"}
        assert (
            client.post(
                f"/api/review-config/tasks/{sid}/messages", headers=auth, json=command
            ).status_code
            == 200
        )
        record = wait(client, sid, auth, "WAITING")
        assert record.pending["question"]
        question_id = record.pending["id"]
    with TestClient(create_app(settings, model=FakeModel())) as client:
        record = client.portal.call(client.app.state.service.store.get, sid)
        assert record.pending["id"] == question_id
        command = {"client_request_id": "answer1", "expected_revision": 0, "answer": "注册日期"}
        path = f"/api/review-config/tasks/{sid}/interactions/{question_id}/answer"
        assert client.post(path, headers=auth, json=command).status_code == 200
        record = wait(client, sid, auth, "COMPLETED")
        assert record.pending is None
        index = next((i for i, m in enumerate(record.messages) if m.get("tool_calls")))
        assert record.messages[index + 1]["role"] == "tool"
        assert "注册日期" in record.messages[index + 1]["content"]
        assert client.post(path, headers=auth, json=command).status_code == 200
        history = client.get(f"/api/review-config/tasks/{sid}").json()["chat_messages"]
        assert [m["content"] for m in history] == [
            "审核日期",
            "注册日期还是发证日期？",
            "注册日期",
            "已读取要求，草稿待配置",
        ]
    with TestClient(create_app(settings, model=FakeModel())) as client:
        assert client.get(f"/api/review-config/tasks/{sid}").json()["chat_messages"] == history


def test_model_budget_is_interrupted_and_cancelled_question_does_not_resurrect(settings):

    settings = settings.model_copy(update={"max_calls": 1})
    with TestClient(create_app(settings, model=FakeModel([call("find_templates")]))) as client:
        auth = headers()
        sid = create(client, auth)
        client.post(
            f"/api/review-config/tasks/{sid}/messages",
            headers=auth,
            json={"client_request_id": "message", "expected_revision": 0, "text": "检查配置"},
        )
        record = wait(client, sid, auth, "INTERRUPTED")
        assert "预算" in record.error
        assert record.state != "READY_FOR_CONFIRMATION"


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("ask_user", {"question": "日期？", "options": "注册日期"}),
        ("ask_user", {"question": "日期？", "options": [1]}),
        ("get_configuration", {"page_id": 1}),
        ("analyze_materials", {"sample_ids": "sample", "focus": ""}),
        ("edit_draft", {"expected_revision": True, "operations": []}),
        (
            "edit_draft",
            {
                "expected_revision": "0",
                "operations": [{"op": "remove_field", "value": {"key": "x"}}],
            },
        ),
        ("run_replay", {"case_set_id": {"id": "x"}}),
        ("record_decision", {"status": "WRONG", "reason": "r", "evidence_refs": ["snapshot"]}),
        ("get_page_snapshot", {"unexpected": True}),
    ],
)
def test_tools_reject_invalid_types_before_any_side_effect(name, args):
    from forj_review_config_service.tools.arguments import validate_arguments

    with pytest.raises(ConfigurationError) as exc:
        validate_arguments(name, args)
    assert exc.value.code == "INVALID_ARGUMENTS"


def test_bad_question_returns_tool_error_then_model_can_correct_it(settings):
    replies = [
        call("ask_user", {"question": "日期？", "options": "注册日期"}),
        call("ask_user", {"question": "日期？", "options": ["注册日期"]}),
    ]
    with TestClient(create_app(settings, model=FakeModel(replies))) as client:
        sid = create(client, {})
        client.post(
            f"/api/review-config/tasks/{sid}/messages",
            json={"client_request_id": "m", "expected_revision": 0, "text": "配置日期"},
        )
        record = wait(client, sid, {}, "WAITING")
        assert record.pending["options"] == ["注册日期"]
        failures = [r for r in record.tool_results.values() if r.get("code") == "INVALID_ARGUMENTS"]
        assert len(failures) == 1
        events = client.portal.call(client.app.state.service.store.events, sid, 0)
        assert any(
            (
                e["type"] == "tool_completed"
                and e["data"].get("call_id")
                and (e["data"].get("code") == "INVALID_ARGUMENTS")
                for e in events
            )
        )


def test_reused_provider_call_id_executes_distinct_operations_with_paired_responses(settings):
    replies = [
        call("get_page_snapshot"),
        call(
            "edit_draft",
            {
                "expected_revision": 0,
                "operations": [
                    {"op": "upsert_field", "value": {"key": "permit.vin", "label": "updated"}}
                ],
            },
        ),
        {"role": "assistant", "content": "草稿已更新"},
    ]
    with TestClient(create_app(settings, model=FakeModel(replies))) as client:
        sid = create(client, {})
        client.post(
            f"/api/review-config/tasks/{sid}/messages",
            json={"client_request_id": "m", "expected_revision": 0, "text": "改标题"},
        )
        record = wait(client, sid, {}, "COMPLETED")
        assert record.revision == 1 and record.draft["fields"][0]["label"] == "updated"
        calls = [call for m in record.messages for call in m.get("tool_calls", [])]
        assert len({c["id"] for c in calls}) == 2
        for c in calls:
            assert sum((m.get("tool_call_id") == c["id"] for m in record.messages)) == 1


def test_crash_after_persisting_call_resumes_same_internal_identity_once(settings):
    from forj_review_config_service.application.conversation import normalize_reply

    kernel, record = candidate(settings)
    message = normalize_reply(
        call(
            "edit_draft",
            {
                "expected_revision": 0,
                "operations": [
                    {"op": "upsert_field", "value": {"key": "permit.vin", "label": "resumed"}}
                ],
            },
        )
    )
    record.messages.append(message)
    call_id = message["tool_calls"][0]["id"]
    with TestClient(
        create_app(
            settings,
            model=FakeModel([{"role": "assistant", "content": "done"}] * 2),
            kernel=AsyncSnapshotKernel(kernel),
        )
    ) as client:
        client.portal.call(client.app.state.service.store.put, record)
        base = f"/api/review-config/tasks/{record.task_id}/resume"
        client.post(base, json={"client_request_id": "resume1", "expected_revision": 0})
        first = wait(client, record.task_id, {}, "COMPLETED")
        assert first.revision == 1 and call_id in first.tool_results
        client.post(base, json={"client_request_id": "resume2", "expected_revision": 1})
        second = wait(client, record.task_id, {}, "COMPLETED")
        assert second.revision == 1
        assert sum((m.get("tool_call_id") == call_id for m in second.messages)) == 1


def test_question_options_reject_scalar_instead_of_repairing_invalid_contract(settings):
    _, record = candidate(settings)
    old = record.model_dump(mode="json")
    old["pending"] = {
        "id": "legacy",
        "tool_call_id": "legacy",
        "kind": "question",
        "revision": 0,
        "question": "日期？",
        "options": "注册日期",
    }
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TaskRecord.model_validate(old)


@pytest.mark.parametrize(
    "reply",
    [
        {"role": "assistant", "tool_calls": [{}]},
        {"role": "assistant", "tool_calls": "broken"},
        {"role": "assistant", "content": {"text": "broken"}},
    ],
)
def test_malformed_model_call_is_rejected_before_persisting(reply):
    from forj_review_config_service.application.conversation import normalize_reply

    with pytest.raises(ConfigurationError) as exc:
        normalize_reply(reply)
    assert exc.value.code == "MODEL_FORMAT"


def test_question_projection_omits_cancelled_answers_and_deduplicates_question(settings):
    from forj_review_config_service.application.conversation import chat_messages, tool_message

    _, record = candidate(settings)
    question = call("ask_user", {"question": "日期？", "options": []})
    question["content"] = "日期？"
    record.messages.append(question)
    tool_message(record, "call1", {"status": "CANCELLED"})
    assert chat_messages(record) == [{"role": "assistant", "content": "日期？"}]
    record.messages[-1]["content"] = json.dumps({"answer": "注册日期"}, ensure_ascii=False)
    assert chat_messages(record) == [
        {"role": "assistant", "content": "日期？"},
        {"role": "user", "content": "注册日期"},
    ]


def test_error_logs_keep_locations_and_call_id_without_private_payload(settings, caplog):
    import logging

    from forj_review_config_service.application.diagnostics import log_failure

    private = "private-signed-url-or-certificate"
    with caplog.at_level(logging.ERROR):
        try:
            raise ValueError(private)
        except ValueError as exc:
            log_failure(
                logging.getLogger("forj_review_config_service"),
                exc,
                call_id="internal123",
                node="execute_tools",
            )
    assert private not in caplog.text
    assert "internal123" in caplog.text and "test_error_logs" in caplog.text
