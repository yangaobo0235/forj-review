"""Compose capability registries without coupling the graph to business adapters."""

from collections.abc import Mapping
from typing import Any

from forj_review_assistant.businesses.external_check_catalog import build_external_check_handlers
from forj_review_assistant.businesses.page_action_catalog import build_page_actions
from forj_review_assistant.businesses.profile_validation import (
    validate_evidence_stages,
    validate_profile_policy,
)
from forj_review_assistant.businesses.rules.catalog import build_rule_handlers
from forj_review_assistant.capabilities import CapabilityRegistry
from forj_review_assistant.capabilities.business_rules import BusinessRuleRegistry
from forj_review_assistant.capabilities.external_checks import ExternalCheckRegistry
from forj_review_assistant.capabilities.page_actions import PageActionHandler
from forj_review_assistant.capabilities.specs import (
    BusinessRuleHandler,
    CapabilityResult,
    CapabilitySpec,
    ExternalCheckHandler,
    ExternalCheckSpec,
    ReviewExecutionContext,
)
from forj_review_assistant.capabilities.subgraphs import build_capability_subgraph
from forj_review_assistant.contracts.evidence import EvidenceFact
from forj_review_assistant.contracts.recognition import CheckResult
from forj_review_assistant.contracts.review import QrCheck
from forj_review_assistant.evidence.comparison.check_results import qr_review_checks


class WorkflowCapabilities:
    def __init__(
        self,
        service: Any,
        *,
        external_check_handlers: Mapping[str, ExternalCheckHandler] | None = None,
        business_rule_handlers: Mapping[str, BusinessRuleHandler] | None = None,
        page_action_handlers: Mapping[str, PageActionHandler] | None = None,
    ) -> None:
        policy_rule_ids = {
            spec.capability_id
            for profile in service.registry.profiles
            for spec in profile.capabilities
            if spec.capability_id.endswith("_replacement_policy")
        }
        self.external_checks = ExternalCheckRegistry(
            {
                **build_external_check_handlers(service._collect_qr_checks),
                **(external_check_handlers or {}),
            }
        )
        builtin_rules = build_rule_handlers(tuple(sorted(policy_rule_ids)))
        overridden = builtin_rules.keys() & (business_rule_handlers or {}).keys()
        if overridden:
            raise ValueError(f"不能覆盖内置业务规则：{', '.join(sorted(overridden))}")
        self.business_rules = BusinessRuleRegistry(
            {**builtin_rules, **(business_rule_handlers or {})}
        )
        self.page_actions = build_page_actions(page_action_handlers)
        self.capabilities = CapabilityRegistry(
            {
                **{
                    key: build_capability_subgraph(self.execute_external)
                    for key in self.external_checks.ids
                },
                **{
                    key: build_capability_subgraph(self.execute_rule)
                    for key in self.business_rules.ids
                },
                "material_completeness": build_capability_subgraph(self.execute_material),
            }
        )
        for profile in service.registry.profiles:
            self.capabilities.validate(profile.capabilities)
            self.capabilities.validate_bindings(profile.bindings, profile.capabilities)
            self.external_checks.validate(
                tuple(
                    ExternalCheckSpec(
                        spec.capability_id, "REQUIRED" if spec.required else "WHEN_PRESENT"
                    )
                    for spec in profile.capabilities
                    if spec.kind == "EXTERNAL"
                )
            )
            self.business_rules.validate(
                tuple(spec.capability_id for spec in profile.capabilities if spec.kind == "RULE")
            )
            self.page_actions.validate(tuple(profile.enabled_page_actions))
            validate_profile_policy(profile, policy_rule_ids)
            validate_evidence_stages(profile)

    async def execute_external(
        self, context: ReviewExecutionContext, spec: CapabilitySpec
    ) -> CapabilityResult:
        results = await self.external_checks.execute(
            (
                ExternalCheckSpec(
                    spec.capability_id,
                    "REQUIRED" if spec.required else "WHEN_PRESENT",
                ),
            ),
            context,
        )
        qr = [item for item in results if isinstance(item, QrCheck)]
        if qr and spec.stage in {"POST_COMPARE", "FINAL_REVIEW"}:
            raise ValueError("二维码字段证据必须在字段比较前产生")
        checks = [item for item in results if isinstance(item, CheckResult)]
        checks.extend(qr_review_checks(qr, context.request.images))
        return CapabilityResult(
            capability_id=spec.capability_id,
            status="SUCCEEDED",
            checks=checks,
            qr_checks=qr,
            evidence=[item for item in results if isinstance(item, EvidenceFact)],
        )

    async def execute_rule(
        self, context: ReviewExecutionContext, spec: CapabilitySpec
    ) -> CapabilityResult:
        result = self.business_rules.execute((spec.capability_id,), context)
        return CapabilityResult(
            capability_id=spec.capability_id,
            status="SUCCEEDED",
            checks=list(result.checks),
            evidence=list(result.evidence),
            page_actions=list(result.page_action_candidates),
            page_action_intents=list(result.page_action_intents),
        )

    @staticmethod
    async def execute_material(
        context: ReviewExecutionContext, spec: CapabilitySpec
    ) -> CapabilityResult:
        report = context.material_completeness or context.batch.material_completeness
        return CapabilityResult(
            capability_id=spec.capability_id,
            status="SUCCEEDED",
            checks=[
                CheckResult(
                    check_id="MATERIAL-COMPLETENESS",
                    label="材料完整性",
                    status="MATCH" if report and report.status == "COMPLETE" else "INSUFFICIENT",
                    reason="材料完整"
                    if report and report.status == "COMPLETE"
                    else "材料缺失或存在不确定证据",
                )
            ],
        )
