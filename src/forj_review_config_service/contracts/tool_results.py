"""Small, factual references to durable results; full evidence remains available by ID."""

import json


def pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def result_catalog(value: dict) -> dict:
    """Index authoritative subtrees without duplicating the three page views."""
    catalog = {}
    editable = value.get("editable")
    if isinstance(editable, dict):
        maintenance = value.get("maintenance", {}).get("fields", {})
        default_properties = value.get("maintenance", {}).get("field_properties", [])
        catalog["fields"] = [
            {
                "key": field["key"],
                "label": field.get("label"),
                "pointer": f"/editable/fields/{index}",
                **{
                    key: item
                    for key, item in maintenance.get(field["key"], {}).items()
                    if key != "editable_properties" or item != default_properties
                },
            }
            for index, field in enumerate(editable.get("fields", []))
        ]
        catalog["materials"] = [
            {
                "document_type": material["document_type"],
                "label": material.get("label"),
                "scopes": material.get("scopes", []),
                "pointer": f"/editable/materials/{index}",
            }
            for index, material in enumerate(editable.get("materials", []))
        ]
        catalog["page_options"] = {
            key: "/editable/" + pointer_token(key)
            for key in editable
            if key not in {"fields", "materials"}
        }
        catalog["dependencies"] = "/dependencies"
    schema = value.get("schema")
    if isinstance(schema, dict):
        catalog["schema_definitions"] = {
            key: "/schema/$defs/" + pointer_token(key) for key in schema.get("$defs", {})
        }
        catalog["schema_properties"] = "/schema/properties"
    return catalog


def result_reference(call_id: str, content: str) -> str:
    try:
        value = json.loads(content)
    except ValueError:
        value = None
    summary = {}
    if isinstance(value, dict):
        for key in (
            "status",
            "ok",
            "reference",
            "kernel_version",
            "operations",
            "limits",
            "execution",
            "revision",
            "candidate_digest",
            "effective_digest",
            "waiting_reason",
            "next_action",
            "blockers",
        ):
            if key in value:
                summary[key] = value[key]
        if isinstance(value.get("maintenance"), dict):
            summary["maintenance"] = {
                key: value["maintenance"][key]
                for key in ("operation_inputs", "field_properties", "policy_date_properties")
                if key in value["maintenance"]
            }
        catalog = result_catalog(value)
        if catalog:
            summary["catalog"] = catalog
        if isinstance(value.get("rules"), dict):
            summary["rules_pointer"] = "/rules"
        # Never turn a large value into an unbounded summary.
        if len(json.dumps(summary, ensure_ascii=False)) > 12000:
            summary = {key: item for key, item in summary.items() if key != "catalog"}
            summary["message"] = (
                "目录较大；按JSON Pointer读取 /editable/fields、/editable/materials 或 /schema/$defs"
            )
            if len(json.dumps(summary, ensure_ascii=False)) > 12000:
                summary = {"message": "结果摘要较大，请按原始 ID 和 JSON Pointer 读取"}
    return json.dumps(
        {
            "status": "RESULT_STORED",
            "tool_result_id": call_id,
            "evidence_id": call_id,
            "summary": summary,
            "preview": "" if summary.get("catalog") else content[:1500],
            "read_with": "read_tool_result",
        },
        ensure_ascii=False,
    )
