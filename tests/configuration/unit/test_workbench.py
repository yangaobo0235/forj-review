"""User-facing task semantics: references, revisions, recovery and actual adoption."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from forj_review_config_service.application.adoption import prepare_adoption, verify_adoption
from forj_review_config_service.application.conversation import chat_messages
from forj_review_config_service.application.handoff import export_handoff
from forj_review_config_service.application.local_reports import import_local_report
from forj_review_config_service.application.rebase import merge_candidate
from forj_review_config_service.application.task_view import (
    candidate_export,
    task_detail,
    task_view,
)
from forj_review_config_service.application.tasks import manage_requirements, receive_message
from forj_review_config_service.contracts.models import (
    AdoptionCommand,
    ConfigurationError,
    PageDiscoverySnapshot,
    TaskRecord,
)
from forj_review_config_service.contracts.tasks import intent_digest
from forj_review_service.runtime.configuration.verification import proof_is_current
from tests.support.configuration import SNAPSHOT


def record():
    return TaskRecord(task_id="task", baseline_digest="base", draft={"page_id": "page"})


def test_task_detail_projects_business_messages_and_requirements_without_mutating_history():
    r = record()
    r.messages = [
        {"role": "user", "content": "审核注册日期\nrequirement_id=req1"},
        {"role": "assistant", "content": "请确认日期含义"},
        {"role": "tool", "content": "private tool result"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "系统强制验证结果：{}"},
    ]
    original = r.model_dump(mode="json")

    class Kernel:
        def page_options(self, draft):
            return {"required_materials": ["vehicle_license"]}

        def field_dependencies(self, draft):
            return {"vin": ["model"]}

        def comparison_configurable_fields(self, draft):
            return ["vin"]

    detail = task_detail(r, Kernel(), "local")
    assert (
        detail["chat_messages"]
        == chat_messages(r)
        == [
            {"role": "user", "content": "审核注册日期"},
            {"role": "assistant", "content": "请确认日期含义"},
        ]
    )
    assert detail["task"]["coverage"] == [] and not detail["task"]["can_publish"]
    assert detail["environment"] == "local"
    assert detail["page_options"] == {"required_materials": ["vehicle_license"]}
    assert detail["field_dependencies"] == {"vin": ["model"]}
    assert detail["comparison_configurable_fields"] == ["vin"]
    assert detail["execution_summary"] is None
    assert r.model_dump(mode="json") == original


def test_candidate_export_preserves_capture_revisions_and_latest_trial_evidence():
    r = record()
    r.snapshot = PageDiscoverySnapshot.model_validate(SNAPSHOT)
    r.capture_id = "capture"
    r.revision, r.intent_revision, r.evidence_revision = 3, 2, 1
    r.reference_fields = ["vin"]
    r.tool_results = {
        "first": {"kind": "collection_trial", "revision": 2},
        "latest": {"kind": "collection_trial", "revision": 3},
        "other": {"kind": "capabilities"},
    }
    result = candidate_export(r)
    evidence = result["evidence"]
    assert result["candidate"] == r.draft
    assert evidence["snapshot"] == r.snapshot.model_dump(mode="json")
    assert evidence["capture_id"] == "capture"
    assert (evidence["revision"], evidence["intent_revision"], evidence["evidence_revision"]) == (
        3,
        2,
        1,
    )
    assert evidence["reference_fields"] == ["vin"]
    assert evidence["proof"] == {"kind": "collection_trial", "revision": 3}
    assert candidate_export(record())["evidence"]["proof"] is None


def test_queries_preserve_validation_but_pending_messages_disable_publication():
    r = record()
    goal = receive_message(r, "改名称")
    manage_requirements(r, {"message_id": goal["message_id"], "action": "ACCEPT"})
    r.report = {"ok": True, "intent_digest": intent_digest(r)}
    r.state = "READY_FOR_CONFIRMATION"
    assert task_view(r)["can_publish"]
    query = receive_message(r, "进展如何")
    assert not task_view(r)["can_publish"]
    manage_requirements(r, {"message_id": query["message_id"], "action": "QUERY"})
    assert r.report["ok"] and task_view(r)["can_publish"]
    r.turn_status = "RUNNING"
    assert not task_view(r)["can_publish"]


def test_continuous_pending_messages_are_all_interpreted():
    r = record()
    first, second = receive_message(r, "改名称"), receive_message(r, "不要改规则")
    for item in (first, second):
        manage_requirements(r, {"message_id": item["message_id"], "action": "ACCEPT"})
    assert all(item["status"] == "ACTIVE" for item in r.requirements)


def test_three_way_merge_uses_field_keys_and_requires_explicit_conflict_choices():
    base = {"version": "1", "fields": [{"key": "a", "label": "甲"}, {"key": "b", "label": "乙"}]}
    ours = {"version": "draft", "fields": [{"key": "b", "label": "乙改"}]}
    current = {
        "version": "2",
        "fields": [{"key": "a", "label": "甲改"}, {"key": "b", "label": "乙"}],
    }
    original = deepcopy((base, ours, current))
    merged, conflicts = merge_candidate(base, ours, current)
    assert [c["path"] for c in conflicts] == ["/fields/a"]
    assert merged["fields"] == [{"key": "a", "label": "甲改"}, {"key": "b", "label": "乙改"}]
    merged, _ = merge_candidate(base, ours, current, {"/fields/a": "candidate"})
    assert merged["fields"] == [{"key": "b", "label": "乙改"}]
    assert merged["version"] == "2" and original == (base, ours, current)


def local_record():
    r = record()
    r.route = "LOCAL_CODE"
    r.route_assessment = dict(
        route="LOCAL_CODE",
        reason="缺算法",
        evidence_refs=["caps"],
        intent_revision=0,
        revision=0,
        baseline_digest="base",
        capability_digest="caps",
    )
    return r


def test_local_receipt_must_match_package_and_never_marks_deployed():
    r = local_record()
    package = export_handoff(r)
    report = {
        "status": "LOCAL_CHECKS_PASSED",
        "task": {
            "digest": package.digest,
            "task_id": "task",
            "intent_digest": package.intent_digest,
        },
        "changed_files": ["src/normalizer.py"],
        "delivery": {"summary": "补丁待评审"},
    }
    import_local_report(r, report)
    assert not task_view(r)["complete"] and not task_view(r)["can_publish"]
    assert "待开发人员评审" in task_view(r)["label"]
    report["task"]["digest"] = "stale"
    with pytest.raises(ConfigurationError, match="不一致"):
        import_local_report(r, report)


def test_human_handoff_requires_actual_distinct_local_investigation():
    r = local_record()
    package = export_handoff(r)
    report = {
        "status": "HUMAN_HANDOFF",
        "task": {
            "digest": package.digest,
            "task_id": "task",
            "intent_digest": package.intent_digest,
        },
        "delivery": {
            "status": "HUMAN_HANDOFF",
            "obstacles": ["外部契约缺少可靠的成功判据"],
            "evidence_refs": ["one", "two"],
        },
        "evidence": [],
    }
    with pytest.raises(ConfigurationError, match="实际调查"):
        import_local_report(r, report)
    report["evidence"] = [
        {"id": key, "tool": "read_file", "result": {"path": key, "content": key}}
        for key in ("one", "two")
    ]
    import_local_report(r, report)
    assert r.route == "HUMAN_HANDOFF" and not task_view(r)["complete"]


class AdoptionKernel:
    proof_is_current = staticmethod(proof_is_current)
    state = "EFFECTIVE"

    async def activation(self, record):
        return {"state": self.state}

    async def context(self, page_id):
        return {"reference": {"version": "2"}, "manifest": {"fields": [{"key": "vin"}]}}


def adoption_record():
    r = record()
    r.snapshot = PageDiscoverySnapshot.model_validate(SNAPSHOT)
    r.receipt = {"page_id": "page", "version": "2", "effective_digest": "digest"}
    r.activation = {"state": "EFFECTIVE"}
    prepare_adoption(r)
    return r


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"version": "old"},
        {"page_instance_id": "another"},
        {"effective_digest": "wrong"},
        {"fields": {"vin": 0}},
        {"diagnostics": ["歧义"]},
    ],
)
async def test_adoption_rejects_wrong_identity_version_and_collection(change):
    r = adoption_record()
    t = r.adoption_task
    cmd = AdoptionCommand(
        client_request_id="use",
        expected_revision=0,
        task_id=t["id"],
        **{
            **{
                k: t[k]
                for k in (
                    "snapshot_id",
                    "page_instance_id",
                    "page_url",
                    "page_id",
                    "version",
                    "effective_digest",
                )
            },
            "fields": {"vin": 1},
            **change,
        },
    )
    with pytest.raises(ConfigurationError):
        await verify_adoption(r, cmd, AdoptionKernel())
    assert not task_view(r)["complete"]


@pytest.mark.asyncio
async def test_completion_needs_receipt_loaded_release_and_browser_use_and_rejects_expiry():
    r = adoption_record()
    t = r.adoption_task
    cmd = AdoptionCommand(
        client_request_id="use",
        expected_revision=0,
        task_id=t["id"],
        **{
            k: t[k]
            for k in (
                "snapshot_id",
                "page_instance_id",
                "page_url",
                "page_id",
                "version",
                "effective_digest",
            )
        },
        fields={"vin": 1},
    )
    assert not task_view(r)["complete"]
    t["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with pytest.raises(ConfigurationError, match="过期"):
        await verify_adoption(r, cmd, AdoptionKernel())
    t["expires_at"] = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    kernel = AdoptionKernel()
    kernel.state = "LOADING"
    with pytest.raises(ConfigurationError, match="未加载"):
        await verify_adoption(r, cmd, kernel)
    kernel.state = "EFFECTIVE"
    await verify_adoption(r, cmd, kernel)
    assert task_view(r)["complete"] and not task_view(r)["can_publish"]


def test_reference_contract_uses_explicit_reference_names():
    from pydantic import ValidationError

    record = TaskRecord(
        task_id="current",
        baseline_digest="base",
        reference_fields=["vin"],
        reference_samples=["image"],
    )
    assert record.reference_fields == ["vin"] and record.reference_samples == ["image"]
    with pytest.raises(ValidationError):
        TaskRecord(task_id="obsolete", baseline_digest="base", selected_fields=["vin"])
