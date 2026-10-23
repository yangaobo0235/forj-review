"""Execution reports describe real results and enforce the same editing boundary as manual use."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from forj_review_config_service.application.context import closing_tools
from forj_review_config_service.application.conversation import tool_message
from forj_review_config_service.application.progress import tool_activity, turn_summary
from forj_review_config_service.application.tool_execution import ToolExecutor
from forj_review_config_service.contracts.models import ConfigurationError, TaskRecord
from forj_review_config_service.integrations.review_kernel.snapshot import SnapshotKernel
from forj_review_config_service.tools.arguments import validate_arguments
from forj_review_config_service.tools.catalog import configuration_tools
from tests.support.pages import SCRAP_REPLACEMENT_CHANGCHUN, page_definition


def route_task():
    page = page_definition(SCRAP_REPLACEMENT_CHANGCHUN)
    # Generic SYSTEM-input maintenance coverage is independent of scrap's field list.
    page["fields"].append(
        {
            "key": "permit.contact_phone",
            "label": "合成联系电话",
            "aliases": ["合成联系电话"],
            "mode": "SYSTEM",
            "section": "new_vehicle",
            "page_section": "new_vehicle",
            "reviewable": True,
            "required": False,
            "writable": False,
        }
    )
    r = TaskRecord(task_id="task", baseline_digest="base", draft=page, turn_id="run")
    r.investigated_tools = ["get_configuration", "get_configuration_capabilities"]
    r.tool_results = {
        "caps": {"kernel_version": "current"},
        "config": {"reference": {"page_id": page["page_id"]}},
    }

    async def capabilities():
        return {"kernel_version": "current"}

    return r, ToolExecutor(
        None,
        SimpleNamespace(capabilities=capabilities, edit_fields=SnapshotKernel.edit_fields),
        None,
        24,
        None,
    )


@pytest.mark.asyncio
async def test_config_route_cannot_override_phone_maintenance_boundary_with_schema_support():
    record, executor = route_task()
    args = validate_arguments(
        "assess_route",
        {
            "route": "CONFIG",
            "reason": "通用Schema有evidence属性",
            "evidence_refs": ["caps", "config"],
            "operations": [
                {
                    "op": "upsert_field",
                    "value": {
                        "key": "permit.contact_phone",
                        "mode": "SINGLE_SOURCE",
                        "evidence": [{"material": "invoice", "field": "phone"}],
                    },
                }
            ],
        },
    )
    before = deepcopy(record.draft)
    with pytest.raises(ConfigurationError) as error:
        await executor._execute(record, "assess_route", args, "route")
    assert error.value.code == "FIELD_MAINTENANCE_ONLY"
    assert record.draft == before and record.revision == 0 and record.route == "UNDECIDED"


@pytest.mark.asyncio
async def test_phone_label_route_preflight_is_allowed_without_editing_or_publishing():
    record, executor = route_task()
    args = validate_arguments(
        "assess_route",
        {
            "route": "CONFIG",
            "reason": "名称可维护",
            "evidence_refs": ["caps", "config"],
            "operations": [
                {
                    "op": "upsert_field",
                    "value": {"key": "permit.contact_phone", "label": "客户联系电话"},
                }
            ],
        },
    )
    before = deepcopy(record.draft)
    result = await executor._execute(record, "assess_route", args, "route")
    assert result["route"] == "CONFIG"
    assert record.draft == before and record.revision == 0 and record.receipt is None


@pytest.mark.asyncio
async def test_config_route_requires_executable_operations():
    record, executor = route_task()
    with pytest.raises(ConfigurationError) as error:
        await executor._execute(
            record,
            "assess_route",
            validate_arguments(
                "assess_route",
                {"route": "CONFIG", "reason": "应该可以", "evidence_refs": ["caps", "config"]},
            ),
            "route",
        )
    assert error.value.code == "ROUTE_PLAN_REQUIRED"


@pytest.mark.asyncio
async def test_duplicate_reads_reuse_evidence_but_revision_changes_invalidate_cache():
    record, _ = route_task()
    events, reads = [], []

    class Store:
        async def get(self, task_id):
            return record

        async def put(self, value, event="progress", data=None):
            events.append((event, data))

    class Kernel:
        async def capabilities(self):
            reads.append(True)
            return {"kernel_version": "current"}

    executor = ToolExecutor(Store(), Kernel(), None, 24, None)
    for index in range(3):
        if index == 2:
            record.revision += 1
        record.messages.append(
            {
                "role": "assistant",
                "content": "",
                "_run_id": "run",
                "tool_calls": [
                    {
                        "id": str(index),
                        "function": {"name": "get_configuration_capabilities", "arguments": "{}"},
                    }
                ],
            }
        )
        await executor.execute_tools({"task_id": "task"})
    assert len(reads) == 2
    assert record.tool_results["1"]["_execution"]["reused_from"] == "0"
    assert all(
        data.get("activity", {}).get("detail") for name, data in events if name == "tool_completed"
    )


def test_budget_reserve_keeps_edit_and_clarification_tools_without_forcing_code_route():
    record, _ = route_task()
    record.model_calls = 20
    names = {tool["function"]["name"] for tool in closing_tools(record, configuration_tools(), 24)}
    assert {"edit_draft", "ask_user", "inspect_candidate", "assess_route"} <= names
    assert not names & {"get_configuration", "read_tool_result", "analyze_materials"}
    assert record.route == "UNDECIDED"


def test_observation_summary_names_each_sample_without_leaking_its_values_or_claiming_approval():
    summary = tool_activity(
        "analyze_materials",
        {"sample_ids": ["sample_1"]},
        {
            "observations": [
                {
                    "sample_id": "sample_1",
                    "material_category": "机动车登记证书",
                    "value": "private-phone",
                }
            ]
        },
    )
    assert "sample_1：机动车登记证书" in summary["detail"]
    assert "不能据此直接认定审核通过" in summary["detail"]
    assert "private-phone" not in json.dumps(summary)
    record, _ = route_task()
    record.error = "模型预算耗尽"
    assert "尚未生成配置修改" in turn_summary(record)["detail"]


def test_evidence_id_is_explicit_and_different_from_original_paging_source():
    record, _ = route_task()
    tool_message(
        record, "current-call", {"tool_result_id": "original-source", "value": {"label": "名称"}}
    )
    content = json.loads(record.messages[-1]["content"])
    assert content["evidence_id"] == "current-call"
    assert content["tool_result_id"] == "original-source"
