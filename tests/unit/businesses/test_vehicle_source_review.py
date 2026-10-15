"""车源审核的材料要求：行驶证必须，登记证书第 1、2 页与车辆铭牌二选一。"""

from datetime import datetime, timedelta, timezone

import pytest

from forj_review_assistant.businesses.completeness import (
    evaluate_collected,
    evaluate_extracted,
)
from forj_review_assistant.businesses.profiles import VEHICLE_SOURCE_DEFAULT
from forj_review_assistant.contracts.checks import CheckResultValue
from forj_review_assistant.contracts.recognition import (
    AgentBatchResult,
    RecognizedDocument,
)
from forj_review_assistant.contracts.review import FieldObservation, ReviewRequest

SCOPE = "vehicle"


def image(index: int, category_hint: str):
    return {
        "index": index,
        "imageId": f"{category_hint}-{index}",
        "src": f"https://example.test/{index}.jpg",
        "businessScope": SCOPE,
        "categoryHint": category_hint,
    }


def request(*category_hints: str) -> ReviewRequest:
    return ReviewRequest(
        page_url="https://admin.forjtruck.com/vehicle-source/approval",
        business_type="vehicle_source",
        region="default",
        images=[image(index, hint) for index, hint in enumerate(category_hints, start=1)],
    )


def document(target_id: str, document_type: str, index: int, covered_pages: list[int] | None = None):
    return RecognizedDocument(
        target_id=target_id,
        image_index=index,
        document_type=document_type,
        business_scope=SCOPE,
        covered_pages=covered_pages or [],
    )


def missing_materials(report) -> set[str]:
    return {
        issue.material_type
        for issue in report.issues
        if issue.code == "MISSING_MATERIAL"
    }


def test_collected_checklist_lists_license_and_the_two_alternatives() -> None:
    report = evaluate_collected(request(), VEHICLE_SOURCE_DEFAULT)

    assert [item.display_name for item in report.checklist] == [
        "机动车行驶证",
        "机动车登记证书第 1、2 页",
        "车辆铭牌",
    ]
    assert missing_materials(report) == {
        "vehicle_license",
        "registration_certificate",
        "vehicle_nameplate",
    }


def test_nameplate_satisfies_the_registration_certificate_slot() -> None:
    """只上传行驶证和铭牌：不再报“缺少登记证书”。"""
    report = evaluate_collected(
        request("vehicle_license", "vehicle_nameplate"),
        VEHICLE_SOURCE_DEFAULT,
    )

    assert missing_materials(report) == set()
    assert report.status == "COMPLETE"
    by_type = {item.material_type: item for item in report.checklist}
    assert by_type["vehicle_license"].status == "PRESENT"
    assert by_type["vehicle_nameplate"].status == "PRESENT"
    assert by_type["registration_certificate"].status == "PRESENT"
    assert by_type["registration_certificate"].reason == "已提供同组替代材料：车辆铭牌"


def test_registration_certificate_satisfies_the_nameplate_slot() -> None:
    report = evaluate_collected(
        request("vehicle_license", "registration_certificate"),
        VEHICLE_SOURCE_DEFAULT,
    )

    assert missing_materials(report) == set()
    by_type = {item.material_type: item for item in report.checklist}
    assert by_type["vehicle_nameplate"].reason == "已提供同组替代材料：机动车登记证书第 1、2 页"


def test_missing_license_is_still_reported_when_an_alternative_is_present() -> None:
    """替代关系只作用于同组材料，行驶证仍然必须上传。"""
    report = evaluate_collected(request("vehicle_nameplate"), VEHICLE_SOURCE_DEFAULT)

    assert missing_materials(report) == {"vehicle_license"}
    assert report.status == "INCOMPLETE"


def test_extracted_requires_both_registration_pages_when_no_alternative() -> None:
    report = evaluate_extracted(
        request("vehicle_license", "registration_certificate"),
        VEHICLE_SOURCE_DEFAULT,
        AgentBatchResult(recognized_documents=[
            document("vehicle_license-1", "vehicle_license", 1),
            document("registration_certificate-2", "registration_certificate", 2, [1]),
        ]),
    )

    codes = {issue.code for issue in report.issues}
    assert "MISSING_REGISTRATION_PAGES" in codes


def test_extracted_accepts_the_nameplate_instead_of_the_registration_pages() -> None:
    """登记证书只给了一页，但铭牌在，视作已经满足该组材料要求。"""
    report = evaluate_extracted(
        request("vehicle_license", "registration_certificate", "vehicle_nameplate"),
        VEHICLE_SOURCE_DEFAULT,
        AgentBatchResult(recognized_documents=[
            document("vehicle_license-1", "vehicle_license", 1),
            document("registration_certificate-2", "registration_certificate", 2, [1]),
            document("vehicle_nameplate-3", "vehicle_nameplate", 3),
        ]),
    )

    codes = {issue.code for issue in report.issues}
    assert "MISSING_REGISTRATION_PAGES" not in codes
    assert missing_materials(report) == set()
    by_type = {item.material_type: item for item in report.checklist}
    assert by_type["registration_certificate"].reason == "已提供同组替代材料：车辆铭牌"


def test_extracted_reports_missing_group_when_neither_alternative_is_recognized() -> None:
    report = evaluate_extracted(
        request("vehicle_license"),
        VEHICLE_SOURCE_DEFAULT,
        AgentBatchResult(recognized_documents=[
            document("vehicle_license-1", "vehicle_license", 1),
        ]),
    )

    assert missing_materials(report) == {"registration_certificate", "vehicle_nameplate"}


def test_vehicle_source_material_policy_declares_the_alternative_group() -> None:
    policy = VEHICLE_SOURCE_DEFAULT.material_policy

    assert policy is not None
    groups = {
        requirement.document_type: requirement.alternative_group
        for requirement in policy.materials
    }
    assert groups == {
        "vehicle_license": "",
        "registration_certificate": "vehicle_supplement",
        "vehicle_nameplate": "vehicle_supplement",
    }


def _inventory_step_id(
    field: str,
    label: str,
    order: int,
    *,
    editable: bool = True,
    control_type: str = "text",
):
    from forj_review_assistant.contracts.review import ReviewFieldSnapshot

    return ReviewFieldSnapshot(
        field=field,
        label=label,
        order=order,
        value="",
        editable=editable,
        control_type=control_type,
    )


# 真实车源审核页的控件目录：13 个核对字段 + 页面上的其它控件。
PAGE_CONTROLS = (
    ("vehicle.type", "车辆类型"),
    ("vehicle.usage_nature", "车辆用途"),
    ("other.attachment", "是否带挂"),
    ("other.price", "售价"),
    ("other.floor_price", "底价"),
    ("other.factory", "主机厂"),
    ("other.publisher", "发布人"),
    ("vehicle.model", "车型"),
    ("vehicle.plate_no", "车牌号码"),
    ("vehicle.vin", "车架号"),
)


def test_review_inventory_keeps_only_the_declared_fields() -> None:
    """车源审核只核对声明的字段：售价、底价等控件不进审核目录。"""
    from forj_review_assistant.presentation.routing import build_review_tasks

    tasks = build_review_tasks(
        request=request().model_copy(update={
            "review_fields": [
                _inventory_step_id(field, label, order)
                for order, (field, label) in enumerate(PAGE_CONTROLS, start=1)
            ],
            "page_fields": {"vehicle.type": "载货货车"},
        }),
        profile=VEHICLE_SOURCE_DEFAULT,
        comparisons=[],
        external_checks=[],
        business_checks=[],
        completeness=None,
        limitations=[],
    )

    fields = [item.page_target_field for item in tasks if item.category == "FIELD"]
    assert fields == [
        "vehicle.type",
        "vehicle.usage_nature",
        "vehicle.model",
        "vehicle.plate_no",
        "vehicle.vin",
    ]
    # 没有注册页面动作的业务不提供回填按钮。
    assert all(item.writable is False for item in tasks)
    assert not any("售价" in item.label or "底价" in item.label for item in tasks)


def test_model_field_shows_the_rule_conclusions_as_one_field_entry() -> None:
    """「车型」的核验条目由三条规则结论投影而来，按最坏状态合并。"""
    from forj_review_assistant.contracts.checks import CheckResult
    from forj_review_assistant.presentation.routing import build_review_tasks

    checks = [
        CheckResult(check_id="VEHICLE-MODEL-POWER", label="车型马力", status="MATCH", reason="马力一致"),
        CheckResult(check_id="VEHICLE-MODEL-CODE", label="车型整车型号", status="CONFLICT", reason="整车型号不一致"),
        CheckResult(check_id="VEHICLE-MODEL-EMISSION", label="车型排放标准", status="INSUFFICIENT", reason="材料未提供排放标准"),
    ]
    tasks = build_review_tasks(
        request=request().model_copy(update={
            "review_fields": [_inventory_step_id("vehicle.model", "车型", 1)],
            "page_fields": {"vehicle.model": "一汽解放 J6L 200马力(CA5180CCYP62K1L4E5)(国五)"},
        }),
        profile=VEHICLE_SOURCE_DEFAULT,
        comparisons=[],
        external_checks=[],
        business_checks=checks,
        completeness=None,
        limitations=[],
    )

    model = next(item for item in tasks if item.step_id == "FIELD-vehicle.model")
    assert model.result_status == "CONFLICT"
    assert model.label == "车型"
    assert "马力一致" in model.reason and "整车型号不一致" in model.reason
    assert model.details["check_ids"] == [
        "VEHICLE-MODEL-POWER",
        "VEHICLE-MODEL-CODE",
        "VEHICLE-MODEL-EMISSION",
    ]
    # 理由已经逐条搬进候选框，工作台不再重复整段（这个检查只带结论、不带值）。
    assert model.details["reason_distributed"] is True


def test_projected_conclusions_keep_their_task_when_no_field_row_was_built() -> None:
    """字段控件不在页面上时投影出不来条目，独立任务必须保留。

    抑制的唯一依据是“字段条目真的发出来了”，不是“声明里写过绑定”。
    """
    from forj_review_assistant.contracts.checks import CheckResult
    from forj_review_assistant.presentation.routing import build_review_tasks

    checks = [
        CheckResult(
            check_id="VEHICLE-MODEL-POWER",
            label="车型马力",
            status="CONFLICT",
            reason="页面车型马力 430 与材料推导值 473 不一致",
            values=[CheckResultValue(source="车型马力", value="473 马力", conflicting=True)],
        )
    ]
    tasks = build_review_tasks(
        request=request().model_copy(update={
            # 页面上没有「车型」控件，投影出不来字段条目。
            "review_fields": [_inventory_step_id("vehicle.vin", "VIN", 1)],
            "page_fields": {"vehicle.model": "一汽解放 430马力(CA4250P1K15T1E6A80)(国六)"},
        }),
        profile=VEHICLE_SOURCE_DEFAULT,
        comparisons=[],
        external_checks=[],
        business_checks=checks,
        completeness=None,
        limitations=[],
    )

    assert [item.step_id for item in tasks if item.category == "FIELD"] == ["FIELD-vehicle.vin"]
    power = next(item for item in tasks if item.step_id == "BUSINESS-VEHICLE-MODEL-POWER")
    assert power.reason == "页面车型马力 430 与材料推导值 473 不一致"


def test_projected_reason_stays_visible_when_a_check_did_not_write_it() -> None:
    """理由里多出检查没写过的一句时不能只靠候选框：那一句没有别的地方可看。"""
    from forj_review_assistant.contracts.checks import CheckResult
    from forj_review_assistant.contracts.review import CollectionDiagnostics
    from forj_review_assistant.presentation.routing import build_review_tasks

    checks = [
        CheckResult(
            check_id="VEHICLE-MODEL-POWER",
            label="车型马力",
            status="MATCH",
            reason="页面车型马力 220 与材料推导值 220 一致",
            values=[CheckResultValue(source="车型马力", value="220 马力", conflicting=False)],
        )
    ]
    tasks = build_review_tasks(
        request=request().model_copy(update={
            "review_fields": [_inventory_step_id("vehicle.model", "车型", 1)],
            "page_fields": {"vehicle.model": "一汽解放 J6L 200马力(CA5180CCYP62K1L4E5)(国五)"},
            # 页面控件存在歧义时字段不能作为展示目标，理由里会补一句说明。
            "collection_diagnostics": CollectionDiagnostics(
                ambiguous_fields=["vehicle.model"],
                unmatched_labels=[],
            ),
        }),
        profile=VEHICLE_SOURCE_DEFAULT,
        comparisons=[],
        external_checks=[],
        business_checks=checks,
        completeness=None,
        limitations=[],
    )

    model = next(item for item in tasks if item.step_id == "FIELD-vehicle.model")
    assert model.result_status == "INSUFFICIENT"
    assert model.reason.endswith("页面字段缺失或存在歧义，请人工核对该字段")
    assert model.details["reason_distributed"] is False
    # 候选值上的逐条说明照旧保留，两处不冲突。
    assert model.values[0].check_reason == "页面车型马力 220 与材料推导值 220 一致"


@pytest.mark.parametrize(
    ("control_type", "editable", "writable"),
    [
        ("text", True, True),
        ("textarea", True, True),
        ("number", True, True),
        # 下拉和日期控件写回器还没验证过，一律交人工填写。
        ("select", True, False),
        ("date", True, False),
        ("text", False, False),
    ],
)
def test_vehicle_source_offers_writeback_only_for_text_controls(
    control_type: str,
    editable: bool,
    writable: bool,
) -> None:
    """回填按钮只在业务声明过的控件类型上出现。

    声明为可写的字段渲染成下拉或日期控件时同样不给按钮——审核员点出一个
    写不进去（或写进去回读不准）的操作比没有按钮更糟。
    """
    from forj_review_assistant.contracts.review import FieldComparison, FieldStatus
    from forj_review_assistant.presentation.routing import build_review_tasks

    tasks = build_review_tasks(
        request=request().model_copy(update={
            "review_fields": [
                _inventory_step_id(
                    "vehicle.owner", "所有人", 1,
                    editable=editable, control_type=control_type,
                )
            ],
            "page_fields": {"vehicle.owner": "某某物流有限公司"},
        }),
        profile=VEHICLE_SOURCE_DEFAULT,
        comparisons=[
            FieldComparison(
                field="vehicle.owner",
                left_value="某某物流有限公司",
                right_value="某某物流有限公司",
                status=FieldStatus.MATCH,
                message="多个来源字段一致",
            )
        ],
        external_checks=[],
        business_checks=[],
        completeness=None,
        limitations=[],
    )

    owner = next(item for item in tasks if item.step_id == "FIELD-vehicle.owner")
    assert owner.control_type == control_type
    assert owner.writable is writable


def test_vehicle_source_declares_writeback_for_its_text_fields() -> None:
    """允许写回的字段来自扩展包声明，不在代码里另维护一份白名单。"""
    from forj_review_assistant.businesses.packs.vehicle_source import (
        VEHICLE_SOURCE_PACK,
    )

    assert VEHICLE_SOURCE_PACK.writable_field_keys() == (
        "vehicle.type",
        "vehicle.plate_no",
        "vehicle.vin",
        "vehicle.engine_no",
        "vehicle.license_vehicle_type",
        "vehicle.brand_model",
        "vehicle.usage_nature",
        "vehicle.registration_date",
        "vehicle.issue_date",
        "vehicle.owner",
        "vehicle.model",
        "vehicle.fuel_type",
        "vehicle.engine_model",
    )
    # 只放开文本框；下拉与日期控件在写回器验证之前不放开。
    assert VEHICLE_SOURCE_PACK.writable_control_kinds == ("text", "textarea", "number")
    assert VEHICLE_SOURCE_DEFAULT.page_action_ids == ("fill_review_fields",)


@pytest.mark.parametrize(
    ("days_ago", "expected"),
    [
        # 窗口是「审核日往前推 90 天」到今天，含两端。
        (0, "MATCH"),
        (90, "MATCH"),
        (91, "INSUFFICIENT"),
        (-1, "INSUFFICIENT"),
    ],
)
def test_issue_date_must_be_within_the_last_ninety_days(days_ago: int, expected: str) -> None:
    """发证日期超出时效交人工复核，不自动判不通过。"""
    from dataclasses import replace
    from datetime import datetime, timedelta, timezone

    from forj_review_assistant.presentation.response import _build_comparisons
    from forj_review_assistant.presentation.routing import build_review_tasks

    issued = (datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=days_ago)).isoformat()
    tasks = build_review_tasks(
        request=request().model_copy(update={
            "review_fields": [_inventory_step_id("vehicle.issue_date", "发证日期", 1)],
            "page_fields": {"vehicle.issue_date": issued},
        }),
        profile=VEHICLE_SOURCE_DEFAULT,
        comparisons=_build_comparisons(
            request().model_copy(update={"page_fields": {"vehicle.issue_date": issued}}),
            replace(VEHICLE_SOURCE_DEFAULT, required_fields=("vehicle.issue_date",)),
            [FieldObservation(field="vehicle.issue_date", source_type="image", source_id="license",
                              document_type="vehicle_license", business_scope="vehicle", value=issued),
             FieldObservation(field="vehicle.issue_date", source_type="page", source_id="page", value=issued)],
        ),
        external_checks=[],
        business_checks=[],
        completeness=None,
        limitations=[],
    )

    step = next(item for item in tasks if item.step_id == "FIELD-vehicle.issue_date")
    assert step.result_status == expected
    if expected != "MATCH":
        assert "90 天" in step.reason
        assert "人工复核" in step.reason


def test_registration_date_is_not_subject_to_the_same_window() -> None:
    """时效只挂在发证日期上；注册日期再久远也不因此降级。"""
    from forj_review_assistant.businesses.field_policies import field_policy

    assert field_policy("vehicle.issue_date").max_age_days == 90
    assert field_policy("vehicle.registration_date").max_age_days is None


def test_vehicle_source_uses_vehicle_scope_and_page_interaction() -> None:
    assert VEHICLE_SOURCE_DEFAULT.page_interaction is True
    assert VEHICLE_SOURCE_DEFAULT.material_policy.mode == "enforce"
    assert {section.id for section in VEHICLE_SOURCE_DEFAULT.sections} == {SCOPE}
    assert all(
        field.startswith("vehicle.") for field in VEHICLE_SOURCE_DEFAULT.required_fields
    )


def test_vehicle_source_field_evidence_policies_stay_off_scrap_keys() -> None:
    """车源的证据策略只覆盖自己的字段，不改变报废置换的声明。"""
    from forj_review_assistant.businesses.field_policies import field_policy

    assert field_policy("old_vehicle.vin").allowed_document_types == (
        "vehicle_license",
        "registration_certificate",
    )
    vin = field_policy("vehicle.vin")
    assert vin is not None
    assert [(rule.source, rule.document_types, rule.match) for rule in vin.authority] == [
        ("image", ("vehicle_license",), "full"),
        ("page", (), "full"),
        ("image", ("registration_certificate", "vehicle_nameplate"), "suffix8"),
    ]


def test_engine_model_benchmarks_on_registration_or_nameplate_only() -> None:
    """行驶证只有“发动机号码”，没有“发动机型号”，不能当基准。

    把它当基准会让每一次审核都卡在“未取得行驶证值”，而材料里其实有型号。
    """
    from forj_review_assistant.businesses.field_policies import field_policy

    policy = field_policy("vehicle.engine_model")
    assert policy.allowed_document_types == ("registration_certificate", "vehicle_nameplate")
    assert [(rule.source, rule.document_types, rule.required) for rule in policy.authority] == [
        ("image", ("registration_certificate", "vehicle_nameplate"), True),
        ("page", (), False),
    ]


def test_fuel_type_accepts_the_registration_certificate() -> None:
    """登记证书第 13 项就是燃料种类，不能只认行驶证。"""
    from forj_review_assistant.businesses.field_policies import field_policy

    policy = field_policy("vehicle.fuel_type")
    assert policy.allowed_document_types == ("vehicle_license", "registration_certificate")


async def _run_full_review(page_fields: dict, material_fields: dict, monkeypatch):
    """按生产路径跑一遍完整主图：材料字段先过路由，再进比较和规则。"""
    from forj_review_assistant.businesses.routing import route_fields
    from forj_review_assistant.runtime.review_service import ReviewService

    observations = []
    documents = []
    for index, (document_type, raw_fields) in enumerate(material_fields.items(), start=1):
        routed, limitation = route_fields(SCOPE, document_type, raw_fields)
        assert limitation is None
        target_id = f"{document_type}-{index}"
        documents.append(
            RecognizedDocument(
                target_id=target_id,
                image_index=index,
                document_type=document_type,
                business_scope=SCOPE,
                covered_pages=[1, 2] if document_type == "registration_certificate" else [],
            )
        )
        observations.extend(
            FieldObservation(
                field=field,
                source_type="image",
                source_id=target_id,
                image_id=target_id,
                image_index=index,
                value=value,
                document_type=document_type,
                business_scope=SCOPE,
            )
            for field, value in routed.items()
        )
    batch = AgentBatchResult(observations=observations, recognized_documents=documents)
    service = ReviewService()

    async def extract(request, callback, *args):
        await callback(batch)
        return batch

    monkeypatch.setattr(service, "_extract_documents", extract)
    request = ReviewRequest(
        page_url="https://admin.forjtruck.com/vehicle-source/approval",
        business_type="vehicle_source",
        region="default",
        page_fields=page_fields,
    )
    return await service.assist_async(request, lambda response, _: None)


PAGE_FIELDS = {
    "vehicle.type": "载货货车",
    "vehicle.plate_no": "鲁B12345",
    "vehicle.vin": "LFNAHUKP1H1E12345",
    "vehicle.engine_no": "12345678",
    "vehicle.license_vehicle_type": "重型仓栅式货车",
    "vehicle.brand_model": "解放牌CA5180CCYP62K1L4E5",
    "vehicle.usage_nature": "货运",
    "vehicle.registration_date": "2020-05-01",
    "vehicle.issue_date": datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
    "vehicle.owner": "某某物流有限公司",
    "vehicle.model": "一汽解放 J6L 中卡 220马力 4X2 6.75米仓栅式载货车(CA5180CCYP62K1L4E5)(国五)",
    "vehicle.fuel_type": "柴油",
    "vehicle.engine_model": "CA4DK1-22E5",
}

# 行驶证 + 铭牌（登记证书与铭牌二选一，这里走铭牌分支）。
# 行驶证上只有“发动机号码”，没有“发动机型号”和独立的“车辆型号”，
# 所以这两项只从登记证书或车辆铭牌取得。
MATERIAL_FIELDS = {
    "vehicle_license": {
        "vehicle.plate_no": "鲁B12345",
        "vehicle.vin": "LFNAHUKP1H1E12345",
        "vehicle.engine_no": "12345678",
        "vehicle.type": "重型仓栅式货车",
        "vehicle.brand_model": "解放牌CA5180CCYP62K1L4E5",
        "vehicle.usage_nature": "货运",
        "vehicle.registration_date": "2020年5月1日",
        "vehicle.issue_date": PAGE_FIELDS["vehicle.issue_date"],
        "vehicle.owner": "某某物流有限公司",
        "vehicle.fuel_type": "柴油",
    },
    "vehicle_nameplate": {
        "vehicle.vin": "LFNAHUKP1H1E12345",
        "vehicle.engine_model": "CA4DK1-22E5",
        "vehicle.model_code": "CA5180CCYP62K1L4E5",
        "vehicle.brand_model": "解放牌CA5180CCYP62K1L4E5",
        "vehicle.power_kw": "162",
        "vehicle.emission_standard": "国五",
    },
}


@pytest.mark.asyncio
async def test_full_review_passes_when_page_and_materials_agree(monkeypatch) -> None:
    response = await _run_full_review(PAGE_FIELDS, MATERIAL_FIELDS, monkeypatch)

    comparisons = {item.field: item.status.value for item in response.comparisons}
    assert comparisons == {
        "vehicle.type": "MATCH",
        "vehicle.plate_no": "MATCH",
        "vehicle.vin": "MATCH",
        "vehicle.engine_no": "MATCH",
        "vehicle.license_vehicle_type": "MATCH",
        "vehicle.brand_model": "MATCH",
        "vehicle.usage_nature": "MATCH",
        "vehicle.registration_date": "MATCH",
        "vehicle.issue_date": "MATCH",
        "vehicle.owner": "MATCH",
        "vehicle.fuel_type": "MATCH",
        "vehicle.engine_model": "MATCH",
    }
    checks = {item.check_id: item.status for item in response.cross_checks}
    assert checks["VEHICLE-MODEL-POWER"] == "MATCH"
    assert checks["VEHICLE-MODEL-CODE"] == "MATCH"
    assert checks["VEHICLE-MODEL-EMISSION"] == "MATCH"
    # 车型不参与常规字段比对，它的字段条目由三条规则结论投影而来。
    model = next(item for item in response.review_tasks if item.step_id == "FIELD-vehicle.model")
    assert model.result_status == "MATCH"
    # 页面侧和材料侧分开摆，两侧用同一条检查的标签命名，审核员能一一对应。
    assert [(value.source, value.value) for value in model.page_values] == [
        ("车型原文", PAGE_FIELDS["vehicle.model"]),
        ("车型马力", "220 马力"),
        ("车型整车型号", "CA5180CCYP62K1L4E5"),
        ("车型排放标准", "国五"),
    ]
    assert [(value.source, value.value) for value in model.values] == [
        ("车型马力", "220 马力（由发动机型号、发动机功率推导）"),
        ("车型整车型号", "CA5180CCYP62K1L4E5"),
        # 铭牌上直接印了国五，不再标成“由发动机型号的排放后缀推导”。
        ("车型排放标准", "国五"),
    ]
    # 每条结论自带比对结果和说明，审核员不必回头读字段底部那段理由。
    assert [(value.conflicting, value.check_reason) for value in model.values] == [
        (False, "页面车型马力 220 与材料推导值 220 一致"),
        (False, "页面整车型号 CA5180CCYP62K1L4E5 与材料 CA5180CCYP62K1L4E5 一致"),
        (False, "页面排放标准 国五 与材料 国五 一致"),
    ]
    assert [item.id for item in response.sections] == [SCOPE]
    # 三条结论已经投影进「车型」字段行，不再各自出一张卡片：否则审核员在
    # 「待处理」里会把同一条结论看两遍。
    assert [item.step_id for item in response.review_tasks if item.category == "BUSINESS_RULE"] == []


@pytest.mark.asyncio
async def test_engine_model_brand_prefix_is_not_a_conflict(monkeypatch) -> None:
    """页面写「潍柴WP10.5H430E62」、材料只读到型号本身时不算冲突。

    品牌和型号是两件事；比对只看型号，高亮也不能标出「缺少 潍柴」。
    """
    page = {**PAGE_FIELDS, "vehicle.engine_model": "潍柴WP10.5H430E62"}
    materials = {
        **MATERIAL_FIELDS,
        "vehicle_nameplate": {
            **MATERIAL_FIELDS["vehicle_nameplate"],
            "vehicle.engine_model": "WP10.5H430E62",
        },
    }

    response = await _run_full_review(page, materials, monkeypatch)

    comparisons = {item.field: item for item in response.comparisons}
    assert comparisons["vehicle.engine_model"].status.value == "MATCH"
    step = next(
        item for item in response.review_tasks if item.step_id == "FIELD-vehicle.engine_model"
    )
    material = [value for value in step.values if value.source != "申请页面字段"]
    assert [value.value for value in material] == ["WP10.5H430E62"]
    assert material[0].differences == []

    # 型号本身不同时仍然要标出来，只是标记里不再出现品牌。
    disagreeing = {
        **materials,
        "vehicle_nameplate": {
            **materials["vehicle_nameplate"],
            "vehicle.engine_model": "WP13NG480E61",
        },
    }
    other = await _run_full_review(page, disagreeing, monkeypatch)
    marked = next(
        value.differences
        for value in next(
            item for item in other.review_tasks if item.step_id == "FIELD-vehicle.engine_model"
        ).values
        if value.source != "申请页面字段"
    )
    assert marked
    assert all("潍柴" not in item.page_text for item in marked)


@pytest.mark.asyncio
async def test_full_review_flags_a_material_that_disagrees_with_the_license(monkeypatch) -> None:
    materials = {
        **MATERIAL_FIELDS,
        "vehicle_nameplate": {
            **MATERIAL_FIELDS["vehicle_nameplate"],
            "vehicle.model_code": "CA5180CCYP62K1L4E9",
            "vehicle.emission_standard": "国四",
        },
    }
    response = await _run_full_review(PAGE_FIELDS, materials, monkeypatch)

    checks = {item.check_id: item.status for item in response.cross_checks}
    assert checks["VEHICLE-MODEL-CODE"] == "CONFLICT"
    assert checks["VEHICLE-MODEL-EMISSION"] == "CONFLICT"
    assert checks["VEHICLE-MODEL-POWER"] == "MATCH"


def test_uncertain_vehicle_source_field_still_reaches_human_review() -> None:
    """一票否决依赖按分区反查声明；反查失败会让车源的 uncertain 静默失效。"""
    from forj_review_assistant.evidence.comparison.evidence_values import (
        batch_observations,
    )

    observation = FieldObservation(
        field="vehicle.vin",
        source_type="image",
        source_id="vehicle_license-1",
        image_id="vehicle_license-1",
        image_index=1,
        value="LFNAHUKP1H1E12345",
        document_type="vehicle_license",
        business_scope=SCOPE,
    )
    batch = AgentBatchResult(
        observations=[observation],
        recognized_documents=[
            RecognizedDocument(
                target_id="vehicle_license-1",
                image_index=1,
                document_type="vehicle_license",
                business_scope=SCOPE,
                uncertain_fields=["vehicle.vin"],
            ),
        ],
    )

    assert batch_observations(batch)[0].uncertain is True
