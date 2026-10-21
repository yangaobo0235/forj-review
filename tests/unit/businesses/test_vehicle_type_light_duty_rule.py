"""报废车辆类型档位复核：材料写「轻型」时该字段转人工复核（青岛口径）。"""

import pytest

from forj_review_service.businesses.rules.vehicle_type_light_duty import (
    VEHICLE_TYPE_LIGHT_DUTY_CHECK_ID,
    build_vehicle_type_light_duty_check,
)
from forj_review_service.capabilities.specs import ReviewExecutionContext
from forj_review_service.contracts.recognition import RecognitionBatchResult
from forj_review_service.contracts.review import (
    FieldComparison,
    FieldObservation,
    FieldStatus,
    ReviewRequest,
)
from forj_review_service.presentation.routing import build_review_tasks
from forj_review_service.runtime.review_service import ReviewService
from tests.support.pages import (
    SCRAP_REPLACEMENT_CHANGCHUN,
    SCRAP_REPLACEMENT_QINGDAO,
    business_registry,
)

PAGE = {"old_vehicle.type": "载货车"}


def observation(value: object, document_type: str, index: int) -> FieldObservation:
    return FieldObservation(
        field="old_vehicle.type",
        source_type="image",
        source_id=f"{document_type}-{index}",
        image_id=f"{document_type}-{index}",
        image_index=index,
        value=value,
        document_type=document_type,
        business_scope="old_vehicle",
    )


def run(observations: list[FieldObservation], profile=SCRAP_REPLACEMENT_QINGDAO):
    return build_vehicle_type_light_duty_check(
        ReviewExecutionContext(
            request=ReviewRequest(
                page_url="https://example.test/scrap-replace-qingdao/review/1",
                region=profile.region.value,
                page_fields=PAGE,
            ),
            profile=profile,
            batch=RecognitionBatchResult(),
            observations=tuple(observations),
        )
    ).checks


def test_light_duty_material_value_asks_for_human_review() -> None:
    checks = run([observation("轻型厢式货车", "vehicle_license", 1)])

    assert len(checks) == 1
    check = checks[0]
    assert check.check_id == VEHICLE_TYPE_LIGHT_DUTY_CHECK_ID
    assert check.status == "INSUFFICIENT"
    assert "轻型" in check.reason
    assert "机动车行驶证" in check.reason
    assert "轻型厢式货车" in check.reason


@pytest.mark.parametrize("value", ["重型厢式货车", "中型仓栅式货车", "重型半挂牵引车"])
def test_other_duty_classes_change_nothing(value: str) -> None:
    """档位不在比对范围内，但只有轻型这一档需要人看。"""
    assert run([observation(value, "vehicle_license", 1)]) == ()


def test_any_one_material_is_enough() -> None:
    """行驶证写重型、回收证明写轻型：折叠后同为载货汽车，比对判一致，
    所以必须由本规则把轻型点名出来交人工。"""
    checks = run(
        [
            observation("重型厢式货车", "vehicle_license", 1),
            observation("轻型厢式货车", "scrap_certificate", 2),
        ]
    )

    assert len(checks) == 1
    assert "报废机动车回收证明" in checks[0].reason
    assert "机动车行驶证" not in checks[0].reason


def test_one_material_submitted_twice_is_named_once() -> None:
    """正反面、重复上传是同一份材料的多次读取，不该点名两遍。"""
    checks = run(
        [
            observation("轻型厢式货车", "vehicle_license", 1),
            observation("轻型厢式货车", "vehicle_license", 2),
        ]
    )

    assert checks[0].reason.count("机动车行驶证") == 1


def test_unreadable_value_is_not_a_light_duty_hit() -> None:
    assert run([observation("轻型厢式货车？无法确认", "vehicle_license", 1)]) == ()


def test_page_value_is_not_material_evidence() -> None:
    """页面的报废车辆类型是粗分类下拉，档位只看材料原文。"""
    page_side = FieldObservation(
        field="old_vehicle.type",
        source_type="page",
        source_id="page",
        value="轻型载货车",
    )

    assert run([page_side]) == ()


def test_regions_without_keywords_never_raise_the_check() -> None:
    """关键词由地区政策声明：长春未配置，同样材料不产出检查项。"""
    checks = run(
        [observation("轻型厢式货车", "vehicle_license", 1)],
        profile=SCRAP_REPLACEMENT_CHANGCHUN,
    )

    assert checks == ()


def test_check_merges_into_the_vehicle_type_field_task() -> None:
    """结论并进「报废车辆类型」字段行：审核员在字段卡片上看到待复核，
    而不是在页面外核验里另找一张卡。"""
    checks = run([observation("轻型厢式货车", "vehicle_license", 1)])
    steps = build_review_tasks(
        request=ReviewRequest(
            page_url="https://example.test/scrap-replace-qingdao/review/1",
            region="qingdao",
            page_fields=PAGE,
        ),
        profile=SCRAP_REPLACEMENT_QINGDAO,
        comparisons=[
            FieldComparison(
                field="old_vehicle.type",
                left_value="轻型厢式货车",
                right_value="载货车",
                status=FieldStatus.MATCH,
                message="多个来源字段一致",
                evidence=[
                    {
                        "source": "图片识别",
                        "image_id": "vehicle_license-1",
                        "value": "轻型厢式货车",
                    }
                ],
            )
        ],
        external_checks=[],
        business_checks=list(checks),
        completeness=None,
        limitations=[],
    )

    assert "BUSINESS-VEHICLE-TYPE-LIGHT-DUTY" not in {item.step_id for item in steps}
    step = next(item for item in steps if item.step_id == "FIELD-old_vehicle.type")
    assert step.result_status == "INSUFFICIENT"
    assert step.requires_reviewer_action is True
    assert "多个来源字段一致" in step.reason
    assert "轻型" in step.reason
    # 字段行原本就列了材料值，规则不再重复堆一份候选值。
    assert len(step.values) == 1
    assert f"BUSINESS-{VEHICLE_TYPE_LIGHT_DUTY_CHECK_ID}" in step.check_ids


@pytest.mark.asyncio
async def test_light_duty_reaches_the_workbench_through_the_graph(monkeypatch) -> None:
    """端到端：能力真的在主图里跑过，字段卡片因此从「一致」变「待复核」。

    三份材料里只有回收证明写了轻型——归一化后同为载货汽车，字段比对判一致，
    这条盲区只能由本规则补上。
    """
    service = ReviewService(registry=business_registry())

    async def extract(*args):
        return RecognitionBatchResult(
            observations=[
                observation("重型厢式货车", "vehicle_license", 1),
                observation("重型厢式货车", "registration_certificate", 2),
                observation("轻型厢式货车", "scrap_certificate", 3),
            ]
        )

    monkeypatch.setattr(service, "_extract_documents", extract)
    result = await service.assist_async(
        ReviewRequest(
            page_url="https://example.test/scrap-replace-qingdao/review/1",
            region="qingdao",
            page_fields=PAGE,
        )
    )

    assert VEHICLE_TYPE_LIGHT_DUTY_CHECK_ID in {item.check_id for item in result.cross_checks}
    step = next(item for item in result.review_tasks if item.step_id == "FIELD-old_vehicle.type")
    assert step.result_status == "INSUFFICIENT"
    assert "轻型" in step.reason
    assert result.recommendation == "REVIEW_REQUIRED"
