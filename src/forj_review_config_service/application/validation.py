"""Candidate validation and browser evidence bound to the current working context."""

from uuid import uuid4

from forj_review_config_service.application.conversation import fingerprint


def trial_binding(record) -> dict:
    return {
        "revision": record.revision,
        "snapshot_digest": fingerprint(record.snapshot.model_dump(mode="json")),
        "capture_id": record.capture_id,
        "selected_fields": sorted(record.selected_fields),
        "selected_samples": sorted(record.selected_samples),
        "required_samples": sorted(record.required_samples),
    }


def evaluate_trial(record, manifest: dict, facts: dict, kernel) -> dict:
    mapping = {
        d.get("sample_id"): d.get("material") for d in record.decisions
        if d["status"] == "RESOLVED" and d["revision"] == record.revision and d.get("sample_id")
    }
    return kernel.evaluate_collection(manifest, facts, record.draft.get("page_id"),
                                      record.selected_samples, mapping)


def current_trial(record, preview: dict, result: dict, kernel) -> bool:
    if (
        result.get("kind") != "collection_trial"
        or not kernel.proof_is_current(result)
        or result.get("binding") != trial_binding(record)
        or result.get("candidate_digest") != preview["candidate_digest"]
    ):
        return False
    facts = result.get("facts")
    return isinstance(facts, dict) and evaluate_trial(record, preview["manifest"], facts, kernel)["ok"]


async def validate_candidate(kernel, record):
    preview = await kernel.validate(record)
    blockers = []
    manifest = preview["manifest"]
    fields = {f["key"] for f in manifest["fields"]}
    materials = {m["document_type"] for m in manifest["materials"]}
    resolved = [
        d
        for d in record.decisions
        if d["status"] == "RESOLVED" and d["revision"] == record.revision
    ]
    for cid in record.selected_fields:
        if not any(d.get("candidate_id") == cid and d.get("field_key") in fields for d in resolved):
            blockers.append("选中字段尚未建立有效映射：" + cid)
    for sample in record.selected_samples:
        if not any(
            d.get("sample_id") == sample and d.get("material") in materials for d in resolved
        ):
            blockers.append("选中材料尚未建立有效映射：" + sample)
    for sample in record.required_samples:
        if not any(
            d.get("sample_id") == sample and d.get("material") in preview["required_materials"]
            for d in resolved
        ):
            blockers.append("用户选择的必传材料尚未配置为必传：" + sample)
    for requirement in record.requirements:
        if not any(d.get("requirement_id") == requirement["requirement_id"] for d in resolved):
            blockers.append("用户要求尚未完成：" + requirement["requirement_id"])
    if any(
        d["status"] not in {"RESOLVED", "SUPERSEDED"} and d["revision"] == record.revision
        for d in record.decisions
    ):
        blockers.append("存在未解决的业务歧义或能力缺口")
    results = list(record.tool_results.values())
    if not any(isinstance(r, dict) and current_trial(record, preview, r, kernel) for r in results):
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
        "ok": not blockers,
        "blockers": blockers,
        "replays": replays,
        "decision_summary": [d["reason"] for d in resolved],
    }
