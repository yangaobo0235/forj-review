"""Domain validation for effective profiles, shared by runtime assembly."""

from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.contracts.review import BusinessType


def validate_profile_policy(profile: BusinessProfile, policy_rule_ids: set[str]) -> None:
    rule_ids = {spec.capability_id for spec in profile.capabilities if spec.kind == "RULE"}
    policy_rules = rule_ids & policy_rule_ids
    if policy_rules and profile.replacement_policy is None:
        raise ValueError("地区政策规则缺少 replacement_policy 配置")
    policy = profile.replacement_policy
    if policy is not None and (
        profile.business_type is not BusinessType.SCRAP_REPLACEMENT
        or policy.region != profile.region
        or policy.version != profile.version
        or policy.policy_id != f"scrap_replacement_{profile.region}"
        or (policy_rules and policy_rules != {f"{profile.region}_replacement_policy"})
    ):
        raise ValueError("地区政策的归属、版本或规则标识与 Profile 不一致")


def validate_evidence_stages(profile: BusinessProfile) -> None:
    pack = profile.pack_declaration
    comparison_fields = set(profile.required_fields)
    if pack:
        comparison_fields.update(
            field.key for field in pack.fields if field.mode is not None or field.material_field
        )
    for spec in profile.capabilities:
        if spec.stage in {"POST_COMPARE", "FINAL_REVIEW"} and comparison_fields.intersection(
            spec.output_facts
        ):
            raise ValueError(
                f"能力 {spec.capability_id} 在字段比较后产出比较字段；请使用 PRE_COMPARE 或更早阶段"
            )
