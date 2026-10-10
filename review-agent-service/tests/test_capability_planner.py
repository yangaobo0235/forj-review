from app.businesses.profiles import (
    CONSISTENCY_DEFAULT,
    SCRAP_REPLACEMENT_QINGDAO,
    VEHICLE_SOURCE_DEFAULT,
)
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

