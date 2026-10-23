"""Verify an actual browser collection against the deployed release, after publication."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from forj_review_config_service.contracts.models import ConfigurationError


def prepare_adoption(record):
    if not record.receipt or (record.activation or {}).get("state") != "EFFECTIVE":
        return
    if record.snapshot is None:
        return
    record.adoption_task = {
        "id": uuid4().hex,
        "kind": "adoption",
        "capture_id": record.capture_id,
        "snapshot_id": record.snapshot.snapshot_id,
        "page_instance_id": record.snapshot.page_instance_id,
        "page_url": record.snapshot.page_url,
        "page_id": record.receipt["page_id"],
        "version": record.receipt["version"],
        "effective_digest": record.receipt["effective_digest"],
        "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
    }


async def verify_adoption(record, command, kernel):
    task = record.adoption_task or {}
    if not task or task["id"] != command.task_id or not kernel.proof_is_current(task):
        raise ConfigurationError("STALE_ADOPTION", "采用核对已过期，请重新核对服务加载")
    for key in (
        "snapshot_id",
        "page_instance_id",
        "page_url",
        "page_id",
        "version",
        "effective_digest",
    ):
        if getattr(command, key) != task[key]:
            raise ConfigurationError("ADOPTION_MISMATCH", "页面或采用版本不一致，请重新采集并关联")
    activation = await kernel.activation(record)
    record.activation = {**activation, "checked_at": datetime.now(UTC).isoformat()}
    if activation["state"] != "EFFECTIVE":
        raise ConfigurationError("RELEASE_NOT_LOADED", "审核服务当前未加载本次发布，请先核对加载")
    context = await kernel.context(task["page_id"])
    if context["reference"]["version"] != task["version"]:
        raise ConfigurationError("RELEASE_SUPERSEDED", "活动页面配置已变化，请重新核对目标发布")
    expected = {f["key"] for f in context["manifest"]["fields"]}
    if command.diagnostics or any(command.fields.get(key) != 1 for key in expected):
        raise ConfigurationError(
            "ADOPTION_COLLECTION_FAILED", "采用核对发现字段缺失、歧义或页面诊断，请查看业务页面"
        )
    record.adoption = {
        "page_id": command.page_id,
        "version": command.version,
        "effective_digest": command.effective_digest,
        "page_instance_id": command.page_instance_id,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    record.adoption_task = None
