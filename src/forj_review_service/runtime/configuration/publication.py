"""Atomic snapshot preparation shared by service and offline maintenance tools."""

from dataclasses import replace

from forj_review_service.businesses.packs import build_collect_manifest
from forj_review_service.businesses.page_catalog import page_catalog
from forj_review_service.businesses.page_config.inspection import (
    compare_configurations,
    configuration_snapshot,
    digest,
    mapping_diff,
    profile_fingerprint,
)
from forj_review_service.businesses.page_config.kernel import KERNEL_VERSION
from forj_review_service.businesses.page_config.state import ConfigurationState
from forj_review_service.contracts.errors import ConfigurationInputError
from forj_review_service.runtime.configuration.service import validated_snapshot


def configuration_baseline(state):
    return state.baseline(KERNEL_VERSION)


def rollback_definition(state, page_id, version):
    source = state.source(page_id, version)
    if source is None:
        raise ConfigurationInputError("历史版本不存在")
    return {**source, "active": True}


def preview_version(state, candidate, *, activation_only=False):
    definition, staged = stage_version(state, candidate, activation_only=activation_only)
    _, before = validated_snapshot(state)
    _, after = validated_snapshot(staged)
    profile = after.resolve(
        definition.business_type, definition.region, definition.version, definition.page_id
    )
    policy = profile.pack_declaration.material_policy
    current = next(p for p in before.active_profiles if p.page_id == definition.page_id)
    diff = compare_configurations(
        configuration_snapshot(before.profiles, before.active_profiles),
        configuration_snapshot(after.profiles, after.active_profiles),
    )
    old_fields = state.source(current.page_id, current.version)["fields"]
    new_fields = definition.model_dump(mode="json")["fields"]
    diff["field_changes"] = {
        "page_id": definition.page_id,
        **mapping_diff(
            {f["key"]: f for f in old_fields},
            {f["key"]: f for f in new_fields},
        ),
    }
    return {
        "candidate_digest": digest(
            {
                "draft": candidate,
                "baseline": configuration_baseline(state),
                "effective": profile_fingerprint(profile),
            }
        ),
        "effective_digest": profile_fingerprint(profile),
        "kernel_version": KERNEL_VERSION,
        "page_catalog": page_catalog(after.active_profiles),
        "page_id": definition.page_id,
        "required_materials": sorted({m.document_type for m in policy.materials})
        if policy and policy.mode == "enforce"
        else [],
        "manifest": build_collect_manifest(profile.pack_declaration),
        "diff": diff,
    }


def stage_version(state: ConfigurationState, candidate: dict, *, activation_only=False):
    definition, staged = state.stage(candidate, activation_only=activation_only)
    validated_snapshot(staged)
    return definition, staged


def commit_version(state, candidate, operation_id, candidate_digest, *, activation_only=False):
    if not operation_id or not operation_id.isalnum():
        raise ValueError("保存操作标识无效")
    definition, staged = stage_version(state, candidate, activation_only=activation_only)
    _, registry = validated_snapshot(staged)
    profile = registry.resolve(
        definition.business_type, definition.region, definition.version, definition.page_id
    )
    receipt = {
        "operation_id": operation_id,
        "page_id": definition.page_id,
        "version": definition.version,
        "candidate_digest": candidate_digest,
        "effective_digest": profile_fingerprint(profile),
        "kernel_version": KERNEL_VERSION,
        "state": "PUBLISHED",
    }
    return replace(
        staged, pending=receipt, receipts={**staged.receipts, operation_id: receipt}
    ), receipt
