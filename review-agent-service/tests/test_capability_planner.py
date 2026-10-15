from dataclasses import replace

import pytest

from app.businesses.profiles import (
    CONSISTENCY_DEFAULT,
    SCRAP_REPLACEMENT_QINGDAO,
    VEHICLE_SOURCE_DEFAULT,
)
from app.capabilities.specs import CapabilityBinding, CapabilitySpec
from app.workflow.planner import plan_capabilities


def test_configured_profile_plans_declared_capabilities_only():
    ids = {item.capability_id for item in plan_capabilities(SCRAP_REPLACEMENT_QINGDAO)}
    assert ids == {
        "material_completeness",
        "scrap_certificate_qr",
        "qingdao_replacement_policy",
        "affiliation_subject",
        "verify_invoice",
        "owner_consistency",
        "vehicle_type_light_duty",
    }


def test_vehicle_source_profile_plans_its_own_capabilities():
    ids = {item.capability_id for item in plan_capabilities(VEHICLE_SOURCE_DEFAULT)}

    assert ids == {"material_completeness", "vehicle_model_consistency"}
    assert not any(item.endswith("_replacement_policy") for item in ids)


def test_unconfigured_profile_is_explicitly_marked():
    plan = plan_capabilities(CONSISTENCY_DEFAULT)
    assert [(item.capability_id, item.status) for item in plan] == [("profile", "NOT_CONFIGURED")]


def dependency_profile(*specs, bindings=()):
    return replace(VEHICLE_SOURCE_DEFAULT, material_policy=None, external_checks=(), rule_groups=(),
                   capability_specs=specs, binding_declarations=bindings)


@pytest.mark.parametrize("same_stage", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_fact_producers_precede_consumers_regardless_of_declaration_order(same_stage, reverse):
    producer = CapabilitySpec("z_producer", stage="POST_COMPARE" if same_stage else "PRE_COMPARE",
                              output_facts=("verified",))
    consumer = CapabilitySpec("a_consumer", input_facts=("verified",))
    specs = (consumer, producer) if reverse else (producer, consumer)
    plan = plan_capabilities(dependency_profile(*specs), set())
    assert [(p.capability_id, p.status) for p in plan] == [("z_producer", "READY"), ("a_consumer", "READY")]


def test_disabled_producer_does_not_satisfy_consumer():
    profile = dependency_profile(
        CapabilitySpec("producer", output_facts=("verified",)),
        CapabilitySpec("consumer", input_facts=("verified",)),
        bindings=(CapabilityBinding("producer", enabled=False),),
    )
    plan = {p.capability_id: p for p in plan_capabilities(profile, set())}
    assert plan["producer"].status == "SKIPPED"
    assert plan["consumer"].status == "BLOCKED"
    assert plan["consumer"].missing_dependencies == ("verified",)


@pytest.mark.parametrize("specs, message", [
    ((CapabilitySpec("a", input_facts=("b.fact",), output_facts=("a.fact",)),
      CapabilitySpec("b", input_facts=("a.fact",), output_facts=("b.fact",))), "循环"),
    ((CapabilitySpec("early", stage="PRE_COMPARE", input_facts=("late.fact",)),
      CapabilitySpec("late", output_facts=("late.fact",))), "阶段"),
    ((CapabilitySpec("a", output_facts=("same",)), CapabilitySpec("b", output_facts=("same",))), "重复"),
])
def test_invalid_fact_dependencies_fail_before_execution(specs, message):
    with pytest.raises(ValueError, match=message):
        plan_capabilities(dependency_profile(*specs), set())

