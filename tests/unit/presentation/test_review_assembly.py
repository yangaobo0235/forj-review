"""已配置页面按完整建议汇总风险，未配置页面保留人工审核提示。"""

from typing import Literal

from forj_review_service.contracts.recognition import CheckResult, RecognitionBatchResult
from forj_review_service.contracts.review import (
    BusinessType,
    FieldComparison,
    FieldStatus,
    Recommendation,
    Region,
    ReviewResponse,
)
from forj_review_service.presentation.assembly import assemble_review_response
from tests.support.pages import SCRAP_BUSINESS as TEST_BUSINESS


def response_for(
    business_type: BusinessType,
    region: Region,
    *,
    comparisons: list[FieldComparison] | None = None,
    issues: list[str] | None = None,
    presentation: Literal["FIELD_WORKBENCH", "MANUAL_REVIEW"] = "MANUAL_REVIEW",
) -> ReviewResponse:
    return ReviewResponse(
        presentation=presentation,
        business_type=business_type,
        region=region,
        profile_version="1.0",
        recommendation=Recommendation.REVIEW_REQUIRED,
        risk_level="MEDIUM",
        summary="审核检查正在进行",
        comparisons=comparisons or [],
        issues=issues or [],
    )


def comparison(field: str, status: FieldStatus) -> FieldComparison:
    return FieldComparison(
        field=field,
        left_value="A",
        right_value="A" if status is FieldStatus.MATCH else "B",
        status=status,
        message="多个来源字段一致" if status is FieldStatus.MATCH else "多个来源存在不同值",
    )


def conflict_business_check() -> CheckResult:
    return CheckResult(
        check_id="CROSS-TRANSFER-DATE-001",
        label="过户日期核验",
        status="CONFLICT",
        reason="过户日期早于发布时间",
    )


def test_manual_review_comparison_conflict_is_high_with_manual_summary() -> None:
    assembled = assemble_review_response(
        response_for(
            BusinessType.TRANSFER,
            Region.DEFAULT,
            comparisons=[comparison("transfer.vin", FieldStatus.CONFLICT)],
        ),
        RecognitionBatchResult(),
        business_checks=[],
        business=TEST_BUSINESS,
    )

    assert assembled.risk_level == "HIGH"
    assert assembled.summary == "发现明确字段冲突"
    assert assembled.recommendation is Recommendation.REVIEW_REQUIRED


def test_manual_review_business_check_conflict_does_not_raise_risk_level() -> None:
    assembled = assemble_review_response(
        response_for(BusinessType.TRANSFER, Region.DEFAULT),
        RecognitionBatchResult(),
        business_checks=[conflict_business_check()],
        business=TEST_BUSINESS,
    )

    assert assembled.recommendation is Recommendation.REVIEW_REQUIRED
    assert assembled.risk_level == "LOW"
    assert assembled.summary == "字段检查通过"


def test_manual_review_review_required_comparison_is_medium_with_manual_summary() -> None:
    assembled = assemble_review_response(
        response_for(
            BusinessType.TRANSFER,
            Region.DEFAULT,
            comparisons=[comparison("transfer.vin", FieldStatus.REVIEW_REQUIRED)],
        ),
        RecognitionBatchResult(),
        business_checks=[],
        business=TEST_BUSINESS,
    )

    assert assembled.risk_level == "MEDIUM"
    assert assembled.summary == "审核辅助结果需要人工复核"


def test_manual_review_clean_review_is_low_with_manual_summary() -> None:
    assembled = assemble_review_response(
        response_for(
            BusinessType.TRANSFER,
            Region.DEFAULT,
            comparisons=[comparison("transfer.vin", FieldStatus.MATCH)],
        ),
        RecognitionBatchResult(),
        business_checks=[],
        business=TEST_BUSINESS,
    )

    assert assembled.recommendation is Recommendation.PASS
    assert assembled.risk_level == "LOW"
    assert assembled.summary == "字段检查通过"


def test_configured_page_business_conflict_is_high_with_advice_summary() -> None:
    assembled = assemble_review_response(
        response_for(
            BusinessType.SCRAP_REPLACEMENT, Region.QINGDAO, presentation="FIELD_WORKBENCH"
        ),
        RecognitionBatchResult(),
        business_checks=[conflict_business_check()],
        business=TEST_BUSINESS,
    )

    assert assembled.recommendation is Recommendation.REVIEW_REQUIRED
    assert assembled.risk_level == "HIGH"
    assert assembled.summary == "发现 1 项需要审核人员确认"


def test_configured_page_clean_review_is_low_with_advice_summary() -> None:
    assembled = assemble_review_response(
        response_for(
            BusinessType.SCRAP_REPLACEMENT,
            Region.CHANGCHUN,
            presentation="FIELD_WORKBENCH",
            comparisons=[comparison("new_vehicle.vin", FieldStatus.MATCH)],
        ),
        RecognitionBatchResult(),
        business_checks=[],
        business=TEST_BUSINESS,
    )

    assert assembled.recommendation is Recommendation.PASS
    assert assembled.risk_level == "LOW"
    assert assembled.summary == "全部必检项目满足要求"
