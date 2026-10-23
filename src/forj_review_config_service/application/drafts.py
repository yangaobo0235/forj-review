"""Deterministic, bounded authoring operations; no arbitrary JSON paths."""

from forj_review_config_service.contracts.models import (
    ConfigurationError,
    DraftOperation,
    TaskRecord,
)


def apply_operations(record: TaskRecord, operations: list[DraftOperation], kernel):
    if record.activation_only:
        raise ConfigurationError("ROLLBACK_IMMUTABLE", "回退会话只能切换历史版本，不能修改历史内容")
    candidate = kernel.edit_fields(
        record.draft, [operation.model_dump() for operation in operations]
    )
    if candidate == record.draft:
        return
    record.draft = candidate
    record.revision += 1
    if record.route == "UNDECIDED":
        record.route = "CONFIG"
    record.report = None
    record.state = "DRAFTING"
    record.pending = None
