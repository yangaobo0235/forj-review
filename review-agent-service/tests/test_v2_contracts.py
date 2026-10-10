from dataclasses import replace

from app.businesses.profiles import SCRAP_REPLACEMENT_QINGDAO
from app.contracts.review_schema import review_contract_schema
from app.models.review import ReviewRequest
from app.services.review import ReviewService
from app.workflow.planner import plan_capabilities


def test_profile_bindings_are_planned_in_stage_order() -> None:
    plan = plan_capabilities(SCRAP_REPLACEMENT_QINGDAO, {"old_vehicle"})
    # 同阶段按能力 ID 排序：owner_consistency、qingdao_replacement_policy、
    # vehicle_type_light_duty 与 verify_invoice 同为 POST_COMPARE。
    assert [item.capability_id for item in plan] == [
        "material_completeness",
        "scrap_certificate_qr",
        "owner_consistency",
        "qingdao_replacement_policy",
        "vehicle_type_light_duty",
        "verify_invoice",
        "affiliation_subject",
    ]


def test_canonical_profile_keeps_capabilities_when_legacy_selector_is_empty() -> None:
    profile = replace(SCRAP_REPLACEMENT_QINGDAO, external_checks=())
    assert "scrap_certificate_qr" in {item.capability_id for item in profile.bindings}


def test_review_response_has_trace_and_contract_schema() -> None:
    response = ReviewService().assist(
        ReviewRequest(page_url="https://example.test/review/1", region="qingdao")
    )
    assert response.trace_id
    schema = review_contract_schema()
    assert schema["protocol_version"] == "2.0"
    assert "ReviewTask" in schema["components"]

