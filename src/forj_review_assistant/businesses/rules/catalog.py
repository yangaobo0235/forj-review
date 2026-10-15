"""Built-in business rule assembly, independent of the workflow graph."""

from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType

from forj_review_assistant.businesses.rules.affiliation import run_affiliation_subject
from forj_review_assistant.businesses.rules.invoice_verification import run_verify_invoice
from forj_review_assistant.businesses.rules.owner_consistency import (
    OWNER_CONSISTENCY_CHECK_ID,
    run_owner_consistency,
)
from forj_review_assistant.businesses.rules.replacement_policy import run_replacement_policy
from forj_review_assistant.businesses.rules.transfer_invoice_date import (
    CHECK_ID as TRANSFER_DATE_CHECK_ID,
)
from forj_review_assistant.businesses.rules.transfer_invoice_date import run_transfer_invoice_date
from forj_review_assistant.businesses.rules.vehicle_model import (
    EMISSION_CHECK_ID,
    MODEL_CODE_CHECK_ID,
    POWER_CHECK_ID,
    run_vehicle_model,
)
from forj_review_assistant.businesses.rules.vehicle_type_light_duty import (
    VEHICLE_TYPE_LIGHT_DUTY_CHECK_ID,
    run_vehicle_type_light_duty,
)
from forj_review_assistant.capabilities.specs import BusinessRuleHandler, CapabilitySpec


@dataclass(frozen=True)
class RuleDefinition:
    handler: BusinessRuleHandler
    fields: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    stage: str = "POST_COMPARE"


# Fields read by handlers are explicit dependencies for configuration editing.
# verify_invoice reads the pack's invoice_verification_field, validated separately.
RULE_DEFINITIONS = MappingProxyType(
    {
        "vehicle_model_consistency": RuleDefinition(
            run_vehicle_model,
            (
                "vehicle.model",
                "vehicle.engine_model",
                "vehicle.model_code",
                "vehicle.power_kw",
                "vehicle.emission_standard",
            ),
            (POWER_CHECK_ID, MODEL_CODE_CHECK_ID, EMISSION_CHECK_ID),
        ),
        "owner_consistency": RuleDefinition(
            run_owner_consistency,
            ("old_vehicle.owner", "new_vehicle.owner"),
            (OWNER_CONSISTENCY_CHECK_ID,),
        ),
        "vehicle_type_light_duty": RuleDefinition(
            run_vehicle_type_light_duty, ("old_vehicle.type",), (VEHICLE_TYPE_LIGHT_DUTY_CHECK_ID,)
        ),
        "transfer_invoice_date": RuleDefinition(
            run_transfer_invoice_date,
            ("transfer.invoice_date", "application.source_published_at"),
            (TRANSFER_DATE_CHECK_ID,),
        ),
        "affiliation_subject": RuleDefinition(
            run_affiliation_subject,
            (
                "old_vehicle.owner",
                "new_vehicle.owner",
                "application.owner_type",
                "old_vehicle.affiliation",
                "new_vehicle.affiliation",
            ),
            ("AFFILIATION-SUBJECT-001", "AFFILIATION-AUX-CUSTOMER-NAME"),
            "FINAL_REVIEW",
        ),
        "verify_invoice": RuleDefinition(run_verify_invoice),
    }
)

RULE_HANDLERS = MappingProxyType({key: value.handler for key, value in RULE_DEFINITIONS.items()})
RULE_FIELDS = MappingProxyType(
    {key: frozenset(value.fields) for key, value in RULE_DEFINITIONS.items()}
)

REPLACEMENT_POLICY_CHECKS = frozenset({"POLICY-INVOICE-DATE", "POLICY-DISPOSAL-DEADLINE"})
TASK_PROJECTOR_RULES = MappingProxyType({
    "old_vehicle_affiliation": "affiliation_subject",
    "new_vehicle_affiliation": "affiliation_subject",
})


def rule_spec(rule_id: str) -> CapabilitySpec:
    if rule_id not in RULE_HANDLERS:
        raise ValueError(f"未注册的可配置业务规则：{rule_id}")
    return CapabilitySpec(
        rule_id,
        stage=RULE_DEFINITIONS[rule_id].stage,
    )


def build_rule_handlers(policy_rule_ids: Sequence[str]) -> dict[str, BusinessRuleHandler]:
    return {
        **RULE_HANDLERS,
        **{rule_id: run_replacement_policy for rule_id in policy_rule_ids},
    }
