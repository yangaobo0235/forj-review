"""Candidate validation and browser evidence bound to the current working context."""

from datetime import UTC, datetime
from uuid import uuid4

from forj_review_config_service.application.conversation import fingerprint
from forj_review_config_service.application.requirement_checks import requirement_checks
from forj_review_config_service.contracts.tasks import active_requirements, intent_digest


def trial_binding(record) -> dict:
    return {
        "revision": record.revision,
        "evidence_revision": record.evidence_revision,
        "snapshot_digest": fingerprint(record.snapshot.model_dump(mode="json"))
        if record.snapshot
        else None,
        "capture_id": record.capture_id,
        "reference_fields": sorted(record.reference_fields),
        "reference_samples": sorted(record.reference_samples),
    }


def evaluate_trial(record, manifest: dict, facts: dict, kernel) -> dict:
    materials = {m["document_type"] for m in manifest["materials"]}
    mapping = {
        d.get("sample_id"): d.get("material")
        for d in record.decisions
        if d["status"] == "RESOLVED"
        and d["revision"] == record.revision
        and d.get("sample_id")
        and d.get("evidence_revision", record.evidence_revision) == record.evidence_revision
        and d.get("material") in materials
    }
    invalid = [
        d["sample_id"]
        for d in record.decisions
        if d["status"] == "RESOLVED"
        and d["revision"] == record.revision
        and d.get("sample_id")
        and d.get("material") not in materials
    ]
    result = kernel.evaluate_collection(
        manifest, facts, record.draft.get("page_id"), sorted(mapping), mapping
    )
    return {**result, "ok": result["ok"] and not invalid, "invalid_material_bindings": invalid}


def current_trial(record, preview: dict, result: dict, kernel) -> bool:
    if (
        result.get("kind") != "collection_trial"
        or not kernel.proof_is_current(result)
        or result.get("binding") != trial_binding(record)
        or result.get("candidate_digest") != preview["candidate_digest"]
    ):
        return False
    facts = result.get("facts")
    return (
        isinstance(facts, dict) and evaluate_trial(record, preview["manifest"], facts, kernel)["ok"]
    )


async def validate_candidate(kernel, record):
    started = datetime.now(UTC)
    preview = await kernel.validate(record)
    source = (await kernel.context(record.draft["page_id"]))["source"]
    changes_required = any(
        source.get(key) != record.draft.get(key)
        for key in (source.keys() | record.draft.keys()) - {"version", "active"}
    )
    blockers = []
    if record.route in {"LOCAL_CODE", "HUMAN_HANDOFF"}:
        blockers.append("当前需求需本地代码或人工接续，不能作为配置发布")
    if record.snapshot is None:
        record.waiting_reason = "CAPTURE"
        blockers.append("缺少页面现场，请从扩展采集并关联后继续")
    resolved = [
        d
        for d in record.decisions
        if d["status"] == "RESOLVED"
        and d["revision"] == record.revision
        and d.get("evidence_revision", record.evidence_revision) == record.evidence_revision
    ]
    # Investigation references do not define material policy or acceptance coverage.
    coverage = requirement_checks(record, source)
    requirements_ok = all(item["ok"] for item in coverage) and not record.pending_messages
    if record.pending_messages:
        blockers.append("最新用户消息尚未解释，不能发布旧候选")
    for item in coverage:
        if not item["ok"]:
            blockers.append(
                "目标尚未被实际检查覆盖：" + item["requirement_id"] + "；" + item["reason"]
            )
    ambiguity = any(
        d["status"] not in {"RESOLVED", "SUPERSEDED"} and d["revision"] == record.revision
        for d in record.decisions
    )
    if ambiguity:
        blockers.append("存在未解决的业务歧义或能力缺口")
    results = list(record.tool_results.values())
    collection_ok = any(
        isinstance(r, dict) and current_trial(record, preview, r, kernel) for r in results
    )
    if not collection_ok:
        blockers.append("缺少当前候选的浏览器试采集证明")
    replays = [
        r
        for r in results
        if isinstance(r, dict)
        and r.get("case_set_id")
        and r.get("revision") == record.revision
        and r.get("ok")
        and r.get("candidate_digest") == preview["candidate_digest"]
        and (await kernel.verify_replay_receipt(r))
    ]
    if not replays:
        blockers.append("缺少当前候选的独立样例回放")
    return {
        **preview,
        "report_id": uuid4().hex,
        "revision": record.revision,
        "intent_revision": record.intent_revision,
        "evidence_revision": record.evidence_revision,
        "requirement_checks": coverage,
        "changes_required": changes_required or record.activation_only,
        "intent_digest": intent_digest(record),
        "started_at": started.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "verification_context": {
            "schema_version": "2.0",
            "requirements": active_requirements(record),
            "capability_digest": fingerprint(await kernel.capabilities()),
            "case_sets": [
                {"id": r["case_set_id"], "digest": r.get("case_digest")} for r in replays
            ],
            "coverage": [
                "compile",
                "dependencies",
                "collection",
                "independent_replay",
                "requirements",
            ],
            "baseline_digest": record.baseline_digest,
            "evidence_revision": record.evidence_revision,
            "requirement_checks": coverage,
        },
        "ok": not blockers,
        "blockers": blockers,
        "replays": replays,
        "decision_summary": [d["reason"] for d in resolved],
        "checks": [
            {"name": "配置编译与依赖", "ok": True},
            {"name": "真实页面只读采集", "ok": collection_ok},
            {"name": "独立业务样例", "ok": bool(replays)},
            {
                "name": "有效需求与业务决定",
                "ok": requirements_ok
                and not ambiguity
                and record.route not in {"LOCAL_CODE", "HUMAN_HANDOFF"},
            },
        ],
    }
