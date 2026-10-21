from dataclasses import replace

from forj_review_service.contracts.review import ReviewRequest
from forj_review_service.contracts.review_schema import review_contract_schema
from forj_review_service.runtime.review_service import ReviewService
from forj_review_service.runtime.workflow.planner import plan_capabilities
from tests.support.pages import SCRAP_REPLACEMENT_QINGDAO, business_registry


def test_profile_bindings_are_planned_in_stage_order() -> None:
    plan = plan_capabilities(SCRAP_REPLACEMENT_QINGDAO, {"old_vehicle"})
    # 同阶段按能力 ID 排序；排放标准规则在 POST_COMPARE，挂靠自动动作在 FINAL_REVIEW。
    assert [item.capability_id for item in plan] == [
        "material_completeness",
        "scrap_certificate_qr",
        "emission_standard",
        "owner_consistency",
        "qingdao_replacement_policy",
        "vehicle_type_light_duty",
        "verify_invoice",
        "affiliation_subject",
        "set_affiliation",
    ]


def test_capability_bindings_use_registered_defaults_when_not_overridden() -> None:
    profile = replace(SCRAP_REPLACEMENT_QINGDAO, binding_declarations=())
    assert "scrap_certificate_qr" in {item.capability_id for item in profile.bindings}


def test_review_response_has_trace_and_contract_schema() -> None:
    response = ReviewService(registry=business_registry()).assist(
        ReviewRequest(page_url="https://example.test/review/1", region="qingdao")
    )
    assert response.trace_id
    schema = review_contract_schema()
    assert schema["protocol_version"] == "2.0"
    assert "ReviewTask" in schema["components"]
