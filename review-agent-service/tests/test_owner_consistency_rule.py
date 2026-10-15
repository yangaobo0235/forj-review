"""新旧车所有人一致性：先比页面原值，再比对材料。"""

from app.businesses.profiles import SCRAP_REPLACEMENT_QINGDAO
from app.businesses.rules.owner_consistency import (
    OWNER_CONSISTENCY_CHECK_ID,
    build_owner_consistency_check,
)
from app.capabilities.specs import ReviewExecutionContext
from app.models.review import FieldObservation, ReviewRequest
from app.presentation.routing import build_review_tasks
from app.workflow.models import AgentBatchResult

OLD = "old_vehicle"
NEW = "new_vehicle"
COMPANY = "怀远县鑫盛运输有限公司"

PAGE = {"old_vehicle.owner": COMPANY, "new_vehicle.owner": COMPANY}


def observation(field: str, value: object, document_type: str, scope: str, index: int = 1):
    return FieldObservation(
        field=field,
        source_type="image",
        source_id=f"{document_type}-{index}",
        image_id=f"{document_type}-{index}",
        image_index=index,
        value=value,
        document_type=document_type,
        business_scope=scope,
    )


def all_materials(value: object = COMPANY) -> list[FieldObservation]:
    """业务只认这四份材料：旧车行驶证/回收证明，新车行驶证/发票。"""
    return [
        observation("old_vehicle.owner", value, "vehicle_license", OLD),
        observation("old_vehicle.owner", value, "scrap_certificate", OLD, index=2),
        observation("new_vehicle.owner", value, "vehicle_license", NEW, index=3),
        observation("new_vehicle.owner", value, "invoice", NEW, index=4),
    ]


def run(page_fields: dict | None = None, observations: list | None = None):
    checks = build_owner_consistency_check(
        ReviewExecutionContext(
            request=ReviewRequest(
                page_url="https://example.test/scrap-replace-qingdao/review/1",
                region="qingdao",
                page_fields=PAGE if page_fields is None else page_fields,
            ),
            profile=SCRAP_REPLACEMENT_QINGDAO,
            batch=AgentBatchResult(),
            observations=tuple(all_materials() if observations is None else observations),
        )
    ).checks
    assert len(checks) == 1
    return checks[0]


def test_matching_page_values_and_materials_pass() -> None:
    check = run()

    assert check.check_id == OWNER_CONSISTENCY_CHECK_ID
    assert check.label == "车辆所有人一致性"
    assert check.status == "MATCH"
    assert COMPANY in check.reason
    assert len(check.values) == 4


def test_page_values_are_carried_separately_from_the_materials() -> None:
    """页面侧取值随检查一起下发，任务装配把它摆到「页面原始值」区块。"""
    check = run()

    assert check.details["page_values"] == [
        {"source": "报废车辆所有人（页面）", "value": COMPANY},
        {"source": "新车所有人（页面）", "value": COMPANY},
    ]


def test_evidence_labels_say_which_side_each_material_belongs_to() -> None:
    """同一份行驶证在两侧都出现，只写「行驶证」审核员分不清是哪一边的。"""
    check = run()

    assert [value.source for value in check.values] == [
        "旧车行驶证",
        "旧车回收证明",
        "新车行驶证",
        "新车销售发票",
    ]
    assert {value.image_id for value in check.values} == {
        "vehicle_license-1",
        "scrap_certificate-2",
        "vehicle_license-3",
        "invoice-4",
    }


def test_different_page_owners_conflict_before_any_material_is_read() -> None:
    """页面填了两个不同的人就是冲突，材料再一致也改变不了这个事实。"""
    check = run(page_fields={"old_vehicle.owner": COMPANY, "new_vehicle.owner": "张三"})

    assert check.status == "CONFLICT"
    assert COMPANY in check.reason and "张三" in check.reason


def test_material_that_disagrees_with_the_page_conflicts() -> None:
    """材料对不上用标红表达，不再把两边的值念一遍。"""
    observations = all_materials()
    observations[3] = observation("new_vehicle.owner", "乙物流有限公司", "invoice", NEW, index=4)
    check = run(observations=observations)

    assert check.status == "CONFLICT"
    assert "新车销售发票" in check.reason
    assert "乙物流有限公司" not in check.reason
    assert check.details["reason_distributed"] is True
    assert "page_value_note" not in check.details

    differing = next(value for value in check.values if value.value == "乙物流有限公司")
    assert differing.conflicting is True
    assert differing.differences
    # 逐字差异落在真正不同的那几个字上，值本身保持原文。
    assert differing.value == "乙物流有限公司"
    assert not any(value.conflicting for value in check.values if value.value == COMPANY)


def test_page_mismatch_is_stated_under_the_page_values_block() -> None:
    """两个页面值不同时，在「页面原始值」下面留一句话，理由整段收起来。"""
    check = run(page_fields={"old_vehicle.owner": COMPANY, "new_vehicle.owner": "张三"})

    assert check.details["page_value_note"] == "新旧车页面所有人不一致"
    assert check.details["reason_distributed"] is True


def test_a_close_match_is_highlighted_character_by_character() -> None:
    """差一个字也是冲突：整串标红会让审核员看不出差在哪。"""
    observations = all_materials()
    observations[0] = observation("old_vehicle.owner", "南城珺顺物流有限公司", "vehicle_license", OLD)
    check = run(page_fields={"old_vehicle.owner": "南城瑞顺物流有限公司", "new_vehicle.owner": "南城瑞顺物流有限公司"}, observations=observations)

    differing = check.values[0]
    assert differing.conflicting is True
    assert [item.page_text for item in differing.differences] == ["瑞"]
    assert {item.start for item in differing.differences} == {2}


def test_insufficient_evidence_keeps_its_reason_visible() -> None:
    """证据不足时界面上没有别的东西能解释，整段理由必须留着。"""
    missing_material = run(observations=all_materials()[:2])
    assert missing_material.status == "INSUFFICIENT"
    assert "reason_distributed" not in missing_material.details
    assert missing_material.reason

    missing_page = run(page_fields={"old_vehicle.owner": COMPANY})
    assert missing_page.status == "INSUFFICIENT"
    assert "reason_distributed" not in missing_page.details


def test_missing_page_value_asks_for_human_review() -> None:
    assert run(page_fields={"old_vehicle.owner": COMPANY}).status == "INSUFFICIENT"
    assert run(page_fields={"new_vehicle.owner": COMPANY}).status == "INSUFFICIENT"
    assert run(page_fields={}).status == "INSUFFICIENT"


def test_missing_material_on_one_side_asks_for_human_review() -> None:
    """页面一致但一侧没有材料可佐证时不能判通过。"""
    only_old = all_materials()[:2]
    check = run(observations=only_old)

    assert check.status == "INSUFFICIENT"
    assert "新车" in check.reason


def test_one_material_submitted_several_times_yields_one_value() -> None:
    """重复上传、正反面分开上传都算同一份材料的多次读取。"""
    observations = [
        observation("old_vehicle.owner", COMPANY, "vehicle_license", OLD, index=1),
        observation("old_vehicle.owner", COMPANY, "vehicle_license", OLD, index=2),
        *all_materials()[1:],
    ]
    check = run(observations=observations)

    assert check.status == "MATCH"
    assert len(check.values) == 4


def test_unreadable_material_values_do_not_invent_a_conflict() -> None:
    """「无法识别」不是一个人名，不能被当作与页面不符。"""
    observations = all_materials()
    observations[1] = observation("old_vehicle.owner", "无法识别", "scrap_certificate", OLD, index=2)
    check = run(observations=observations)

    assert check.status == "MATCH"
    assert [value.value for value in check.values] == [COMPANY, COMPANY, COMPANY]


def test_owner_consistency_task_has_page_values_and_no_write_target() -> None:
    """这条结论不是页面控件：只给「查看原图」，不给回填。"""
    steps = build_review_tasks(
        request=ReviewRequest(
            page_url="https://example.test/scrap-replace-qingdao/review/1",
            region="qingdao",
            page_fields=PAGE,
        ),
        profile=SCRAP_REPLACEMENT_QINGDAO,
        comparisons=[],
        external_checks=[],
        business_checks=[run()],
        completeness=None,
        limitations=[],
    )

    step = next(item for item in steps if item.step_id == f"BUSINESS-{OWNER_CONSISTENCY_CHECK_ID}")
    assert step.category == "BUSINESS_RULE"
    assert step.page_target_field is None
    assert step.writable is False
    assert [value.source for value in step.page_values] == [
        "报废车辆所有人（页面）",
        "新车所有人（页面）",
    ]
    assert len(step.values) == 4
    # page_values 只是装卸用的传递键，不应再留在 details 里重复一份。
    assert "page_values" not in step.details
