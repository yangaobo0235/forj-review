"""Investigations select exact evidence subtrees instead of scanning whole pages."""

import json

import pytest

from forj_review_config_service.application.tool_execution import ToolExecutor
from forj_review_config_service.contracts.models import ConfigurationError, TaskRecord
from forj_review_config_service.tools.arguments import validate_arguments


async def read_result(value, **arguments):
    record = TaskRecord(task_id="task", baseline_digest="base")
    record.tool_results["source"] = value
    executor = ToolExecutor(None, None, None, 24, None)
    return await executor._execute(
        record,
        "read_tool_result",
        validate_arguments("read_tool_result", {"tool_result_id": "source", **arguments}),
        "read",
    )


@pytest.mark.asyncio
async def test_targeted_material_lookup_returns_complete_semantics_without_other_materials():
    material = {"document_type": "receipt", "guidance": "只读取购买方电话，不读取销售方电话"}
    result = await read_result(
        {"editable": {"materials": [material, {"private": "other"}]}},
        pointer="/editable/materials/0",
    )
    assert result["value"] == material
    assert result["tool_result_id"] == "source" and result["next_offset"] is None
    assert "private" not in json.dumps(result)


@pytest.mark.asyncio
async def test_json_pointer_escapes_and_empty_key_are_resolved_exactly():
    result = await read_result({"a/b": {"~key": {"": [True, None]}}}, pointer="/a~1b/~0key//1")
    assert result["value"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("pointer", ["/missing", "/array/-1", "/array/01", "/array/3", "/leaf/key"])
async def test_missing_or_invalid_subtree_is_explicit(pointer):
    with pytest.raises(ConfigurationError) as exc:
        await read_result({"array": [1], "leaf": "text"}, pointer=pointer)
    assert exc.value.code == "RESULT_PATH_NOT_FOUND"


@pytest.mark.asyncio
async def test_large_subtree_pages_keep_original_source_and_pointer():
    result = await read_result({"large": {"text": "x" * 100}}, pointer="/large", limit=30)
    following = await read_result(
        {"large": {"text": "x" * 100}}, pointer="/large", limit=1000, offset=result["next_offset"]
    )
    assert json.loads(result["content"] + following["content"]) == {"text": "x" * 100}
    assert following["tool_result_id"] == "source" and following["pointer"] == "/large"
    assert following["next_offset"] is None


@pytest.mark.asyncio
async def test_legacy_whole_result_lookup_still_works():
    result = await read_result({"status": "ok"})
    assert result["value"] == {"status": "ok"}


@pytest.mark.asyncio
async def test_configuration_query_returns_target_source_semantics_and_real_restrictions():
    context = {
        "reference": {"page_id": "page", "version": "active"},
        "editable": {
            "fields": [
                {"key": "contact", "mode": "SYSTEM", "evidence": []},
                {"key": "other", "evidence": [{"material": "other"}]},
            ],
            "materials": [
                {"document_type": "receipt", "guidance": "明确购买方栏位"},
                {"document_type": "other", "guidance": "unrelated"},
            ],
        },
        "maintenance": {"fields": {"contact": {"editable_properties": ["label"]}, "other": {}}},
        "dependencies": {"contact": ["开发接入字段"], "other": []},
        "effective": {"duplicate": "x" * 100000},
    }

    class Kernel:
        async def context(self, page_id):
            assert page_id == "page"
            return context

    executor = ToolExecutor(None, Kernel(), None, 24, None)
    args = validate_arguments(
        "get_configuration",
        {"page_id": "page", "field_keys": ["contact"], "material_types": ["receipt"]},
    )
    result = await executor._execute(None, "get_configuration", args, "query")
    assert [f["key"] for f in result["editable"]["fields"]] == ["contact"]
    assert result["editable"]["materials"][0]["guidance"] == "明确购买方栏位"
    assert result["dependencies"] == {"contact": ["开发接入字段"]}
    assert result["maintenance"]["fields"] == {"contact": {"editable_properties": ["label"]}}
    assert "effective" not in result
    args["field_keys"] = ["missing"]
    with pytest.raises(ConfigurationError) as exc:
        await executor._execute(None, "get_configuration", args, "query")
    assert exc.value.code == "CONFIGURATION_TARGET_NOT_FOUND"
    assert len(context["editable"]["fields"]) == 2
