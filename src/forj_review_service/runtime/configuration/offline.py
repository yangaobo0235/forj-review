"""Configuration maintenance without a running assistant service or model."""

import hashlib
import json
from dataclasses import replace

from forj_review_service.businesses.page_config.inspection import digest
from forj_review_service.businesses.page_config.maintenance import edit_fields
from forj_review_service.runtime.configuration.publication import (
    commit_version,
    configuration_baseline,
    preview_version,
    rollback_definition,
    stage_version,
)
from forj_review_service.runtime.configuration.service import (
    ReplaySuite,
    replay_suite,
    validated_snapshot,
)
from forj_review_service.runtime.configuration.verification import (
    evaluate_collection,
    proof_is_current,
)


def prepare_maintenance(
    state, page_id, version=None, operations=(), *, candidate=None, rollback=None
):
    _, registry = validated_snapshot(state)
    profile = next((p for p in registry.active_profiles if p.page_id == page_id), None)
    if profile is None or profile.pack_declaration is None:
        raise ValueError("只能维护已有且已配置的页面")
    if rollback:
        if operations or candidate:
            raise ValueError("回退不能同时编辑内容")
        draft = rollback_definition(state, page_id, rollback)
    elif candidate:
        draft = candidate
        if draft.get("page_id") != page_id:
            raise ValueError("候选页面与目标不一致")
    else:
        if not version:
            raise ValueError("字段维护必须指定新版本")
        draft = {**state.source(page_id, profile.version), "active": True}
        draft["version"] = version
        draft = edit_fields(draft, list(operations))
    preview = preview_version(state, draft, activation_only=bool(rollback))
    return {
        "kind": "field_maintenance",
        "baseline": configuration_baseline(state),
        "draft": draft,
        "activation_only": bool(rollback),
        "preview": preview,
    }


async def verify_maintenance(state, plan, evidence, suite_data):
    if plan["baseline"] != configuration_baseline(state):
        raise ValueError("配置基准已变化，请重新导出和验证")
    draft = plan["draft"]
    preview = preview_version(state, draft, activation_only=plan["activation_only"])
    if preview != plan["preview"]:
        raise ValueError("候选或内核已变化，请重新准备和验证")
    proof = evidence["proof"]
    snapshot = evidence["snapshot"]
    binding = {
        "revision": evidence["revision"],
        **(
            {"evidence_revision": evidence["evidence_revision"]}
            if "evidence_revision" in evidence
            else {"intent_revision": evidence["intent_revision"]}
            if "intent_revision" in evidence
            else {}
        ),
        "snapshot_digest": hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "capture_id": evidence.get("capture_id"),
        **{
            key: sorted(evidence[key])
            for key in (
                ("reference_fields", "reference_samples")
                if evidence.get("schema_version") == "2.0"
                else ("selected_fields", "selected_samples", "required_samples")
            )
        },
    }
    if (
        proof.get("kind") != "collection_trial"
        or proof.get("binding") != binding
        or proof.get("candidate_digest") != preview["candidate_digest"]
        or not proof_is_current(proof)
    ):
        raise ValueError("试采集证明过期或与现场、候选不匹配")
    facts = proof["facts"]
    if (
        any(
            facts.get(key) != snapshot.get(key)
            for key in (
                "snapshot_id",
                "page_instance_id",
                "page_url",
                "structure_digest",
            )
        )
        or facts.get("candidate_digest") != preview["candidate_digest"]
    ):
        raise ValueError("试采集原始事实与页面现场不匹配")
    decisions = [
        d
        for d in evidence["decisions"]
        if d["status"] == "RESOLVED" and d["revision"] == evidence["revision"]
    ]
    field_ids = {item["candidate_id"] for item in snapshot["fields"]}
    sample_ids = {item["sample_id"] for item in snapshot["samples"]}
    reference_fields = evidence.get("reference_fields", evidence.get("selected_fields", []))
    reference_samples = evidence.get("reference_samples", evidence.get("selected_samples", []))
    legacy_scope = evidence.get("schema_version", "1.0") != "2.0"
    if (
        set(reference_fields) - field_ids
        or set(reference_samples) - sample_ids
        or set(evidence.get("required_samples", [])) - set(reference_samples)
    ):
        raise ValueError("选择项必须来自当前现场")
    fields = {item["key"] for item in preview["manifest"]["fields"]}
    materials = {item["document_type"] for item in preview["manifest"]["materials"]}
    mappings = {
        d.get("sample_id"): d.get("material") for d in decisions if d.get("material") in materials
    }
    if any(d.get("sample_id") and d.get("material") not in materials for d in decisions):
        raise ValueError("当前材料映射指向已不存在的候选材料")
    if legacy_scope and any(
        not any(d.get("candidate_id") == cid and d.get("field_key") in fields for d in decisions)
        for cid in reference_fields
    ):
        raise ValueError("选中字段缺少有效映射")
    if any(
        mappings.get(sample) not in preview["required_materials"]
        for sample in evidence.get("required_samples", [])
    ):
        raise ValueError("所选必传材料未配置为必传")
    trial = evaluate_collection(
        preview["manifest"],
        facts,
        draft["page_id"],
        reference_samples if legacy_scope else sorted(mappings),
        mappings,
    )
    if not trial["ok"]:
        raise ValueError("浏览器试采集检查未通过：" + json.dumps(trial, ensure_ascii=False))
    suite = ReplaySuite.model_validate(suite_data)
    if not any(
        c.request.page_id == draft["page_id"] and c.request.profile_version == draft["version"]
        for c in suite.cases
    ):
        raise ValueError("回放必须包含目标页面和候选版本")
    _, staged = stage_version(state, draft, activation_only=plan["activation_only"])
    _, registry = validated_snapshot(staged)
    replay = await replay_suite(registry, suite)
    if not replay["ok"]:
        raise ValueError("独立样例回放失败：" + json.dumps(replay, ensure_ascii=False))
    return {
        "page_id": draft["page_id"],
        "candidate_digest": preview["candidate_digest"],
        "trial": trial,
        "replay": replay,
        "case_digest": digest(suite.model_dump(mode="json")),
    }


async def save_maintenance(state, plan, evidence, suite_data, operation_id):
    previous = state.receipts.get(operation_id)
    if previous:
        receipt = previous
        if (receipt["page_id"], receipt["version"], receipt["candidate_digest"]) != (
            plan["draft"]["page_id"],
            plan["draft"]["version"],
            plan["preview"]["candidate_digest"],
        ):
            raise ValueError("保存操作 ID 已用于其他候选")
        return state, receipt
    report = await verify_maintenance(state, plan, evidence, suite_data)
    staged, receipt = commit_version(
        state,
        plan["draft"],
        operation_id,
        report["candidate_digest"],
        activation_only=plan["activation_only"],
    )
    receipt.update(
        source="manual_cli", verification=report, baseline=plan["baseline"], operator=None
    )
    return replace(
        staged, pending=receipt, receipts={**staged.receipts, operation_id: receipt}
    ), receipt
