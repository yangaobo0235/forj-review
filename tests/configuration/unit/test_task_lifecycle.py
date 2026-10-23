"""Requirement correction, context limits and archival retain the actual task."""

import json

import pytest

from forj_review_config_service.application.context import model_messages, protocol_groups
from forj_review_config_service.application.lifecycle import archive, restore
from forj_review_config_service.application.tasks import manage_requirements, receive_message
from forj_review_config_service.contracts.models import (
    CapabilityGap,
    ConfigurationError,
    TaskRecord,
)
from forj_review_config_service.contracts.tasks import active_requirements, intent_digest


def record():
    return TaskRecord(task_id="task", baseline_digest="baseline", draft={"page_id": "page"})


def test_replacing_and_withdrawing_only_explicit_targets_invalidates_approval():
    r = record()
    message = receive_message(r, "改成开票日期")
    first = manage_requirements(r, dict(message_id=message["message_id"], action="ACCEPT"))[
        "created"
    ][0]
    original_digest = intent_digest(r)
    r.report, r.pending = {"ok": True}, {"id": "confirmation", "kind": "confirmation"}
    second = receive_message(r, "更正，是注册日期")
    manage_requirements(
        r,
        dict(
            message_id=second["message_id"],
            action="REPLACE",
            requirement_ids=[first["requirement_id"]],
        ),
    )
    second = active_requirements(r)[0]
    assert second["text"] == "更正，是注册日期"
    assert first["status"] == "SUPERSEDED" and intent_digest(r) != original_digest
    assert r.report is None and r.pending is None
    third = receive_message(r, "撤回刚才这个修改")
    manage_requirements(
        r,
        dict(
            message_id=third["message_id"],
            action="WITHDRAW",
            requirement_ids=[second["requirement_id"]],
        ),
    )
    assert active_requirements(r) == []


def test_status_query_does_not_become_permanent_acceptance_target():
    r = record()
    question = receive_message(r, "目前进展如何")
    manage_requirements(r, dict(message_id=question["message_id"], action="QUERY"))
    assert active_requirements(r) == []
    with pytest.raises(ConfigurationError, match="尚未处理"):
        manage_requirements(r, dict(message_id=question["message_id"], action="ACCEPT"))


def test_new_intent_supersedes_old_requirement_judgments_but_preserves_scene_mapping():
    r = record()
    message = receive_message(r, "使用新比较算法")
    first = manage_requirements(r, dict(message_id=message["message_id"], action="ACCEPT"))[
        "created"
    ][0]
    r.decisions = [
        {"requirement_id": first["requirement_id"], "status": "UNSUPPORTED", "revision": 0},
        {"candidate_id": "field", "status": "RESOLVED", "revision": 0},
    ]
    r.route, r.waiting_reason = "LOCAL_CODE", "LOCAL_EXECUTION"
    r.capability_gaps = [
        CapabilityGap(
            requirement_id=first["requirement_id"],
            missing_capability="新算法",
            evidence_refs=["deployed-capabilities"],
        )
    ]
    latest = receive_message(r, "不做新算法了，只改标签")
    manage_requirements(
        r,
        dict(
            message_id=latest["message_id"],
            action="REPLACE",
            requirement_ids=[first["requirement_id"]],
        ),
    )
    assert r.decisions[0]["status"] == "SUPERSEDED"
    assert r.decisions[1]["status"] == "RESOLVED"
    assert r.route == "UNDECIDED" and r.waiting_reason is None
    assert not r.capability_gaps


def test_bounded_context_keeps_current_user_and_complete_native_tool_groups():
    r = record()
    r.messages = [
        {"role": "user", "content": "旧消息" * 10000},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "a", "function": {"name": "read_draft", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "a", "content": "x" * 20000},
        {"role": "user", "content": "当前更正需求"},
    ]
    context = model_messages(r)
    assert context[-1]["content"] == "当前更正需求"
    assert len(json.dumps(context, ensure_ascii=False)) < 72000
    assert protocol_groups(r.messages)[1][-1]["tool_call_id"] == "a"


def test_oversized_effective_requirements_stop_visibly_instead_of_silently_omitting_them():
    r = record()
    r.requirements = [{"requirement_id": str(i), "text": "x" * 800} for i in range(100)]
    with pytest.raises(ConfigurationError, match="上下文"):
        model_messages(r)


def test_older_requirement_keeps_conditions_beyond_summary_preview():
    r = record()
    r.requirements = [{"requirement_id": "long", "text": "背景" * 500 + "禁止改写登记日期"}]
    assert "禁止改写登记日期" in model_messages(r)[0]["content"]


def test_paged_tool_content_remains_readable_instead_of_recursive_result_references():
    r = record()
    page = json.dumps({"content": "x" * 10000, "next_offset": 12000})
    r.messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "page-read", "function": {"name": "read_tool_result", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "page-read", "content": page},
    ]
    assert model_messages(r)[-1]["content"] == page


def test_large_capability_result_keeps_operations_and_identity_outside_schema_preview():
    from forj_review_config_service.contracts.tool_results import result_reference

    value = {
        "schema": "x" * 100000,
        "kernel_version": "actual-kernel",
        "operations": {"field": ["upsert_field"]},
        "limits": {"code_execution": False},
    }
    reference = json.loads(result_reference("capabilities", json.dumps(value)))
    assert reference["summary"]["kernel_version"] == "actual-kernel"
    assert reference["summary"]["operations"] == value["operations"]
    assert reference["tool_result_id"] == "capabilities"


def test_configuration_catalog_locates_fields_materials_and_actual_edit_limits():
    from forj_review_config_service.contracts.tool_results import result_reference

    value = {
        "reference": {"page_id": "page", "version": "active"},
        "editable": {
            "fields": [{"key": "input.contact", "label": "联系电话"}],
            "materials": [{"document_type": "receipt", "label": "收据", "scopes": ["buyer"]}],
            "settings": {"large": "x" * 20000},
        },
        "maintenance": {
            "fields": {
                "input.contact": {
                    "dependencies": ["开发接入字段"],
                    "editable_properties": ["label"],
                }
            }
        },
    }
    reference = json.loads(result_reference("config", json.dumps(value)))
    catalog = reference["summary"]["catalog"]
    assert catalog["fields"][0]["pointer"] == "/editable/fields/0"
    assert catalog["fields"][0]["editable_properties"] == ["label"]
    assert catalog["materials"][0]["pointer"] == "/editable/materials/0"
    assert reference["preview"] == ""  # No second copy of the beginning of the page.


def test_old_paging_is_compacted_to_original_evidence_and_recent_page_stays_readable():
    r = record()
    for index in range(6):
        call_id = f"read-{index}"
        r.messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": call_id, "function": {"name": "read_tool_result", "arguments": "{}"}}
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        {
                            "tool_result_id": "original",
                            "pointer": "/schema",
                            "content": "x" * 10000,
                            "next_offset": (index + 1) * 10000,
                        }
                    ),
                },
            ]
        )
    context = model_messages(r)
    old = json.loads(context[2]["content"])
    assert old["tool_result_id"] == "original" and old["pointer"] == "/schema"
    assert old["status"] == "RESULT_PREVIOUSLY_READ"
    assert json.loads(context[-1]["content"])["content"] == "x" * 10000
    assert len(context) == 13  # Original protocol groups fit, no orphan tool messages.


def test_saved_pre_catalog_results_rebuild_from_durable_evidence_without_mutating_history():
    from copy import deepcopy

    r = record()
    value = {
        "reference": {"page_id": "page", "version": "1"},
        "editable": {"fields": [{"key": "contact", "label": "电话"}], "notes": "x" * 10000},
    }
    for call_id in ("old", "new"):
        r.messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "function": {"name": "get_configuration", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call_id, "content": '{"status":"RESULT_STORED"}'},
            ]
        )
        r.tool_results[call_id] = deepcopy(value)
    before = deepcopy(r.messages)
    context = model_messages(r)
    assert json.loads(context[2]["content"])["same_evidence_as"] == "new"
    assert json.loads(context[-1]["content"])["summary"]["catalog"]["fields"][0]["key"] == "contact"
    assert r.messages == before


def test_model_sees_real_remaining_budget_and_investigation_without_decisions():
    r = record()
    r.model_calls = 7
    r.messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": str(index), "function": {"name": "read_draft", "arguments": "{}"}}
                for index in range(6)
            ],
        }
    ]
    context = model_messages(r, max_calls=10)[0]["content"]
    task = json.loads(context.split("当前任务：", 1)[1])
    assert task["execution_budget"] == {
        "model_calls_used": 7,
        "model_calls_remaining": 3,
        "model_calls_limit": 10,
        "investigation_calls_since_progress": 6,
    }
    assert "不能重复浏览已有定义代替决策" in context
    r.messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "edit", "function": {"name": "edit_draft", "arguments": "{}"}}],
        }
    )
    task = json.loads(model_messages(r)[0]["content"].split("当前任务：", 1)[1])
    assert task["execution_budget"]["investigation_calls_since_progress"] == 0


def test_new_round_retains_user_constraints_and_evidence_ids_without_replaying_old_read_loop():
    r = record()
    r.turn_id = "recovery"
    r.messages = [
        {"role": "user", "content": "只改名称，保留草稿", "_run_id": "old"},
        {
            "role": "assistant",
            "content": "",
            "_run_id": "old",
            "tool_calls": [
                {"id": "source", "function": {"name": "get_configuration", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "source", "_run_id": "old", "content": "{}"},
        {
            "role": "assistant",
            "content": "",
            "_run_id": "old",
            "tool_calls": [
                {"id": "scan", "function": {"name": "read_tool_result", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "scan", "_run_id": "old", "content": "{}"},
    ]
    r.tool_results["source"] = {"reference": {"page_id": "page"}}
    context = model_messages(r)
    assert [m["role"] for m in context] == ["system", "user"]
    assert context[1]["content"] == "只改名称，保留草稿"
    assert '"tool_result_id": "source"' in context[0]["content"]
    assert len(r.messages) == 5
    r.messages[1]["content"] = "已确认只保留草稿，不发布"
    context = model_messages(r)
    assert context[-1] == {"role": "assistant", "content": "已确认只保留草稿，不发布"}
    assert not any(message.get("tool_calls") for message in context)


def test_question_protocol_spanning_rounds_stays_paired():
    r = record()
    r.turn_id = "recovery"
    r.messages = [
        {
            "role": "assistant",
            "content": "",
            "_run_id": "old",
            "tool_calls": [{"id": "question", "function": {"name": "ask_user", "arguments": "{}"}}],
        },
        {
            "role": "tool",
            "tool_call_id": "question",
            "_run_id": "recovery",
            "content": '{"answer":"注册日期"}',
        },
    ]
    context = model_messages(r)
    assert context[1]["tool_calls"][0]["id"] == context[2]["tool_call_id"] == "question"


def test_archival_preserves_draft_goals_and_evidence_reference():
    r = record()
    message = receive_message(r, "保留这个修改")
    r.draft["version"] = "draft"
    r.capture_id = "old-capture"
    archive(r)
    assert r.state == "CLOSED" and r.snapshot is None and r.capture_id == "old-capture"
    assert r.draft["version"] == "draft" and r.revision == 0
    assert r.pending_messages == [message["message_id"]]
    restore(r)
    assert r.state == "DRAFTING" and r.waiting_reason == "CAPTURE"
