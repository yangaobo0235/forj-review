import pytest

from forj_review_service.businesses.profiles import BusinessProfile
from forj_review_service.businesses.registry import BusinessRegistry
from forj_review_service.capabilities.specs import CapabilitySpec, RuleExecutionResult
from forj_review_service.contracts.recognition import CheckResult
from forj_review_service.contracts.review import BusinessType, Region, ReviewRequest
from forj_review_service.runtime.review_service import ReviewService


def single_field_profile():
    return BusinessProfile(
        business_type=BusinessType.VEHICLE_SOURCE,
        region=Region.DEFAULT,
        version="1.0",
        required_fields=(),
        sections=(),
        rules_configured=True,
        capability_specs=(CapabilitySpec("single_field_test", kind="RULE", stage="POST_COMPARE"),),
        page_action_ids=(),
    )


@pytest.mark.asyncio
async def test_profile_without_material_or_field_requirements_has_no_missing_input_issues():
    service = ReviewService(
        registry=BusinessRegistry((single_field_profile(),)),
        business_rule_handlers={
            "single_field_test": lambda _: RuleExecutionResult(
                checks=(
                    CheckResult(
                        check_id="READY", label="配置核验", status="MATCH", reason="配置满足"
                    ),
                )
            )
        },
    )
    response = await service.assist_async(
        ReviewRequest(
            page_url="https://example.test/empty",
            business_type="vehicle_source",
            region="default",
        )
    )
    assert response.issues == []
    assert response.agent_advice.findings == []
    assert response.recommendation.value == "PASS"
    assert response.risk_level == "LOW"
