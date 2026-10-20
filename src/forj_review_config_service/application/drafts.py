"""Deterministic, bounded authoring operations; no arbitrary JSON paths."""

from copy import deepcopy

from forj_review_config_service.contracts.models import (
    ConfigurationError,
    DraftOperation,
    SessionRecord,
)

METADATA = {"page_id", "business_type", "region", "version", "title", "paths", "anchors", "review_tab", "extends",
            "scopes", "sections", "page_groups"}
WRITE_POLICY = {"writable_control_kinds", "record_identity_fields", "identity_anchors", "page_actions"}


def apply_operations(record: SessionRecord, operations: list[DraftOperation], inherited: dict | None = None):
    if record.activation_only:
        raise ConfigurationError("ROLLBACK_IMMUTABLE", "回退会话只能切换历史版本，不能修改历史内容")
    draft = deepcopy(record.draft)
    for operation in operations:
        op, value = operation.op, deepcopy(operation.value)
        if op == "create_page":
            if draft:
                raise ConfigurationError("DRAFT_EXISTS", "已有草稿，请使用类型化修改")
            draft = value
        elif op in {"set_page_metadata", "set_write_policy", "set_rule_bindings"}:
            allowed = METADATA if op == "set_page_metadata" else WRITE_POLICY if op == "set_write_policy" else {"enable_rules", "disable_rules"}
            if value.keys() - allowed:
                raise ConfigurationError("INVALID_OPERATION", "操作包含未允许的配置属性", 422)
            draft.update(value)
        elif op.startswith("upsert_") or op in {"set_collection_binding", "set_material_requirements"}:
            material = op in {"upsert_material", "set_material_requirements"}
            collection, identity = ("materials", "document_type") if material else ("fields", "key")
            if not isinstance(value.get(identity), str):
                raise ConfigurationError("INVALID_OPERATION", f"操作缺少 {identity}", 422)
            entries = list(draft.get(collection, []))
            existing = next((entry for entry in entries if entry[identity] == value[identity]), None)
            if existing is None:
                entries.append(value)
            else:
                existing.update(value)
            draft[collection] = entries
            removed = "remove_materials" if material else "remove_fields"
            draft[removed] = [key for key in draft.get(removed, []) if key != value[identity]]
        elif op in {"remove_field", "remove_material"}:
            material = op == "remove_material"
            collection, identity = ("materials", "document_type") if material else ("fields", "key")
            key = value.get(identity)
            if not isinstance(key, str):
                raise ConfigurationError("INVALID_OPERATION", f"操作缺少 {identity}", 422)
            draft[collection] = [item for item in draft.get(collection, []) if item[identity] != key]
            removed = "remove_materials" if material else "remove_fields"
            if key in (inherited or {}).get(collection, []):
                draft[removed] = sorted(set(draft.get(removed, [])) | {key})
        else:
            raise ConfigurationError("INVALID_OPERATION", "未知操作", 422)
    record.draft = draft
    record.revision += 1
    record.report = None
    record.state = "DRAFTING"
    if record.pending:
        record.pending = None
