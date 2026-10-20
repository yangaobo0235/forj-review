"""根据 Profile 生成能力执行计划。"""

from dataclasses import dataclass
from typing import Literal

from forj_review_service.businesses.profiles import BusinessProfile
from forj_review_service.capabilities.specs import order_capabilities

CapabilityStatus = Literal["READY", "SKIPPED", "BLOCKED", "NOT_CONFIGURED"]


@dataclass(frozen=True)
class CapabilityPlanItem:
    capability_id: str
    status: CapabilityStatus
    reason: str = ""
    stage: str = "POST_COMPARE"
    dependencies: tuple[str, ...] = ()
    missing_dependencies: tuple[str, ...] = ()


def plan_capabilities(
    profile: BusinessProfile, available: set[str] | None = None
) -> tuple[CapabilityPlanItem, ...]:
    """只规划 Profile 声明的能力；未配置业务不会隐式执行能力。"""
    if not profile.rules_configured:
        return (
            CapabilityPlanItem(
                "profile",
                "NOT_CONFIGURED",
                profile.unconfigured_message or "业务规则未配置",
                "FINAL_REVIEW",
            ),
        )
    items = []
    bindings = {binding.capability_id: binding for binding in profile.bindings}
    specs = order_capabilities(profile.capabilities)
    available_facts = set(available or ())
    produced_facts: set[str] = set()
    for spec in specs:
        binding = bindings[spec.capability_id]
        if not binding.enabled:
            items.append(
                CapabilityPlanItem(
                    spec.capability_id,
                    "SKIPPED",
                    "Profile 已停用该能力",
                    spec.stage,
                    spec.dependencies,
                    (),
                )
            )
            continue
        required = spec.required if binding.required is None else binding.required
        missing = set()
        if available is not None:
            missing = set(spec.dependencies) - available_facts
            missing.update(set(spec.input_facts) - available_facts - produced_facts)
        status: CapabilityStatus = ("BLOCKED" if required else "SKIPPED") if missing else "READY"
        items.append(
            CapabilityPlanItem(
                spec.capability_id,
                status,
                "缺少能力所需材料或事实：" + "、".join(sorted(missing)) if missing else "",
                spec.stage,
                spec.dependencies,
                tuple(sorted(missing)),
            )
        )
        if status == "READY":
            produced_facts.update(spec.output_facts)
    return tuple(items)
